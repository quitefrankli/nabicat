"""Focused Loft tests."""

from io import BytesIO
from web_app.config import ConfigManager
from web_app.loft.data_interface import DataInterface
from web_app.users import User
from werkzeug.test import EnvironBuilder
import base64
import gzip
import json
import web_app.auth as helpers
import web_app.loft as loft_module

from .helpers import _make_post, _png_file_storage, _post_meta, _raw_post_archive


class TestGalleryEditRoute:
    def test_new_gallery_rejects_request_over_configured_size_before_parsing(
        self,
        client,
        projects_dir,
        monkeypatch,
    ):
        alice = User("alice", "x", "fa", is_admin=False)
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: alice if username == alice.id else None,
        )
        monkeypatch.setattr(
            ConfigManager().loft,
            "gallery_request_max_bytes",
            1,
        )
        with client.session_transaction() as session:
            session["_user_id"] = alice.id
            session["_fresh"] = True

        response = client.post(
            "/loft/new",
            data={
                "project_new": "Album",
                "title": "Too large",
                "template": "gallery",
                "files": (BytesIO(b"image bytes"), "photo.jpg"),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert response.status_code == 413
        assert "size limit" in response.get_json()["error"]
        assert list(projects_dir.iterdir()) == []

    def test_new_gallery_limits_unknown_length_request_stream(
        self,
        client,
        projects_dir,
        monkeypatch,
    ):
        alice = User("alice", "x", "fa", is_admin=False)
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: alice if username == alice.id else None,
        )
        monkeypatch.setattr(
            ConfigManager().loft,
            "gallery_request_max_bytes",
            1,
        )
        monkeypatch.setitem(
            client.application.config,
            "WTF_CSRF_ENABLED",
            True,
        )
        with client.session_transaction() as session:
            session["_user_id"] = alice.id
            session["_fresh"] = True

        builder = EnvironBuilder(
            path="/loft/new",
            method="POST",
            data={
                "project_new": "Album",
                "title": "Unknown length",
                "template": "gallery",
                "files": (BytesIO(b"image bytes"), "photo.jpg"),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        environ = builder.get_environ()
        environ.pop("CONTENT_LENGTH", None)
        environ["wsgi.input_terminated"] = True

        response = client.open(environ)

        assert response.status_code == 413
        assert "size limit" in response.get_json()["error"]
        assert list(projects_dir.iterdir()) == []

    def test_new_gallery_post_upload_uses_ajax_progress_contract(self, client, projects_dir, monkeypatch):
        alice = User("alice", "x", "fa", is_admin=False)
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: alice if username == alice.id else None,
        )

        with client.session_transaction() as sess:
            sess["_user_id"] = alice.id
            sess["_fresh"] = True

        new_response = client.get("/loft/new")
        assert b"data-gallery-upload-form" in new_response.data
        assert b"data-gallery-upload-progress" in new_response.data
        assert b"looping 720p MP4" in new_response.data
        assert b"Topics" in new_response.data
        assert b"Pick an existing topic" in new_response.data
        assert b"new topic name" in new_response.data
        assert b"Projects" not in new_response.data

        response = client.post(
            "/loft/new",
            data={
                "project_new": "Album",
                "title": "Trip",
                "template": "gallery",
                "description": "desc",
                "files": (BytesIO(_png_file_storage("photo.png").read()), "photo.png"),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        assert response.get_json()["redirect_url"] == "/loft/album/trip/"
        post_dir = projects_dir / "album" / "trip"
        assert not (post_dir / "photo.png").exists()
        assert (post_dir / "photo.webp").exists()
        assert not (post_dir / "thumbs").exists()

    def test_update_post_uploads_selected_images(self, client, projects_dir, monkeypatch):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Trip", "desc")
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: alice if username == alice.id else None,
        )

        with client.session_transaction() as sess:
            sess["_user_id"] = alice.id
            sess["_fresh"] = True

        edit_response = client.get(f"/loft/{proj}/{post}/edit")
        assert b">Upload<" not in edit_response.data

        response = client.post(
            f"/loft/{proj}/{post}/edit",
            data={
                "title": "Updated Trip",
                "description": "new desc",
                "files": (BytesIO(_png_file_storage("photo.png").read()), "photo.png"),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        assert response.get_json()["redirect_url"] == f"/loft/{proj}/{post}/"
        post_dir = projects_dir / proj / post
        assert not (post_dir / "photo.png").exists()
        assert (post_dir / "photo.webp").exists()
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == [
            {"type": "image", "filename": "photo.webp"}
        ]

    def test_edit_gallery_exposes_audio_controls_and_saves_media_order(
        self, client, projects_dir, monkeypatch
    ):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Reorder", "")
        di.add_gallery_images(
            alice, proj, post, [_png_file_storage("one.png"), _png_file_storage("two.png")]
        )
        with di.edit_meta() as store:
            td = store.projects[proj].posts[post].template_data
            td.items.append(type(td.items[0])(type="video", filename="sound.mp4", has_audio=True))
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: alice if username == alice.id else None,
        )
        with client.session_transaction() as sess:
            sess["_user_id"] = alice.id
            sess["_fresh"] = True

        edit_response = client.get(f"/loft/{proj}/{post}/edit")
        assert b"data-reorder-grid" in edit_response.data
        assert b"data-reorder-handle" not in edit_response.data
        assert b"data-reorder-previous" in edit_response.data
        assert b"data-reorder-next" in edit_response.data
        assert b"data-video-sound" in edit_response.data

        response = client.post(
            f"/loft/{proj}/{post}/edit",
            data={
                "title": "Reorder",
                "description": "",
                "media_order": '["sound.mp4","two.webp","one.webp"]',
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert response.status_code == 200
        assert [
            item["filename"]
            for item in _post_meta(projects_dir, proj, post)["template-data"]["items"]
        ] == ["sound.mp4", "two.webp", "one.webp"]

    def test_edit_gallery_rejects_stale_media_order(self, client, projects_dir, monkeypatch):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Reorder", "")
        di.add_gallery_images(alice, proj, post, [_png_file_storage("one.png")])
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: alice if username == alice.id else None,
        )
        with client.session_transaction() as sess:
            sess["_user_id"] = alice.id
            sess["_fresh"] = True

        response = client.post(
            f"/loft/{proj}/{post}/edit",
            data={"title": "Reorder", "description": "", "media_order": '["missing.webp"]'},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert response.status_code == 400
        assert "Media order" in response.get_json()["error"]
        assert _post_meta(projects_dir, proj, post)["title"] == "Reorder"


class TestGalleryAssetDelivery:
    def test_video_asset_supports_byte_ranges_and_correct_mime_type(
        self,
        client,
        projects_dir,
    ):
        data_interface = DataInterface()
        owner = User("alice", "x", "fa", is_admin=False)
        project, post = data_interface.create_gallery_post(
            owner,
            "Album",
            "Range request",
            "",
        )
        video = projects_dir / project / post / "clip.mp4"
        video.write_bytes(b"0123456789")

        response = client.get(
            f"/loft/{project}/{post}/clip.mp4",
            headers={"Range": "bytes=2-5"},
        )

        assert response.status_code == 206
        assert response.data == b"2345"
        assert response.content_type == "video/mp4"
        assert response.headers["Accept-Ranges"] == "bytes"


class TestRestrictedPostRoute:
    def test_restricted_post_direct_url_requires_elevated_user(self, client, projects_dir, monkeypatch):
        _make_post(projects_dir, "ideas", "private")
        meta = json.loads((projects_dir.parent / "meta.json").read_text())
        meta["projects"]["ideas"]["posts"]["private"]["visibility"] = "restricted"
        (projects_dir.parent / "meta.json").write_text(json.dumps(meta))

        assert client.get("/loft/ideas/private/").status_code == 404

        elevated = User("eve", "x", "fe", is_elevated=True)
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: elevated if username == elevated.id else None,
        )
        with client.session_transaction() as sess:
            sess["_user_id"] = elevated.id
            sess["_fresh"] = True

        response = client.get("/loft/ideas/private/")

        assert response.status_code == 200
        assert b"<h1>test</h1>" in response.data


class TestRawPostRoutes:
    def test_upload_persists_unlisted_visibility(
        self,
        client,
        projects_dir,
        monkeypatch,
    ):
        archive = gzip.decompress(base64.b64decode(_raw_post_archive()))
        monkeypatch.setattr(
            loft_module, "parse_upload_request",
            lambda: ({"project": "quitefrankli", "post_name": "quitefrankli",
                      "visibility": "unlisted"}, BytesIO(archive)),
        )

        response = client.post("/loft/api/upload_post")

        assert response.status_code == 200
        assert _post_meta(
            projects_dir,
            "quitefrankli",
            "quitefrankli",
        )["visibility"] == "unlisted"

    def test_upload_rejects_unknown_visibility(
        self,
        client,
        projects_dir,
        monkeypatch,
    ):
        monkeypatch.setattr(
            loft_module, "parse_upload_request",
            lambda: ({"project": "quitefrankli", "post_name": "quitefrankli",
                      "visibility": "secret"}, BytesIO(b"")),
        )

        response = client.post("/loft/api/upload_post")

        assert response.status_code == 400
        assert not (projects_dir / "quitefrankli").exists()

    def test_project_short_url_falls_back_to_same_named_unlisted_post(
        self,
        client,
        projects_dir,
    ):
        _make_post(projects_dir, "profile", "profile")
        meta = json.loads((projects_dir.parent / "meta.json").read_text())
        meta["projects"]["profile"]["posts"]["profile"]["visibility"] = "unlisted"
        (projects_dir.parent / "meta.json").write_text(json.dumps(meta))

        response = client.get("/loft/profile/")

        assert response.status_code == 302
        assert response.location.endswith(
            "/loft/profile/profile/"
        )

    def test_unlisted_post_emits_noindex_and_visibility_notice(self, client, projects_dir):
        _make_post(projects_dir, "profile", "profile")
        meta = json.loads((projects_dir.parent / "meta.json").read_text())
        meta["projects"]["profile"]["posts"]["profile"]["visibility"] = "unlisted"
        (projects_dir.parent / "meta.json").write_text(json.dumps(meta))

        response = client.get("/loft/profile/profile/")

        assert response.status_code == 200
        assert b'<meta name="robots" content="noindex">' in response.data
        assert b"This post is unlisted" in response.data
        assert b"can only be accessed with its direct link" in response.data
        assert b"does not appear in the Loft sidebar" in response.data


