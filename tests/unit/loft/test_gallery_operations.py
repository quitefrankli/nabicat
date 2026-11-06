"""Focused Loft tests."""

from io import BytesIO
from pathlib import Path
from web_app.config import ConfigManager
from web_app.errors import APIError
from web_app.loft.data_interface import DataInterface
from web_app.loft.data_interface import VideoInfo
from web_app.users import User
from werkzeug.datastructures import FileStorage
import json
import pytest
import subprocess

from .helpers import _av_mov_file_storage, _mov_file_storage, _mp4_file_storage, _png_file_storage, _portrait_mov_file_storage, _post_meta


class TestGalleryUploadAndDelete:
    def test_gallery_video_resets_shared_post_media_margin(self):
        css = (
            Path(__file__).parents[3]
            / "web_app"
            / "loft"
            / "static"
            / "loft.css"
        ).read_text()

        gallery_video_rule = css.split(".loft-gallery-video video {", 1)[1].split(
            "}", 1
        )[0]
        assert "margin: 0;" in gallery_video_rule

    def test_expanded_gallery_video_keeps_controls_inside_viewport(self):
        css = (
            Path(__file__).parents[3]
            / "web_app"
            / "loft"
            / "static"
            / "loft.css"
        ).read_text()

        expanded_rule = css.split(".loft-gallery-video.is-expanded {", 1)[1].split(
            "}", 1
        )[0]
        assert "box-sizing: border-box;" in expanded_rule
        assert ".loft-gallery-video.is-expanded .loft-video-sound {" in css
        assert "env(safe-area-inset-bottom)" in css

    def test_add_then_delete_image_generates_thumb_and_updates_state(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)

        proj, post = di.create_gallery_post(alice, "Album", "Trip", "desc")
        post_dir = projects_dir / proj / post

        n = di.add_gallery_images(alice, proj, post, [_png_file_storage("photo.png")])
        assert n == 1
        assert not (post_dir / "photo.png").exists()
        assert (post_dir / "photo.webp").exists()
        assert not (post_dir / "thumbs").exists()

        assert not (post_dir / "gallery.json").exists()
        assert not (post_dir / "index.html").exists()
        gallery = _post_meta(projects_dir, proj, post)
        assert "images" not in gallery
        assert gallery["template-data"]["items"] == [
            {"type": "image", "filename": "photo.webp"}
        ]

        rendered = di.get_post_content(proj, post)
        assert 'data-full="photo.webp"' in rendered
        assert 'data-gallery-src="photo.webp"' in rendered
        assert 'src="data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA="' in rendered
        assert "alice" in rendered

        di.delete_gallery_image(proj, post, "photo.webp")
        assert not (post_dir / "photo.png").exists()
        assert not (post_dir / "photo.webp").exists()
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == []

    @pytest.mark.ffmpeg
    def test_add_then_delete_video_transcodes_and_updates_state(self, projects_dir, tmp_path):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)

        proj, post = di.create_gallery_post(alice, "Album", "Clips", "desc")
        post_dir = projects_dir / proj / post

        n = di.add_gallery_media(alice, proj, post, [_mp4_file_storage(tmp_path)])
        assert n == 1
        assert (post_dir / "clip.mp4").exists()
        assert not (post_dir / ".upload-clip.mp4").exists()
        assert not (post_dir / "thumbs").exists()

        gallery = _post_meta(projects_dir, proj, post)
        assert "images" not in gallery
        assert gallery["template-data"]["items"] == [
            {"type": "video", "filename": "clip.mp4", "has_audio": False}
        ]

        rendered = di.get_post_content(proj, post)
        assert (
            '<video data-loft-video data-video-expand '
            'autoplay loop muted playsinline preload="metadata">'
        ) in rendered
        assert "controls" not in rendered
        assert "loft-video-sound" not in rendered
        assert '<source src="clip.mp4" type="video/mp4">' in rendered

        di.delete_gallery_media(proj, post, "clip.mp4")
        assert not (post_dir / "clip.mp4").exists()
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == []

    @pytest.mark.ffmpeg
    def test_phone_video_upload_is_normalized_to_mp4(self, projects_dir, tmp_path):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Phone Clip", "")
        post_dir = projects_dir / proj / post

        n = di.add_gallery_media(alice, proj, post, [_mov_file_storage(tmp_path)])
        assert n == 1
        assert (post_dir / "iphone.mp4").exists()
        assert not (post_dir / "iphone.MOV").exists()
        assert not (post_dir / "thumbs").exists()

        gallery = _post_meta(projects_dir, proj, post)
        assert gallery["template-data"]["items"] == [
            {"type": "video", "filename": "iphone.mp4", "has_audio": False}
        ]

    @pytest.mark.ffmpeg
    def test_video_with_audio_preserves_one_audio_stream_and_renders_sound_control(
        self, projects_dir, tmp_path
    ):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Sound Clip", "")
        post_dir = projects_dir / proj / post

        assert di.add_gallery_media(alice, proj, post, [_av_mov_file_storage(tmp_path)]) == 1

        output = post_dir / "sound.mp4"
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                "-of", "json", str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        streams = json.loads(probe.stdout)["streams"]
        assert [stream["codec_type"] for stream in streams] == ["video", "audio"]
        assert streams[1]["codec_name"] == "aac"
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == [
            {"type": "video", "filename": "sound.mp4", "has_audio": True}
        ]
        rendered = di.get_post_content(proj, post)
        assert 'class="loft-video-sound"' in rendered
        assert 'aria-label="Unmute video"' in rendered

    @pytest.mark.parametrize(("audio_index", "expects_audio"), [(None, False), (3, True)])
    def test_transcode_maps_only_selected_media_streams(
        self, projects_dir, tmp_path, monkeypatch, audio_index, expects_audio
    ):
        commands = []
        monkeypatch.setattr(
            DataInterface,
            "_run_media_command",
            staticmethod(
                lambda cmd, timeout_s, error_message: (
                    commands.append(cmd)
                    or subprocess.CompletedProcess(cmd, 0, "", "")
                )
            ),
        )

        DataInterface()._transcode_video(
            tmp_path / "source.mov",
            tmp_path / "output.mp4",
            "source.mov",
            VideoInfo(duration=0.5, video_index=0, audio_index=audio_index),
        )

        cmd = commands[0]
        mapped = [cmd[index + 1] for index, value in enumerate(cmd[:-1]) if value == "-map"]
        assert mapped == (["0:0", "0:3"] if expects_audio else ["0:0"])
        assert ("-an" in cmd) is not expects_audio
        assert "0:a?" not in cmd
        assert "-dn" in cmd
        assert "-sn" in cmd
        assert cmd[cmd.index("-protocol_whitelist") + 1] == "file"
        assert "-format_whitelist" in cmd
        assert cmd[cmd.index("-map_metadata") + 1] == "-1"
        assert cmd[cmd.index("-map_chapters") + 1] == "-1"
        assert cmd[cmd.index("-t") + 1] == str(
            ConfigManager().loft.gallery_video_max_duration_s
        )
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
        assert cmd[cmd.index("-f") + 1] == "mp4"

    @pytest.mark.ffmpeg
    def test_audio_only_upload_is_rejected_and_cleaned_up(self, projects_dir, tmp_path):
        audio_path = tmp_path / "voice.m4a"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "0.2", "-c:a", "aac", str(audio_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Audio Only", "")
        upload = FileStorage(
            stream=BytesIO(audio_path.read_bytes()),
            filename="fake-video.mp4",
            content_type="video/mp4",
        )

        with pytest.raises(APIError, match="video stream"):
            di.add_gallery_media(alice, proj, post, [upload])

        assert list((projects_dir / proj / post).iterdir()) == []
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == []

    def test_gallery_reorder_validates_exact_filenames(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Order", "")
        di.add_gallery_images(
            alice, proj, post,
            [_png_file_storage("one.png"), _png_file_storage("two.png"), _png_file_storage("three.png")],
        )

        di.update_gallery_meta(
            proj, post, "Order", "", ["three.webp", "one.webp", "two.webp"]
        )
        assert [
            item["filename"]
            for item in _post_meta(projects_dir, proj, post)["template-data"]["items"]
        ] == ["three.webp", "one.webp", "two.webp"]

        with pytest.raises(APIError, match="Media order"):
            di.update_gallery_meta(proj, post, "Order", "", ["one.webp", "missing.webp"])
        assert [
            item["filename"]
            for item in _post_meta(projects_dir, proj, post)["template-data"]["items"]
        ] == ["three.webp", "one.webp", "two.webp"]

    @pytest.mark.ffmpeg
    def test_portrait_video_transcode_outputs_even_dimensions(self, projects_dir, tmp_path):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Portrait Clip", "")
        post_dir = projects_dir / proj / post

        n = di.add_gallery_media(alice, proj, post, [_portrait_mov_file_storage(tmp_path)])

        assert n == 1
        output = post_dir / "portrait.mp4"
        assert output.exists()
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        width, height = [int(value) for value in result.stdout.strip().split(",")]
        assert width % 2 == 0
        assert height % 2 == 0
        assert height == 720

    def test_video_with_missing_duration_metadata_is_rejected(self, projects_dir, tmp_path, monkeypatch):
        di = DataInterface()

        def fake_run(cmd, timeout_s, error_message):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    '{"format":{"format_name":"mov,mp4,m4a,3gp,3g2,mj2"},'
                    '"streams":[{"index":0,"codec_type":"video",'
                    '"codec_name":"h264","width":320,"height":240}]}'
                ),
                stderr="",
            )

        monkeypatch.setattr(DataInterface, "_run_media_command", staticmethod(fake_run))
        with pytest.raises(APIError, match="duration"):
            di._validate_video(tmp_path / "upload.mp4", "phone-video.mp4")

    def test_video_processing_error_uses_original_filename(self, projects_dir, tmp_path, monkeypatch):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Broken Clip", "")

        def fake_run(cmd, timeout_s, error_message):
            if cmd[0] == "ffprobe":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=(
                        '{"format":{"duration":"0.5",'
                        '"format_name":"mov,mp4,m4a,3gp,3g2,mj2"},"streams":'
                        '[{"index":0,"codec_type":"video","codec_name":"h264",'
                        '"duration":"0.5","width":320,"height":240}]}'
                    ),
                    stderr="",
                )
            raise APIError(error_message)

        monkeypatch.setattr(DataInterface, "_run_media_command", staticmethod(fake_run))
        bad_video = FileStorage(
            stream=BytesIO(b"not a video"),
            filename="broken.MOV",
            content_type="video/quicktime",
        )

        with pytest.raises(APIError) as exc:
            di.add_gallery_media(alice, proj, post, [bad_video])

        assert "broken.MOV" in str(exc.value)
        assert "tmp" not in str(exc.value)

    @pytest.mark.ffmpeg
    def test_video_over_duration_limit_is_rejected(self, projects_dir, tmp_path, monkeypatch):
        from web_app.config import ConfigManager
        monkeypatch.setattr(ConfigManager().loft, "gallery_video_max_duration_s", 0)

        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Long Clip", "")
        post_dir = projects_dir / proj / post

        with pytest.raises(APIError, match="too long"):
            di.add_gallery_media(alice, proj, post, [_mp4_file_storage(tmp_path)])

        assert not (post_dir / "clip.mp4").exists()
        assert not (post_dir / ".upload-clip.mp4").exists()
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == []

    def test_quota_blocks_uploads_over_limit(self, projects_dir, monkeypatch):
        from web_app.config import ConfigManager
        monkeypatch.setattr(ConfigManager().loft, "non_admin_quota_bytes", 1)

        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "tiny", "Holiday", "")

        # Quota is checked against the persisted WebP derivative, not an original.
        with pytest.raises(APIError, match="quota"):
            di.add_gallery_images(alice, proj, post, [_png_file_storage("big.png", size=(200, 200))])

    @pytest.mark.ffmpeg
    def test_non_media_bytes_with_image_extension_are_rejected(self, projects_dir):
        """A misleading extension must not allow invalid bytes to be published."""
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "trip", "Album", "")
        post_dir = projects_dir / proj / post

        evil = FileStorage(
            stream=BytesIO(b"<script>alert(1)</script>"),
            filename="evil.png",
            content_type="image/png",
        )
        with pytest.raises(APIError, match="evil.png"):
            di.add_gallery_images(alice, proj, post, [evil])

        # Critical: the original must not be left on disk and the gallery must
        # not list it.
        assert not (post_dir / "evil.png").exists()
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == []

    def test_oversized_title_rejected(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        with pytest.raises(APIError, match="too long"):
            di.create_markdown_post(alice, "blog", "x" * 5000, "body")


