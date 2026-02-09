import shutil
from datetime import datetime
from pathlib import Path
from typing import List

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.config import ConfigManager
from web_app.users import User


# Maximum total storage size for non-admin users (in bytes)
NON_ADMIN_MAX_STORAGE = 100 * 1024 * 1024  # 100 MB


def format_file_size(size_bytes: int) -> str:
    """Convert bytes to human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


class DataInterface(BaseDataInterface):
    """Data interface for file storage operations."""
    
    def __init__(self) -> None:
        super().__init__()
        self.data_sub_dirname = "file_store"
        self.file_store_dir = ConfigManager().save_data_path / self.data_sub_dirname

    def save_file(self, file_storage: FileStorage, user: User) -> None:
        """Save a file to the user's storage directory."""
        user_dir = self._get_user_dir(user)
        # Sanitize filename to prevent path traversal
        safe_filename = secure_filename(file_storage.filename)
        file_path = user_dir / safe_filename

        self.atomic_write(file_path, stream=file_storage.stream, mode="wb")

    def get_file_path(self, filename: str, user: User) -> Path:
        """Get the full path to a user's file."""
        # Sanitize filename to prevent path traversal
        safe_filename = secure_filename(filename)
        return self._get_user_dir(user) / safe_filename

    def delete_file(self, filename: str, user: User) -> None:
        """Delete a file from the user's storage."""
        file_path = self.get_file_path(filename, user)

        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"File: {filename} not found for user: {user.id}")
        
        file_path.unlink()

    def list_files(self, user: User) -> List[str]:
        """Get list of filenames for a user."""
        user_dir = self._get_user_dir(user)
        if not user_dir.exists():
            return []
        return [f.name for f in user_dir.iterdir() if f.is_file()]

    def list_files_with_metadata(self, user: User) -> List[dict]:
        """Get list of files with their metadata (size, upload date)."""
        user_dir = self._get_user_dir(user)
        if not user_dir.exists():
            return []
        
        files = []
        for file_path in user_dir.iterdir():
            if file_path.is_file():
                stat = file_path.stat()
                files.append({
                    'name': file_path.name,
                    'size': stat.st_size,
                    'size_formatted': format_file_size(stat.st_size),
                    'modified': datetime.fromtimestamp(stat.st_mtime),
                    'modified_formatted': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
                })
        # Sort by modified date descending (newest first)
        files.sort(key=lambda x: x['modified'], reverse=True)
        return files

    def get_total_storage_size(self, user: User) -> int:
        """Get total storage size used by a user in bytes."""
        user_dir = self._get_user_dir(user)
        if not user_dir.exists():
            return 0
        total_size = 0
        for file_path in user_dir.iterdir():
            if file_path.is_file():
                total_size += file_path.stat().st_size
        return total_size

    def backup_data(self, backup_dir: Path) -> None:
        """Backup file store data to the backup directory."""
        shutil.copytree(self.file_store_dir, backup_dir / self.data_sub_dirname)

    def _get_user_dir(self, user: User) -> Path:
        """Get the storage directory for a specific user."""
        return self.file_store_dir / user.folder
