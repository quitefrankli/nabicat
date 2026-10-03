from __future__ import annotations

import json
import logging
import os
import random
import shutil
import string
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, TextIO, TypeVar

import boto3
from botocore.exceptions import ClientError
from git import Repo
from pydantic import BaseModel

from web_app.config import ConfigManager
from web_app.logging_utils import log_event
from web_app.users import User, UsersFile

Model = TypeVar("Model", bound=BaseModel)


class _S3Client:
    def __init__(self) -> None:
        ACCESS_KEY = os.environ["AWS_ACCESS_KEY_ID"]
        SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
        self.s3_client = boto3.client('s3', 
                                      aws_access_key_id=ACCESS_KEY, 
                                      aws_secret_access_key=SECRET_ACCESS_KEY)

    @staticmethod
    def _get_s3_path(file: Path) -> str:
        return str(file.relative_to(ConfigManager().save_data_path).as_posix())

    def download_file(self, file: Path) -> None:
        log_event(
            "storage", "storage.s3_download_started",
            source=self._get_s3_path(file), destination=str(file),
        )
        if not file.parent.exists():
            file.parent.mkdir(exist_ok=True, parents=True)
        try:
            bucket = ConfigManager().data_sync_bucket_name
            self.s3_client.download_file(bucket, self._get_s3_path(file), str(file))
        except ClientError as e:
            if e.response['Error']['Code'] == "404":
                log_event(
                    "storage", "storage.s3_file_missing",
                    level=logging.WARNING, path=str(file),
                )
            else:
                raise

    def upload_file(self, file: Path) -> None:
        log_event(
            "storage", "storage.s3_upload_started",
            source=str(file), destination=self._get_s3_path(file),
        )
        bucket = ConfigManager().data_sync_bucket_name
        self.s3_client.upload_file(str(file), bucket, self._get_s3_path(file))

class _OfflineClient:
    def download_file(self, file: Path) -> None:
        pass

    def upload_file(self, file: Path) -> None:
        pass

class DataSyncer:
    _instance: DataSyncer | None = None

    @classmethod
    def instance(cls) -> 'DataSyncer':
        if cls._instance is None:
            config = ConfigManager()
            if config.use_offline_syncer:
                cls._instance = DataSyncer(_OfflineClient())
            else:
                cls._instance = DataSyncer(_S3Client())

        return cls._instance
    
    def __init__(self, client: _S3Client | _OfflineClient) -> None:
        self.client = client

    def download_file(self, file: Path) -> None:
        self.client.download_file(file)

    def upload_file(self, file: Path) -> None:
        self.client.upload_file(file)


class DataInterface:
    def __init__(self) -> None:
        from web_app.redis_client import rmw_lock

        config = ConfigManager()
        self.data_root = config.save_data_path.parent
        self.data_syncer = DataSyncer.instance()
        self._lock_factory = rmw_lock
        self.backups_directory = config.save_data_path.parent / "backups"
        self.users_file = config.save_data_path / "users.json"
        self.metadata_filename = "metadata.json"

    def app_path(self, *parts: str | Path) -> Path:
        return self._safe_path(self.data_root, parts)

    def user_path(self, user: User, *parts: str | Path) -> Path:
        if not user.folder or "/" in user.folder or "\\" in user.folder or user.folder in {".", ".."}:
            raise ValueError("user folder must be a safe path component")
        return self._safe_path(self.data_root / user.folder, parts)

    def load_model(self, path: Path, model: type[Model], *, sync: bool = True) -> Model | None:
        self._assert_path(path)
        if sync:
            self.data_syncer.download_file(path)
        if not path.exists():
            return None
        return model.model_validate_json(path.read_text(encoding="utf-8"))

    @contextmanager
    def edit_model(self, path: Path, model: type[Model], *, exclude_none: bool = False) -> Iterator[Model]:
        self._assert_path(path)
        lock_name = f"model:{path.relative_to(self.data_root)}"
        with self._lock_factory(lock_name):
            current = self.load_model(path, model, sync=False) or model()
            before = current.model_dump_json(exclude_none=exclude_none)
            yield current
            if current.model_dump_json(exclude_none=exclude_none) != before:
                self._save_model(path, current, exclude_none=exclude_none)

    def atomic_write(
        self,
        path: Path,
        data: str | bytes | None = None,
        *,
        stream: BinaryIO | TextIO | None = None,
        encoding: str = "utf-8",
        mode: str | None = None,
    ) -> None:
        if data is None and stream is None:
            raise ValueError("either data or stream must be provided")
        if data is not None and stream is not None:
            raise ValueError("data and stream are mutually exclusive")
        self._assert_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        del mode
        temporary_path: Path | None = None
        try:
            config = ConfigManager()
            with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                if data is not None:
                    temporary.write(data if isinstance(data, bytes) else data.encode(encoding))
                else:
                    assert stream is not None
                    while chunk := stream.read(config.atomic_write_chunk_size):
                        temporary.write(chunk if isinstance(chunk, bytes) else chunk.encode(encoding))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            path.chmod(config.atomic_write_file_mode)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def atomic_delete(self, path: Path) -> bool:
        self._assert_path(path)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _save_model(self, path: Path, model: BaseModel, *, exclude_none: bool = False) -> None:
        self.atomic_write(path, model.model_dump_json(indent=4, exclude_none=exclude_none))

    def _assert_path(self, path: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(self.data_root.resolve())
        except ValueError as error:
            raise ValueError("path must remain inside app data") from error

    @staticmethod
    def _safe_path(root: Path, parts: tuple[str | Path, ...]) -> Path:
        relative = Path(*parts)
        if relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
            raise ValueError("path must remain inside app data")
        return root / relative
    
    def delete_user_data(self, user: User) -> None:
        raise NotImplementedError("Method not overriden")
    
    def backup_data(self, backup_dir: Path) -> None:
        if type(self) != DataInterface:
            raise NotImplementedError("Meothd not overriden")
        self.generate_metadata_file(backup_dir)
        shutil.copy2(self.users_file, backup_dir / "users.json")

    def _backup_subtree(self, src_dir: Path, backup_dir: Path, name: str) -> None:
        """Copy a subapp's data subtree into the backup, no-op if it doesn't exist.

        Uses ``dirs_exist_ok=True`` so a re-run into an existing backup dir
        merges rather than raising.
        """
        if src_dir.exists():
            shutil.copytree(src_dir, backup_dir / name, dirs_exist_ok=True)

    def load_users(self) -> dict[str, User]:
        """Read-only load. For mutations use edit_users() so the write is locked."""
        self.data_syncer.download_file(self.users_file)
        return self.load_users_local()

    def load_users_local(self) -> dict[str, User]:
        """Read the atomic local snapshot without external synchronization."""
        users_file = self.load_model(self.users_file, UsersFile, sync=False) or UsersFile()
        return users_file.as_dict()

    def _save_users(self, users: list[User]) -> None:
        self._save_model(self.users_file, UsersFile(root=list(users)))

    def edit_users(self):
        """Transactional edit of users.json.

        `with di.edit_users() as users: users.add(...)` — locks the file, loads
        fresh, saves on clean exit (only if changed). `users` is a UsersFile
        with dict-style helpers (get/contains/add/remove).
        """
        return self.edit_model(self.users_file, UsersFile)

    @staticmethod
    def generate_random_string(length: int | None = None) -> str:
        if length is None:
            length = ConfigManager().random_string_length
        letters = string.ascii_lowercase
        result_str = ''.join(random.choice(letters) for _ in range(length))

        return result_str

    def generate_new_user(self, username: str, password: str) -> User:
        users = self.load_users()
        used_folders = {user.folder for user in users.values()}
        for _ in range(ConfigManager().random_generation_attempts):
            folder = self.generate_random_string()
            if folder not in used_folders:
                return User.create(username, password, folder)
        raise RuntimeError("Could not generate unique folder")
    
    def generate_metadata_file(self, backup_dir: Path) -> None:
        repo = Repo(".")
        commit_hash = repo.head.commit.hexsha
        data = {
            "commit_hash": commit_hash,
        }
        self.atomic_write(backup_dir / self.metadata_filename, 
                          data=json.dumps(data, indent=4), 
                          mode='w', 
                          encoding='utf-8')

    def generate_backup_dir(self) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        new_backup = self.backups_directory / timestamp
        new_backup.mkdir(parents=True, exist_ok=True)
        self._prune_backups()
        return new_backup

    def _prune_backups(self) -> None:
        max_count = ConfigManager().backup_max_count
        backups = sorted(p for p in self.backups_directory.iterdir() if p.is_dir())
        for old in backups[:-max_count]:
            shutil.rmtree(old)

    def find_avail_temp_file_path(self, ext: str = "") -> Path:
        dir = ConfigManager().temp_dir
        ext = ext if ext.startswith('.') else f".{ext}"
        config = ConfigManager()
        for _ in range(config.random_generation_attempts):
            temp_file = dir / f"{self.generate_random_string(config.random_string_length)}{ext}"
            if not temp_file.exists():
                return temp_file
        raise RuntimeError("Could not find available temporary file path")
    
    def create_temp_file(self, ext: str = "") -> Path:
        temp_file = self.find_avail_temp_file_path(ext)
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.touch(exist_ok=True)

        return temp_file
    
    @contextmanager
    def temp_file_ctx(self, ext: str = ""):
        """
        Context manager for creating and cleaning up a temp file.
        Usage:
            with self.temp_file_ctx('.txt') as temp_path:
                # use temp_path
        """
        temp_path = self.create_temp_file(ext)
        try:
            yield temp_path
        finally:
            if temp_path.exists():
                temp_path.unlink()
