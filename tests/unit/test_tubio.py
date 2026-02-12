import pytest

from unittest.mock import Mock, patch, MagicMock
from datetime import timedelta

from web_app.tubio.audio_downloader import AudioDownloader, VideoTooLongError


class TestExtractVideoId:
    """Tests for YouTube URL detection and video ID extraction."""

    def test_standard_watch_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_watch_url_without_www(self):
        url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_watch_url_http(self):
        url = "http://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_watch_url_no_protocol(self):
        url = "youtube.com/watch?v=dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_short_url(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_short_url_no_protocol(self):
        url = "youtu.be/dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_shorts_url(self):
        url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_embed_url(self):
        url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_url_with_extra_params(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PLtest"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_url_with_whitespace(self):
        url = "  https://www.youtube.com/watch?v=dQw4w9WgXcQ  "
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_not_a_url_returns_none(self):
        query = "rick astley never gonna give you up"
        assert AudioDownloader.extract_video_id(query) is None

    def test_empty_string_returns_none(self):
        assert AudioDownloader.extract_video_id("") is None

    def test_invalid_video_id_length(self):
        # Video IDs must be exactly 11 characters
        url = "https://www.youtube.com/watch?v=short"
        assert AudioDownloader.extract_video_id(url) is None

    def test_other_website_returns_none(self):
        url = "https://vimeo.com/123456789"
        assert AudioDownloader.extract_video_id(url) is None


class TestGetVideoInfo:
    """Tests for fetching video info from YouTube."""

    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    @patch('web_app.tubio.audio_downloader.ConfigManager')
    def test_get_video_info_success(self, mock_config, mock_ydl_class):
        mock_config.return_value.tudio_max_video_length = timedelta(minutes=30)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'title': 'Test Video',
            'duration': 180,  # 3 minutes
            'view_count': 1000000,
            'upload_date': '20240101',
            'description': 'Test description',
            'thumbnail': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg',
        }
        mock_ydl_class.return_value = mock_ydl

        result = AudioDownloader.get_video_info('dQw4w9WgXcQ', set())

        assert result is not None
        assert result['video_id'] == 'dQw4w9WgXcQ'
        assert result['title'] == 'Test Video'
        assert result['length'] == '3:00'
        assert result['cached'] is False
        assert result['thumbnail_url'] == 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg'

    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    @patch('web_app.tubio.audio_downloader.ConfigManager')
    def test_get_video_info_cached(self, mock_config, mock_ydl_class):
        mock_config.return_value.tudio_max_video_length = timedelta(minutes=30)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'title': 'Test Video',
            'duration': 180,
            'view_count': 1000000,
            'upload_date': '20240101',
            'description': 'Test description',
        }
        mock_ydl_class.return_value = mock_ydl

        result = AudioDownloader.get_video_info('dQw4w9WgXcQ', {'dQw4w9WgXcQ'})

        assert result is not None
        assert result['cached'] is True

    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    @patch('web_app.tubio.audio_downloader.ConfigManager')
    def test_get_video_info_too_long_raises_exception(self, mock_config, mock_ydl_class):
        mock_config.return_value.tudio_max_video_length = timedelta(minutes=10)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'title': 'Long Video',
            'duration': 3600,  # 1 hour - exceeds max
        }
        mock_ydl_class.return_value = mock_ydl

        with pytest.raises(VideoTooLongError) as exc_info:
            AudioDownloader.get_video_info('dQw4w9WgXcQ', set())

        assert exc_info.value.video_id == 'dQw4w9WgXcQ'
        assert exc_info.value.duration == timedelta(hours=1)
        assert exc_info.value.max_duration == timedelta(minutes=10)

    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    def test_get_video_info_error(self, mock_ydl_class):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.side_effect = Exception("Video not found")
        mock_ydl_class.return_value = mock_ydl

        result = AudioDownloader.get_video_info('invalidid12', set())

        assert result is None

    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    @patch('web_app.tubio.audio_downloader.ConfigManager')
    def test_get_video_info_thumbnail_from_thumbnails_array(self, mock_config, mock_ydl_class):
        """Test that thumbnail is extracted from thumbnails array if main thumbnail is missing."""
        mock_config.return_value.tudio_max_video_length = timedelta(minutes=30)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'title': 'Test Video',
            'duration': 180,
            'thumbnails': [
                {'url': 'https://example.com/small.jpg'},
                {'url': 'https://example.com/medium.jpg'},
                {'url': 'https://example.com/large.jpg'},
            ],
        }
        mock_ydl_class.return_value = mock_ydl

        result = AudioDownloader.get_video_info('dQw4w9WgXcQ', set())

        assert result is not None
        # Should use the last (highest quality) thumbnail
        assert result['thumbnail_url'] == 'https://example.com/large.jpg'


class TestDownloadThumbnail:
    """Tests for thumbnail download and caching."""

    @patch('web_app.tubio.audio_downloader.DataInterface')
    @patch('web_app.tubio.audio_downloader.requests.get')
    def test_download_thumbnail_success(self, mock_get, mock_di):
        from pathlib import Path
        mock_response = Mock()
        mock_response.content = b'fake_image_data'
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        mock_di_instance = Mock()
        mock_thumbnail_path = Path('/fake/path/12345.jpg')
        mock_di_instance.get_thumbnail_path.return_value = mock_thumbnail_path
        mock_di.return_value = mock_di_instance

        result = AudioDownloader.download_thumbnail('dQw4w9WgXcQ', 12345)

        assert result == mock_thumbnail_path
        mock_di_instance.save_thumbnail.assert_called_once_with(12345, b'fake_image_data')

    @patch('web_app.tubio.audio_downloader.DataInterface')
    @patch('web_app.tubio.audio_downloader.requests.get')
    def test_download_thumbnail_failure(self, mock_get, mock_di):
        mock_get.side_effect = Exception("Network error")

        result = AudioDownloader.download_thumbnail('dQw4w9WgXcQ', 12345)

        assert result is None

    @patch('web_app.tubio.audio_downloader.DataInterface')
    @patch('web_app.tubio.audio_downloader.requests.get')
    def test_download_thumbnail_http_error(self, mock_get, mock_di):
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")
        mock_get.return_value = mock_response

        result = AudioDownloader.download_thumbnail('invalidid', 12345)

        assert result is None


class TestSearchYoutubeWithDirectUrl:
    """Tests for search_youtube handling direct URLs."""

    @patch.object(AudioDownloader, 'get_video_info')
    def test_search_with_direct_url_returns_single_result(self, mock_get_info):
        mock_get_info.return_value = {
            'video_id': 'dQw4w9WgXcQ',
            'title': 'Test Video',
            'length': '3:00',
            'cached': False,
            'thumbnail_url': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg',
        }

        results = AudioDownloader.search_youtube(
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            set()
        )

        assert len(results) == 1
        assert results[0]['video_id'] == 'dQw4w9WgXcQ'
        assert results[0]['thumbnail_url'] == 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg'
        mock_get_info.assert_called_once_with('dQw4w9WgXcQ', set())

    @patch.object(AudioDownloader, 'get_video_info')
    def test_search_with_direct_url_video_not_found(self, mock_get_info):
        mock_get_info.return_value = None

        results = AudioDownloader.search_youtube(
            'https://www.youtube.com/watch?v=invalidid12',
            set()
        )

        assert results == []

    @patch('web_app.tubio.audio_downloader.requests.get')
    def test_search_with_regular_query_does_normal_search(self, mock_get):
        mock_response = Mock()
        mock_response.text = 'var ytInitialData = {};'
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        results = AudioDownloader.search_youtube('rick astley', set())

        # Should have called requests.get for normal search
        mock_get.assert_called_once()
        assert 'search_query' in str(mock_get.call_args)

    @patch.object(AudioDownloader, 'get_video_info')
    def test_search_with_direct_url_raises_video_too_long_error(self, mock_get_info):
        """Test that VideoTooLongError propagates when direct URL video is too long."""
        mock_get_info.side_effect = VideoTooLongError(
            'dQw4w9WgXcQ',
            timedelta(hours=2),
            timedelta(minutes=30)
        )

        with pytest.raises(VideoTooLongError) as exc_info:
            AudioDownloader.search_youtube(
                'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                set()
            )

        assert exc_info.value.video_id == 'dQw4w9WgXcQ'
        assert 'too long' in str(exc_info.value)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
