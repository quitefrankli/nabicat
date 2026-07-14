import binascii
import logging
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import List, Optional

from PIL import Image
from pydantic import BaseModel
from werkzeug.datastructures import FileStorage

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.config import ConfigManager
from web_app.users import User


def format_file_size(size_bytes: int) -> str:
    """Convert bytes to human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


class FileMetadata(BaseModel):
    crc: int
    original_name: str  # First uploaded name (for reference)
    size: int
    upload_date: str  # ISO format datetime string
    mime_type: str = 'application/octet-stream'


class UserFileEntry(BaseModel):
    crc: int
    original_name: str  # User's name for this file
    path: str = ""


class UserMetadata(BaseModel):
    user_id: str
    files: list[UserFileEntry] = []
    folders: list[str] = []


class Metadata(BaseModel):
    users: dict[str, UserMetadata] = {}
    files: dict[int, FileMetadata] = {}


class DataInterface(BaseDataInterface):
    """Data interface for file storage operations."""

    def __init__(self) -> None:
        super().__init__()
        self.data_sub_dirname = "file_store"
        self.file_store_dir = ConfigManager().save_data_path / self.data_sub_dirname
        self.files_dir = self.file_store_dir / "files"
        self.thumbnails_dir = self.file_store_dir / "thumbnails"
        self.metadata_file = self.file_store_dir / "metadata.json"

    def get_metadata(self) -> Metadata:
        """Load metadata from file, returns empty metadata if file doesn't exist."""
        return self.load_model(self.metadata_file, Metadata, sync=False) or Metadata()

    def save_metadata(self, metadata: Metadata) -> None:
        """Save metadata to file."""
        self.save_model(self.metadata_file, metadata)

    @staticmethod
    def _normalise_path(path: str, *, allow_root: bool = False) -> str:
        path = path.replace('\\', '/')
        if allow_root and path in ('', '.'):
            return ''
        candidate = PurePosixPath(path)
        if candidate.is_absolute() or not path or any(part in ('', '.', '..') for part in candidate.parts):
            raise ValueError('Invalid file path')
        return candidate.as_posix()

    @staticmethod
    def _entry_path(entry: UserFileEntry) -> str:
        return entry.path or entry.original_name

    def _user_metadata(self, metadata: Metadata, user: User) -> UserMetadata:
        return metadata.users.setdefault(user.id, UserMetadata(user_id=user.id))

    @staticmethod
    def _parent_folders(path: str) -> list[str]:
        parents = []
        parent = PurePosixPath(path).parent
        while parent != PurePosixPath('.'):
            parents.append(parent.as_posix())
            parent = parent.parent
        return list(reversed(parents))

    def _ensure_parent_folders(self, user_metadata: UserMetadata, path: str) -> None:
        for folder in self._parent_folders(path):
            if folder not in user_metadata.folders:
                user_metadata.folders.append(folder)

    def _cleanup_unreferenced(self, metadata: Metadata) -> None:
        referenced = {
            entry.crc
            for user_metadata in metadata.users.values()
            for entry in user_metadata.files
        }
        for crc in set(metadata.files) - referenced:
            self.atomic_delete(self.files_dir / str(crc))
            self.atomic_delete(self.get_thumbnail_path(crc))
            metadata.files.pop(crc, None)

    def get_user_metadata(self, user: User) -> UserMetadata:
        """Get user metadata, creates new if doesn't exist."""
        metadata = self.get_metadata()
        if user.id not in metadata.users:
            metadata.users[user.id] = UserMetadata(user_id=user.id)
        return metadata.users[user.id]

    def save_file(
        self,
        file_storage: FileStorage,
        user: User,
        relative_path: str | None = None,
        metadata: Metadata | None = None,
    ) -> int:
        """Save a file and return its CRC."""
        self.files_dir.mkdir(parents=True, exist_ok=True)
        chunk_size = ConfigManager().file_store.upload_stream_chunk_bytes
        crc = 0
        file_size = 0

        # Flask has already spooled the multipart upload; copy it in bounded chunks
        # so large files are never loaded into the Gunicorn worker's memory.
        with tempfile.NamedTemporaryFile(dir=self.files_dir, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            try:
                while chunk := file_storage.stream.read(chunk_size):
                    temp_file.write(chunk)
                    crc = binascii.crc32(chunk, crc)
                    file_size += len(chunk)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

        persist = metadata is None
        metadata = metadata or self.get_metadata()
        user_metadata = self._user_metadata(metadata, user)
        stored_path = self._normalise_path(relative_path or file_storage.filename)
        existing_entry = next(
            (entry for entry in user_metadata.files if self._entry_path(entry) == stored_path),
            None,
        )

        # Ignore duplicate uploads for the same user when content matches.
        if (existing_entry and existing_entry.crc == crc) or (
            relative_path is None and any(entry.crc == crc for entry in user_metadata.files)
        ):
            temp_path.unlink(missing_ok=True)
            return crc

        # Check if file content already exists on disk
        if crc not in metadata.files:
            file_path = self.files_dir / str(crc)
            os.replace(temp_path, file_path)
            file_path.chmod(0o644)

            # Create file metadata (use first uploaded name as reference)
            file_metadata = FileMetadata(
                crc=crc,
                original_name=file_storage.filename,
                size=file_size,
                upload_date=datetime.now().isoformat(),
                mime_type=file_storage.content_type or 'application/octet-stream'
            )
            metadata.files[crc] = file_metadata
        else:
            temp_path.unlink(missing_ok=True)

        if existing_entry:
            user_metadata.files.remove(existing_entry)

        # Add to user's file list for new user content entry.
        user_file_entry = UserFileEntry(
            crc=crc,
            original_name=PurePosixPath(stored_path).name,
            path=stored_path,
        )
        user_metadata.files.append(user_file_entry)
        self._ensure_parent_folders(user_metadata, stored_path)

        if persist:
            self._cleanup_unreferenced(metadata)
            self.save_metadata(metadata)
        return crc

    def save_files(
        self,
        uploads: list[tuple[FileStorage, str]],
        folders: list[str],
        user: User,
    ) -> None:
        """Atomically save a validated file/folder batch."""
        if len(uploads) > ConfigManager().file_store.folder_upload_max_entries:
            raise ValueError('Too many files in folder upload')
        paths = [self._normalise_path(path) for _, path in uploads]
        if len(paths) != len(set(paths)):
            raise ValueError('Folder upload contains duplicate paths')

        metadata = self.get_metadata()
        original_crcs = set(metadata.files)
        working_metadata = deepcopy(metadata)
        user_metadata = self._user_metadata(working_metadata, user)
        existing_paths = {self._entry_path(entry) for entry in user_metadata.files}
        required_folders = {
            parent
            for path in paths
            for parent in self._parent_folders(path)
        }
        required_folders.update(self._normalise_path(folder) for folder in folders)
        if required_folders & set(paths) or (required_folders & existing_paths) - set(paths):
            raise ValueError('A file conflicts with a folder path')
        if any(
            path in user_metadata.folders or any(folder.startswith(f'{path}/') for folder in user_metadata.folders)
            for path in paths
        ):
            raise ValueError('A folder conflicts with a file path')
        for folder in folders:
            self._ensure_parent_folders(user_metadata, self._normalise_path(folder))
            normalised = self._normalise_path(folder)
            if normalised not in user_metadata.folders:
                user_metadata.folders.append(normalised)
        try:
            for file_storage, path in uploads:
                self.save_file(file_storage, user, path, metadata=working_metadata)
            self._cleanup_unreferenced(working_metadata)
            self.save_metadata(working_metadata)
        except Exception:
            for crc in set(working_metadata.files) - original_crcs:
                self.atomic_delete(self.files_dir / str(crc))
                self.atomic_delete(self.get_thumbnail_path(crc))
            raise

    def get_folder_files(self, path: str, user: User) -> list[tuple[str, Path]]:
        folder_path = self._normalise_path(path)
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)
        if not user_metadata:
            raise FileNotFoundError(folder_path)
        prefix = f'{folder_path}/'
        files = [
            (entry_path, self.files_dir / str(entry.crc))
            for entry in user_metadata.files
            if (entry_path := self._entry_path(entry)).startswith(prefix)
        ]
        if not files and folder_path not in user_metadata.folders:
            raise FileNotFoundError(folder_path)
        return files

    def get_file_path(self, filename: str, user: User) -> Path:
        """Get the full path to a file by its original name."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            raise FileNotFoundError(f"User {user.id} has no files")

        target_path = self._normalise_path(filename)
        for user_file in user_metadata.files:
            if self._entry_path(user_file) == target_path:
                return self.files_dir / str(user_file.crc)

        raise FileNotFoundError(f"File {filename} not found for user {user.id}")

    def delete_file(self, filename: str, user: User) -> None:
        """Delete a file from the user's storage."""
        self.delete_path(filename, user)

    def delete_path(self, path: str, user: User) -> None:
        """Delete one file or a folder and all of its contents."""
        target_path = self._normalise_path(path)
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            raise FileNotFoundError(f"File: {filename} not found for user: {user.id}")

        is_folder = target_path in user_metadata.folders
        removed = [
            entry for entry in user_metadata.files
            if self._entry_path(entry) == target_path or self._entry_path(entry).startswith(f'{target_path}/')
        ]
        if not removed and not is_folder:
            raise FileNotFoundError(f"File: {path} not found for user: {user.id}")

        user_metadata.files = [entry for entry in user_metadata.files if entry not in removed]
        user_metadata.folders = [
            folder for folder in user_metadata.folders
            if folder != target_path and not folder.startswith(f'{target_path}/')
        ]

        self._cleanup_unreferenced(metadata)
        self.save_metadata(metadata)

    def create_folder(self, path: str, user: User) -> None:
        folder_path = self._normalise_path(path)
        metadata = self.get_metadata()
        user_metadata = self._user_metadata(metadata, user)
        if any(self._entry_path(entry) == folder_path for entry in user_metadata.files):
            raise ValueError('A file already exists at this path')
        self._ensure_parent_folders(user_metadata, folder_path)
        if folder_path not in user_metadata.folders:
            user_metadata.folders.append(folder_path)
        self.save_metadata(metadata)

    def list_directory(self, path: str, user: User) -> dict[str, list[dict]]:
        directory = self._normalise_path(path, allow_root=True)
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)
        if not user_metadata:
            return {'folders': [], 'files': []}
        prefix = f'{directory}/' if directory else ''
        folders = set(user_metadata.folders)
        for entry in user_metadata.files:
            entry_path = self._entry_path(entry)
            if '/' in entry_path:
                folders.update(self._parent_folders(entry_path))
        direct_folders = sorted(
            [
                {'name': folder[len(prefix):], 'path': folder}
                for folder in folders
                if folder.startswith(prefix) and '/' not in folder[len(prefix):]
            ],
            key=lambda item: item['name'].lower(),
        )
        files = []
        for entry in user_metadata.files:
            entry_path = self._entry_path(entry)
            if not entry_path.startswith(prefix) or '/' in entry_path[len(prefix):]:
                continue
            file_meta = metadata.files.get(entry.crc)
            if file_meta:
                files.append({
                    'name': entry_path[len(prefix):], 'path': entry_path, 'size': file_meta.size,
                    'size_formatted': format_file_size(file_meta.size), 'mime_type': file_meta.mime_type,
                })
        return {'folders': direct_folders, 'files': sorted(files, key=lambda item: item['name'].lower())}

    def move_path(self, source: str, destination: str, user: User) -> None:
        source_path = self._normalise_path(source)
        destination_path = self._normalise_path(destination)
        if destination_path == source_path or destination_path.startswith(f'{source_path}/'):
            raise ValueError('Invalid destination')
        metadata = self.get_metadata()
        user_metadata = self._user_metadata(metadata, user)
        source_files = [entry for entry in user_metadata.files if self._entry_path(entry) == source_path]
        is_folder = source_path in user_metadata.folders or any(
            self._entry_path(entry).startswith(f'{source_path}/') for entry in user_metadata.files
        )
        if not source_files and not is_folder:
            raise FileNotFoundError(source_path)
        affected_paths = {
            self._entry_path(entry) for entry in user_metadata.files
            if self._entry_path(entry) == source_path or self._entry_path(entry).startswith(f'{source_path}/')
        }
        destinations = {
            destination_path + entry_path[len(source_path):]
            for entry_path in affected_paths
        }
        if any(self._entry_path(entry) in destinations for entry in user_metadata.files if self._entry_path(entry) not in affected_paths):
            raise ValueError('Destination already exists')
        for entry in user_metadata.files:
            current = self._entry_path(entry)
            if current in affected_paths:
                entry.path = destination_path + current[len(source_path):]
                entry.original_name = PurePosixPath(entry.path).name
        old_folders = list(user_metadata.folders)
        user_metadata.folders = [folder for folder in user_metadata.folders if not (folder == source_path or folder.startswith(f'{source_path}/'))]
        for folder in old_folders:
            if folder == source_path or folder.startswith(f'{source_path}/'):
                user_metadata.folders.append(destination_path + folder[len(source_path):])
        self._ensure_parent_folders(user_metadata, destination_path)
        user_metadata.folders = list(dict.fromkeys(user_metadata.folders))
        self.save_metadata(metadata)

    def list_files(self, user: User) -> List[str]:
        """Get list of filenames for a user."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            return []

        return [self._entry_path(user_file) for user_file in user_metadata.files]

    def list_files_with_metadata(self, user: User) -> List[dict]:
        """Get list of files with their metadata (size, upload date)."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            return []

        files = []
        for user_file in user_metadata.files:
            file_meta = metadata.files.get(user_file.crc)
            if file_meta:
                upload_date = datetime.fromisoformat(file_meta.upload_date)
                files.append({
                    'name': self._entry_path(user_file),
                    'size': file_meta.size,
                    'size_formatted': format_file_size(file_meta.size),
                    'modified': upload_date,
                    'modified_formatted': upload_date.strftime('%Y-%m-%d %H:%M'),
                    'crc': file_meta.crc,
                    'mime_type': file_meta.mime_type
                })

        # Sort by upload date descending (newest first)
        files.sort(key=lambda x: x['modified'], reverse=True)
        return files

    def get_total_storage_size(self, user: User) -> int:
        """Get total storage size used by a user in bytes."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            return 0

        total_size = 0
        for user_file in user_metadata.files:
            file_meta = metadata.files.get(user_file.crc)
            if file_meta:
                total_size += file_meta.size

        return total_size

    def validate_batch_quota(self, paths: list[str], incoming_size: int, user: User) -> None:
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)
        replaced_size = 0
        if user_metadata:
            replacing = {self._normalise_path(path) for path in paths}
            for entry in user_metadata.files:
                if self._entry_path(entry) in replacing and (file_meta := metadata.files.get(entry.crc)):
                    replaced_size += file_meta.size
        max_storage = (
            ConfigManager().file_store.admin_quota_bytes
            if user.has_elevated_access()
            else ConfigManager().file_store.non_admin_quota_bytes
        )
        final_size = self.get_total_storage_size(user) - replaced_size + incoming_size
        if final_size > max_storage:
            raise ValueError(f'Upload exceeds the {format_file_size(max_storage)} storage limit')

    def get_thumbnail_path(self, crc: int) -> Path:
        """Get the path to a thumbnail file."""
        return self.thumbnails_dir / f"{crc}.jpg"

    def has_thumbnail(self, crc: int) -> bool:
        """Check if a thumbnail exists for a file."""
        return self.get_thumbnail_path(crc).exists()

    def create_thumbnail(self, crc: int, max_size: tuple = (300, 300)) -> Optional[Path]:
        """Create a thumbnail for an image file."""
        file_path = self.files_dir / str(crc)
        if not file_path.exists():
            return None

        thumbnail_path = self.get_thumbnail_path(crc)
        if thumbnail_path.exists():
            return thumbnail_path

        try:
            # Open the image
            with Image.open(file_path) as img:
                # Convert RGBA to RGB if necessary
                if img.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                    img = background
                elif img.mode != 'RGB':
                    img = img.convert('RGB')

                # Create thumbnail
                img.thumbnail(max_size, Image.Resampling.LANCZOS)

                # Save thumbnail
                self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
                img.save(thumbnail_path, 'JPEG', quality=85, optimize=True)

                return thumbnail_path
        except Exception as e:
            logging.error(f"Failed to create thumbnail for CRC {crc}: {e}")
            return None

    def get_thumbnail_for_file(self, filename: str, user: User) -> Optional[Path]:
        """Get or create thumbnail for a user's file."""
        try:
            # Get the file path to find the CRC
            file_path = self.get_file_path(filename, user)
            crc = int(file_path.name)

            # Check if thumbnail exists, create if not
            if not self.has_thumbnail(crc):
                return self.create_thumbnail(crc)

            return self.get_thumbnail_path(crc)
        except Exception as e:
            logging.error(f"Failed to get thumbnail for {filename}: {e}")
            return None

    def backup_data(self, backup_dir: Path) -> None:
        """Backup file store data to the backup directory."""
        self._backup_subtree(self.file_store_dir, backup_dir, self.data_sub_dirname)

    def delete_user_data(self, user: User) -> None:
        metadata = self.get_metadata()
        user_metadata = metadata.users.pop(user.id, None)
        if user_metadata is None:
            return

        user_crcs = {user_file.crc for user_file in user_metadata.files}
        referenced_crcs = {
            user_file.crc
            for other_user_metadata in metadata.users.values()
            for user_file in other_user_metadata.files
        }

        for crc in user_crcs - referenced_crcs:
            self.atomic_delete(self.files_dir / str(crc))
            self.atomic_delete(self.get_thumbnail_path(crc))
            metadata.files.pop(crc, None)

        self.save_metadata(metadata)
