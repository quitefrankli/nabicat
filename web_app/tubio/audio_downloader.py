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

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None

                duration = info.get('duration', 0)
                vid_length = timedelta(seconds=duration)
                max_length = ConfigManager().tudio_max_video_length

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
    def search_youtube(query: str, cached_yt_vid_ids: Set[str]) -> List[dict]:
        """
        Search YouTube for videos matching the query and return a list of video info dicts.
        Extracts video_id, title, description, view count, date, and length.
        If query is a direct YouTube URL, returns only that video.

        Raises:
            VideoTooLongError: If a direct URL video exceeds the maximum allowed length.
        """
        # Check if query is a direct YouTube URL
        video_id = AudioDownloader.extract_video_id(query)
        if video_id:
            logging.info(f"Direct YouTube URL detected, fetching video: {video_id}")
            # Let VideoTooLongError propagate for direct URLs
            video_info = AudioDownloader.get_video_info(video_id, cached_yt_vid_ids)
            return [video_info] if video_info else []

        params = {"search_query": query}
        response = requests.get(AudioDownloader.YOUTUBE_SEARCH_URL, params=params)
        response.raise_for_status()
        html = response.text
        # Extract ytInitialData JSON
        initial_data_match = re.search(r'var ytInitialData = (\{.*?\});', html, re.DOTALL)
        if not initial_data_match:
            return []
        try:
            data = json.loads(initial_data_match.group(1))
        except Exception:
            logging.exception("Failed to parse YouTube search results")
            return []
        # Traverse the JSON to get videoRenderer items
        sections = data.get('contents', {}) \
            .get('twoColumnSearchResultsRenderer', {}) \
            .get('primaryContents', {}) \
            .get('sectionListRenderer', {}) \
            .get('contents', [])
        
        logging.info(f"Searching YouTube with query: {query}")
        results = []
        for section in sections:
            items = section.get('itemSectionRenderer', {}).get('contents', [])
            for item in items:
                if len(results) > ConfigManager().tudio_max_results:
                    return results
                video = item.get('videoRenderer')
                if not video:
                    continue
                length_txt = video.get('lengthText', {}).get('simpleText', '')
                if not length_txt:
                    continue
                vid_length = AudioDownloader.get_vid_length(length_txt)
                if vid_length > ConfigManager().tudio_max_video_length:
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

                results.append({
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
        return results

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
    def download_youtube_audio(video_id: str, title: str, user: User, crc: int|None = None) -> None:
        logging.info(f"Tubio downloading video_id:={video_id}")
        url = f"https://www.youtube.com/watch?v={video_id}"
        temp_file = DataInterface().find_avail_temp_file_path(ext=".%(ext)s")
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': temp_file.as_posix(),
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '32',  # lowest quality for cost

            }],
            'extractaudio': True,
            'audioformat': 'm4a',
            'audioquality': 0,  # best effort for lowest
        }

        if ConfigManager().tubio_cookie_path.exists() and not ConfigManager().debug_mode:
            # Use cookies only if not in debug mode
            logging.info(f"Using cookie file: {ConfigManager().tubio_cookie_path}")
            ydl_opts['cookiefile'] = str(ConfigManager().tubio_cookie_path)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        temp_file = temp_file.with_suffix('.m4a')
        if not crc:
            with open(temp_file, 'rb') as f:
                crc = binascii.crc32(f.read())

        # Download and cache thumbnail
        AudioDownloader.download_thumbnail(video_id, crc)

        DataInterface().save_audio_metadata(AudioMetadata(
            crc=crc, title=title, yt_video_id=video_id, is_cached=True
        ))
        user_metadata = DataInterface().get_user_metadata(user)
        user_metadata.add_to_playlist(crc)
        DataInterface().save_user_metadata(user, user_metadata)
        output_file = DataInterface().get_audio_path(crc)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        os.rename(temp_file, output_file)