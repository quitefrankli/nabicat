import io
import json
from unittest.mock import Mock

import pytest

from scripts.youtube_direct import download


def response(body, content_type="text/html"):
    result = io.BytesIO(body)
    result.headers = {"Content-Type": content_type}
    result.status = 200
    return result


def player_page(player):
    return response(('var ytInitialPlayerResponse = ' + json.dumps(player) + ';').encode())


def test_download_audio_from_shorts_without_overwriting(tmp_path):
    opener = Mock()
    opener.open.side_effect = [player_page({
        "playabilityStatus": {"status": "OK"},
        "streamingData": {"adaptiveFormats": [{
            "itag": 140, "mimeType": "audio/mp4", "bitrate": 128000,
            "url": "https://example.googlevideo.com/videoplayback", "contentLength": "5",
        }]},
    }), response(b"audio", "audio/mp4")]
    target = tmp_path / "audio.m4a"
    download("https://www.youtube.com/shorts/5RW4M_-jA5c", target, opener)
    assert target.read_bytes() == b"audio"
    assert opener.open.call_args_list[0].args[0].full_url.endswith("watch?v=5RW4M_-jA5c")
    with pytest.raises(FileExistsError):
        download("5RW4M_-jA5c", target, opener)
    assert target.read_bytes() == b"audio"


@pytest.mark.parametrize("player, message", [
    ({"playabilityStatus": {"status": "LOGIN_REQUIRED", "reason": "Sign in to confirm you’re not a bot"}}, "Sign in"),
    ({"playabilityStatus": {"status": "OK"}, "streamingData": {"adaptiveFormats": [{"signatureCipher": "secret", "mimeType": "audio/mp4"}]}}, "cipher"),
])
def test_unavailable_media_leaves_no_output(tmp_path, player, message):
    opener = Mock()
    opener.open.return_value = player_page(player)
    target = tmp_path / "audio.m4a"
    with pytest.raises(ValueError, match=message):
        download("5RW4M_-jA5c", target, opener)
    assert not target.exists()
    assert opener.open.call_count == 1


def test_truncated_media_removes_partial_output(tmp_path):
    opener = Mock()
    opener.open.side_effect = [player_page({
        "playabilityStatus": {"status": "OK"},
        "streamingData": {"adaptiveFormats": [{
            "mimeType": "audio/mp4", "url": "https://example.googlevideo.com/media",
            "contentLength": "20",
        }]},
    }), response(b"short", "audio/mp4")]
    target = tmp_path / "audio.m4a"
    with pytest.raises(ValueError, match="Incomplete"):
        download("5RW4M_-jA5c", target, opener)
    assert not target.exists()
