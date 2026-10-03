from __future__ import annotations

import shutil
from pathlib import Path

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface as HostDataInterface
from web_app.users import User


class DataInterface(HostDataInterface):
    """JSwipe backup and per-user data cleanup."""

    def __init__(self) -> None:
        super().__init__()
        self.root = ConfigManager().save_data_path / "jswipe"

    def backup_data(self, backup_dir: Path) -> None:
        self._backup_subtree(self.root, backup_dir, "jswipe")

    def atomic_write(self, path: Path, data=None, *, stream=None, encoding="utf-8", mode=None):
        super().atomic_write(path, data=data, stream=stream, encoding=encoding, mode=mode)
        if path.resolve(strict=False).is_relative_to(self.root.resolve()):
            path.chmod(ConfigManager().app_data_file_mode)

    def delete_user_data(self, user: User) -> None:
        from .storage import user_data_lock, user_data_root

        if not user.folder:
            raise ValueError("user folder must be a safe path component")
        self.user_path(user)
        path = user_data_root(user.folder)
        with user_data_lock(user.folder, require_active=False):
            if path.exists():
                shutil.rmtree(path)
