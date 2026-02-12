import binascii
import json
import logging
import shutil

from io import BytesIO
from pathlib import Path
from pydantic import BaseModel
from copy import deepcopy
from pydub import AudioSegment

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.users import User
from web_app.config import ConfigManager


class Playlist(BaseModel):
    name: str
    audio_crcs: list[int] = []

class UserMetadata(BaseModel):
    user_id: str
    playlists: dict[str, Playlist] = {}

    def add_to_playlist(self, audio_crc: int, playlist_name: str = "Favourites") -> None:
        playlist = self.get_playlist(playlist_name)
        if audio_crc not in playlist.audio_crcs:
            playlist.audio_crcs.append(audio_crc)

    def remove_from_playlist(self, audio_crc: int, playlist_name: str = "Favourites") -> None:
        playlist = self.get_playlist(playlist_name)
        if audio_crc in playlist.audio_crcs:
            playlist.audio_crcs.remove(audio_crc)

    def remove_from_all_playlists(self, audio_crc: int) -> None:
        for playlist in self.playlists.values():
            if audio_crc in playlist.audio_crcs:
                playlist.audio_crcs.remove(audio_crc)

    def get_playlist(self, playlist_name: str = "Favourites") -> Playlist:
        if playlist_name not in self.playlists:
            self.playlists[playlist_name] = Playlist(name=playlist_name)
        
        return self.playlists[playlist_name]
    
    def get_playlists(self) -> list[Playlist]:
        return list(self.playlists.values())

class AudioMetadata(BaseModel):
    # this is also the filename to be saved on disk
    # technically it's possible to have multiple audios with the same crc
    # but the chances of such collision are extremely low
    crc: int
    title: str
    yt_video_id: str = ''  # optional, if the audio is from YouTube
    is_cached: bool = False
    source_url: str = ''  # original source URL (e.g. YouTube URL)

class Metadata(BaseModel):
    # username -> UserMetadata
    users: dict[str, UserMetadata] = {}
    # audio crc -> AudioMetadata
    audios: dict[int, AudioMetadata] = {}

class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.app_dir = ConfigManager().save_data_path / "tubio"
        self.app_audio_dir = self.app_dir / "audio"
        self.app_thumbnails_dir = self.app_dir / "thumbnails"
        self.app_metadata_file = self.app_dir / "metadata.json"

    def delete_audio(self, crc: int) -> None:
        metadata = self.get_metadata()
        if crc not in metadata.audios:
            raise ValueError(f"Audio with crc {crc} does not exist.")
        metadata.audios.pop(crc)
        self.save_metadata(metadata)
        self.atomic_delete(self.app_audio_dir / f"{crc}.m4a")

    def get_metadata(self) -> Metadata:
        if not self.app_metadata_file.exists():
            return Metadata()
        with open(self.app_metadata_file, 'r') as f:
            data = f.read()

        return Metadata(**json.loads(data))
    
    def get_user_metadata(self, user: User) -> UserMetadata:
        metadata = self.get_metadata()
        if user.id not in metadata.users:
            metadata.users[user.id] = UserMetadata(user_id=user.id)

        return metadata.users[user.id]
    
    def get_audio_metadata(self, crc: int|None = None, yt_video_id: str|None = None) -> AudioMetadata:
        if not ((crc is None) ^ (yt_video_id is None)):
            raise ValueError("Either crc or yt_video_id must be provided, but not both.")
        
        metadata = self.get_metadata()
        if crc is not None:
            if crc not in metadata.audios:
                raise ValueError(f"Audio with crc {crc} does not exist.")
            return metadata.audios[crc]
        else:
            for audio in metadata.audios.values():
                if audio.yt_video_id == yt_video_id:
                    return audio
            raise ValueError(f"Audio with yt_video_id {yt_video_id} does not exist.")
        
    def save_audio(self, title: str, audio_data: bytes, ext: str) -> int:
        crc = binascii.crc32(audio_data)

        if crc in self.get_metadata().audios:
            logging.warning(f"Audio with crc {crc} already exists, skipping save.")
            return crc  # already exists

        audio_path = self.app_audio_dir / f"{crc}.{ext}"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audio_path, 'wb') as f:
            f.write(audio_data)

        if ext != 'm4a':
            # convert to m4a
            audio = AudioSegment.from_file(audio_path, format=ext)
            output_path = self.app_audio_dir / f"{crc}.m4a"
            audio.export(output_path, format='mp4', bitrate="128k")
            audio_path.unlink()  # remove original file

        audio_metadata = AudioMetadata(crc=crc, title=title, is_cached=True)
        self.save_audio_metadata(audio_metadata)

        return crc

    def save_audio_metadata(self, audio_metadata: AudioMetadata) -> None:
        metadata = self.get_metadata()
        metadata.audios[audio_metadata.crc] = audio_metadata
        self.save_metadata(metadata)
    
    def save_user_metadata(self, user: User, user_metadata: UserMetadata) -> None:
        metadata = self.get_metadata()
        metadata.users[user.id] = user_metadata
        self.save_metadata(metadata)
    
    def save_metadata(self, metadata: Metadata) -> None:
        self.atomic_write(self.app_metadata_file, 
                          data=metadata.model_dump_json(indent=4), 
                          mode="w", 
                          encoding='utf-8')

    def get_audio_path(self, crc: int, metadata: Metadata|None = None) -> Path:
        """DEPRECATED want to stream eventually"""

        metadata = self.get_metadata() if metadata is None else metadata
        if crc not in metadata.audios:
            raise ValueError(f"Audio with crc {crc} does not exist.")

        return self.app_audio_dir / f"{crc}.m4a"

    def get_thumbnail_path(self, crc: int) -> Path:
        return self.app_thumbnails_dir / f"{crc}.jpg"

    def save_thumbnail(self, crc: int, thumbnail_data: bytes) -> None:
        self.app_thumbnails_dir.mkdir(parents=True, exist_ok=True)
        thumbnail_path = self.get_thumbnail_path(crc)
        with open(thumbnail_path, 'wb') as f:
            f.write(thumbnail_data)

    def has_thumbnail(self, crc: int) -> bool:
        return self.get_thumbnail_path(crc).exists()

    def cleanup_unused_tracks(self) -> None:
        metadata = self.get_metadata()
        used_crcs = set()
        for user_metadata in metadata.users.values():
            for playlist in user_metadata.playlists.values():
                used_crcs.update(playlist.audio_crcs)
        
        all_crcs = set(metadata.audios.keys())
        unused_crcs = all_crcs - used_crcs

        for crc in unused_crcs:
            self.delete_audio(crc)
            logging.info(f"Deleted unused audio with crc {crc}.")

    def delete_user_data(self, user: User) -> None:
        metadata = self.get_metadata()
        if user.id not in metadata.users:
            return

        metadata.users.pop(user.id)
        self.save_metadata(metadata)
        self.cleanup_unused_tracks()
        self.cleanup_unused_thumbnails()

    def cleanup_unused_thumbnails(self) -> None:
        if not self.app_thumbnails_dir.exists():
            return

        metadata = self.get_metadata()
        used_crcs = {str(crc) for crc in metadata.audios.keys()}
        for thumbnail_path in self.app_thumbnails_dir.glob("*.jpg"):
            if thumbnail_path.stem not in used_crcs:
                self.atomic_delete(thumbnail_path)
    
    def backup_data(self, backup_dir: Path) -> None:
        tubio_backup_dir = backup_dir / "tubio"
        tubio_backup_dir.mkdir(parents=True, exist_ok=True)
        audio_backup_dir = tubio_backup_dir / "audio"
        audio_backup_dir.mkdir(parents=True, exist_ok=True)
        
        metadata = deepcopy(self.get_metadata())
        for audio in metadata.audios.values():
            if audio.yt_video_id:
                audio.is_cached = False
            else:
                # only copy files that cannot be easily redownloaded from yt
                shutil.copy2(self.get_audio_path(audio.crc, metadata), 
                             audio_backup_dir / f"{audio.crc}.m4a")

        self.atomic_write(tubio_backup_dir / "metadata.json", 
                          data=metadata.model_dump_json(indent=4), 
                          mode="w", 
                          encoding='utf-8')