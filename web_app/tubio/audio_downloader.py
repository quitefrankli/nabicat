import requests
import re
import json
import logging
import yt_dlp
import os
import binascii

from typing import *
from pathlib import Path
from datetime import datetime, timedelta

from web_app.config import ConfigManager
from web_app.redis_client import get_redis
from web_app.tubio.data_interface import Metadata, UserMetadata, AudioMetadata, DataInterface
from web_app.users import User


class VideoTooLongError(Exception):
    """Raised when a video exceeds the maximum allowed length."""
    def __init__(self, video_id: str, duration: timedelta, max_duration: timedelta):
        self.video_id = video_id
        self.duration = duration
        self.max_duration = max_duration
        super().__init__(
            f"Video {video_id} is too long ({duration} > {max_duration})"
        )


_PROGRESS_PREFIX = "nabicat:tubio:progress:"


class DownloadProgress:
    """Tracks download progress for a specific video.

    State lives in Redis (keyed by video_id) so the SSE progress stream can be
    served by a different gunicorn worker than the one running the download.
    Every attribute assignment write-throughs to Redis.
    """
    def __init__(self, video_id: str):
        # Bypass __setattr__ during init so the write-through sees a complete
        # object.
        object.__setattr__(self, "video_id", video_id)
        object.__setattr__(self, "percent", 0.0)
        object.__setattr__(self, "status", "starting")
        object.__setattr__(self, "error", None)
        self._persist()

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name in ("percent", "status", "error"):
            self._persist()

    def _persist(self) -> None:
        payload = json.dumps({
            "percent": self.percent,
            "status": self.status,
            "error": self.error,
        })
        get_redis().set(
            _PROGRESS_PREFIX + self.video_id,
            payload,
            ex=ConfigManager().tubio.download_progress_ttl_s,
        )


def get_download_progress(video_id: str) -> DownloadProgress | None:
    raw = get_redis().get(_PROGRESS_PREFIX + video_id)
    if raw is None:
        return None
    data = json.loads(raw)
    # Rebuild without re-persisting: construct bare, then set fields directly.
    progress = object.__new__(DownloadProgress)
    object.__setattr__(progress, "video_id", video_id)
    object.__setattr__(progress, "percent", data.get("percent", 0.0))
    object.__setattr__(progress, "status", data.get("status", "starting"))
    object.__setattr__(progress, "error", data.get("error"))
    return progress


def clear_download_progress(video_id: str) -> None:
    get_redis().delete(_PROGRESS_PREFIX + video_id)


class AudioDownloader:
    YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"

    # Patterns for YouTube URLs
    YOUTUBE_URL_PATTERNS = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ]

    @staticmethod
    def extract_video_id(query: str) -> str | None:
        """Extract video ID from a YouTube URL. Returns None if not a valid YouTube URL."""
        query = query.strip()
        for pattern in AudioDownloader.YOUTUBE_URL_PATTERNS:
            match = re.match(pattern, query)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def get_video_info(video_id: str, cached_yt_vid_ids: Set[str]) -> dict | None:
        """Fetch info for a single video by ID using yt-dlp."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }

        if ConfigManager().tubio.cookie_path.exists():
            ydl_opts['cookiefile'] = str(ConfigManager().tubio.cookie_path)
        if ConfigManager().debug_mode:
            ydl_opts['nocheckcertificate'] = True

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None

                duration = info.get('duration', 0)
                vid_length = timedelta(seconds=duration)
                max_length = ConfigManager().tubio.max_video_length

                if vid_length > max_length:
                    raise VideoTooLongError(video_id, vid_length, max_length)

                # Format duration as MM:SS or HH:MM:SS
                hours, remainder = divmod(duration, 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    length_txt = f"{hours}:{minutes:02d}:{seconds:02d}"
                else:
                    length_txt = f"{minutes}:{seconds:02d}"

                cached = video_id in cached_yt_vid_ids
                view_count = info.get('view_count', 0)
                view_count_str = f"{view_count:,} views" if view_count else ''

                # Get best thumbnail URL
                thumbnail_url = info.get('thumbnail', '')
                if not thumbnail_url:
                    thumbnails = info.get('thumbnails', [])
                    if thumbnails:
                        # Prefer medium quality thumbnail
                        thumbnail_url = thumbnails[-1].get('url', '')

                return {
                    "video_id": video_id,
                    "url": url,
                    "title": info.get('title', ''),
                    "description": info.get('description', '')[:500] if info.get('description') else '',
                    "view_count": view_count_str,
                    "published": info.get('upload_date', ''),
                    "length": length_txt,
                    "cached": cached,
                    "thumbnail_url": thumbnail_url,
                }
        except VideoTooLongError:
            raise
        except Exception:
            logging.exception(f"Failed to get video info for {video_id}")
            return None

    @staticmethod
    def get_vid_length(text: str) -> timedelta:
        parts = reversed(text.split(':'))
        sec_map = [ 1, 60, 3600 ]  # seconds, minutes, hours
        total_seconds = sum(int(part) * sec for part, sec in zip(parts, sec_map))

        return timedelta(seconds=total_seconds)

    @staticmethod
    def search_youtube(query: str, cached_yt_vid_ids: Set[str], page: int = 0) -> dict:
        """
        Search YouTube for videos matching the query. Returns a dict with paginated results
        and pagination metadata: {"results": [...], "page": int, "has_prev": bool, "has_next": bool}.
        If query is a direct YouTube URL, returns only that video with no pagination.

        Raises:
            VideoTooLongError: If a direct URL video exceeds the maximum allowed length.
        """
        # Check if query is a direct YouTube URL
        video_id = AudioDownloader.extract_video_id(query)
        if video_id:
            logging.info(f"Direct YouTube URL detected, fetching video: {video_id}")
            # Let VideoTooLongError propagate for direct URLs
            video_info = AudioDownloader.get_video_info(video_id, cached_yt_vid_ids)
            results = [video_info] if video_info else []
            return {"results": results, "page": 0, "total_pages": 1}

        params = {"search_query": query}
        response = requests.get(AudioDownloader.YOUTUBE_SEARCH_URL, params=params)
        response.raise_for_status()
        html = response.text
        # Extract ytInitialData JSON
        initial_data_match = re.search(r'var ytInitialData = (\{.*?\});', html, re.DOTALL)
        if not initial_data_match:
            return {"results": [], "page": 0, "total_pages": 1}
        try:
            data = json.loads(initial_data_match.group(1))
        except Exception:
            logging.exception("Failed to parse YouTube search results")
            return {"results": [], "page": 0, "total_pages": 1}
        # Traverse the JSON to get videoRenderer items
        sections = data.get('contents', {}) \
            .get('twoColumnSearchResultsRenderer', {}) \
            .get('primaryContents', {}) \
            .get('sectionListRenderer', {}) \
            .get('contents', [])

        logging.info(f"Searching YouTube with query: {query} (page {page})")
        all_results = []
        for section in sections:
            items = section.get('itemSectionRenderer', {}).get('contents', [])
            for item in items:
                video = item.get('videoRenderer')
                if not video:
                    continue
                length_txt = video.get('lengthText', {}).get('simpleText', '')
                if not length_txt:
                    continue
                vid_length = AudioDownloader.get_vid_length(length_txt)
                if vid_length > ConfigManager().tubio.max_video_length:
                    continue
                vid_id = video.get('videoId')
                cached = vid_id in cached_yt_vid_ids

                view_count = video.get('viewCountText', {}).get('simpleText', '')
                published = video.get('publishedTimeText', {}).get('simpleText', '')
                title = ''.join([r.get('text', '') for r in video.get('title', {}).get('runs', [])])
                description = ''
                if 'detailedMetadataSnippets' in video:
                    description = ' '.join([s.get('snippetText', {}).get('runs', [{}])[0].get('text', '') for s in video['detailedMetadataSnippets']])

                # Get thumbnail URL (prefer medium quality)
                thumbnails = video.get('thumbnail', {}).get('thumbnails', [])
                thumbnail_url = thumbnails[-1].get('url', '') if thumbnails else ''

                all_results.append({
                    "video_id": vid_id,
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                    "title": title,
                    "description": description,
                    "view_count": view_count,
                    "published": published,
                    "length": length_txt,
                    "cached": cached,
                    "thumbnail_url": thumbnail_url,
                })

        page_size = ConfigManager().tubio.max_results
        max_pages = ConfigManager().tubio.max_search_pages
        total_pages = min(max_pages, max(1, (len(all_results) + page_size - 1) // page_size))
        page = max(0, min(page, total_pages - 1))
        start = page * page_size
        end = start + page_size
        return {
            "results": all_results[start:end],
            "page": page,
            "total_pages": total_pages,
        }

    @staticmethod
    def download_thumbnail(video_id: str, crc: int) -> Path | None:
        """Download and cache the video thumbnail. Returns the local path or None on failure."""
        # Use small YouTube thumbnail (320x180) - sufficient for our UI
        thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
        try:
            response = requests.get(thumbnail_url, timeout=10)
            response.raise_for_status()
            DataInterface().save_thumbnail(crc, response.content)
            logging.info(f"Cached thumbnail for video {video_id}")
            return DataInterface().get_thumbnail_path(crc)
        except Exception:
            logging.exception(f"Failed to download thumbnail for {video_id}")
            return None

    @staticmethod
    def _build_ydl_opts(outtmpl: str, progress_hooks: list | None = None) -> dict:
        opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': outtmpl,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '32',
            }],
            'extractaudio': True,
            'audioformat': 'm4a',
            'audioquality': 0,
        }
        if progress_hooks:
            opts['progress_hooks'] = progress_hooks
        if ConfigManager().tubio.cookie_path.exists() and not ConfigManager().debug_mode:
            logging.info(f"Using cookie file: {ConfigManager().tubio.cookie_path}")
            opts['cookiefile'] = str(ConfigManager().tubio.cookie_path)
        if ConfigManager().debug_mode:
            opts['nocheckcertificate'] = True
        return opts

    @staticmethod
    def _with_youtube_player_client(ydl_opts: dict, player_client: str) -> dict:
        retry_opts = {
            **ydl_opts,
            'extractor_args': {
                **ydl_opts.get('extractor_args', {}),
                'youtube': {
                    **ydl_opts.get('extractor_args', {}).get('youtube', {}),
                    'player_client': [player_client],
                },
            },
        }
        return retry_opts

    @staticmethod
    def download_audio_file(video_id: str, ydl_opts: dict) -> None:
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            fallback_client = ConfigManager().tubio.youtube_403_fallback_player_client
            if "HTTP Error 403" not in str(e) or not fallback_client:
                raise
            logging.warning(
                "YouTube media download returned HTTP 403; retrying with player_client=%s",
                fallback_client,
            )
            retry_opts = AudioDownloader._with_youtube_player_client(ydl_opts, fallback_client)
            with yt_dlp.YoutubeDL(retry_opts) as ydl:
                ydl.download([url])

    @staticmethod
    def download_youtube_audio(video_id: str, title: str, user: User, crc: int|None = None) -> None:
        logging.info(f"Tubio downloading video_id:={video_id}")
        temp_file = DataInterface().find_avail_temp_file_path(ext=".%(ext)s")
        temp_file.parent.mkdir(parents=True, exist_ok=True)

        progress = DownloadProgress(video_id)

        def progress_hook(d):
            if d['status'] == 'downloading':
                progress.status = "downloading"
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    progress.percent = (downloaded / total) * 100
            elif d['status'] == 'finished':
                progress.status = "processing"
                progress.percent = 100

        ydl_opts = AudioDownloader._build_ydl_opts(temp_file.as_posix(), [progress_hook])

        try:
            AudioDownloader.download_audio_file(video_id, ydl_opts)
            progress.status = "complete"
        except Exception as e:
            progress.status = "error"
            progress.error = str(e)
            raise
        temp_file = temp_file.with_suffix('.m4a')
        if not crc:
            with open(temp_file, 'rb') as f:
                crc = binascii.crc32(f.read())

        # Download and cache thumbnail
        AudioDownloader.download_thumbnail(video_id, crc)

        with DataInterface().edit_metadata() as metadata:
            metadata.audios[crc] = AudioMetadata(
                crc=crc, title=title, yt_video_id=video_id, is_cached=True,
                source_url=f"https://www.youtube.com/watch?v={video_id}"
            )
            metadata.get_user(user.id).add_to_playlist(crc)
        output_file = DataInterface().get_audio_path(crc)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        os.rename(temp_file, output_file)
