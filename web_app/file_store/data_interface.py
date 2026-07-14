import binascii
import logging
import os
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
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


class UserMetadata(BaseModel):
    user_id: str
    files: list[UserFileEntry] = []


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

    def get_user_metadata(self, user: User) -> UserMetadata:
        """Get user metadata, creates new if doesn't exist."""
        metadata = self.get_metadata()
        if user.id not in metadata.users:
            metadata.users[user.id] = UserMetadata(user_id=user.id)
        return metadata.users[user.id]

    def save_file(self, file_storage: FileStorage, user: User) -> int:
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

        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id, UserMetadata(user_id=user.id))

        # Ignore duplicate uploads for the same user when content matches.
        if any(existing_file.crc == crc for existing_file in user_metadata.files):
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

        # Add to user's file list for new user content entry.
        user_file_entry = UserFileEntry(crc=crc, original_name=file_storage.filename)
        user_metadata.files.append(user_file_entry)
        metadata.users[user.id] = user_metadata

        self.save_metadata(metadata)
        return crc

    def get_file_path(self, filename: str, user: User) -> Path:
        """Get the full path to a file by its original name."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            raise FileNotFoundError(f"User {user.id} has no files")

        # Find the file by original name in user's file list
        for user_file in user_metadata.files:
            if user_file.original_name == filename:
                return self.files_dir / str(user_file.crc)

        raise FileNotFoundError(f"File {filename} not found for user {user.id}")

    def delete_file(self, filename: str, user: User) -> None:
        """Delete a file from the user's storage."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            raise FileNotFoundError(f"File: {filename} not found for user: {user.id}")

        # Find and remove the file entry by original name
        user_file_to_delete = None
        for user_file in user_metadata.files:
            if user_file.original_name == filename:
                user_file_to_delete = user_file
                break

        if user_file_to_delete is None:
            raise FileNotFoundError(f"File: {filename} not found for user: {user.id}")

        crc_to_delete = user_file_to_delete.crc
        user_metadata.files.remove(user_file_to_delete)

        # Check if any user still has this CRC
        file_in_use = False
        for other_user_meta in metadata.users.values():
            for user_file in other_user_meta.files:
                if user_file.crc == crc_to_delete:
                    file_in_use = True
                    break
            if file_in_use:
                break

        # If no user has this file anymore, delete it from disk and metadata
        if not file_in_use:
            file_path = self.files_dir / str(crc_to_delete)
            if file_path.exists():
                file_path.unlink()
            metadata.files.pop(crc_to_delete, None)

        self.save_metadata(metadata)

    def list_files(self, user: User) -> List[str]:
        """Get list of filenames for a user."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            return []

        return [user_file.original_name for user_file in user_metadata.files]

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
                    'name': user_file.original_name,
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
