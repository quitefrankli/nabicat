"""YouTube URL parsing behavior for Tubio."""

import pytest

from web_app.tubio.audio_downloader import AudioDownloader


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "youtu.be/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PLtest",
    "  https://www.youtube.com/watch?v=dQw4w9WgXcQ  ",
])
def test_extract_video_id_from_supported_urls(url):
    assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", [
    "rick astley never gonna give you up",
    "",
    "https://www.youtube.com/watch?v=short",
    "https://vimeo.com/123456789",
])
def test_extract_video_id_rejects_unsupported_urls(url):
    assert AudioDownloader.extract_video_id(url) is None
