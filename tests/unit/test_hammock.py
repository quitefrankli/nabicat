"""Unit tests for Hammock data interface."""

import json
import shutil
import tempfile
from io import BytesIO
from pathlib import Path

import pytest
import web_app.helpers as helpers
from PIL import Image
from werkzeug.datastructures import FileStorage

from web_app.errors import APIError
from web_app.hammock import hammock_api
from web_app.hammock.data_interface import DataInterface
from web_app.users import User


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    d = tmp_path / "hammock" / "projects"
    d.mkdir(parents=True)

    def patched_init(self):
        from markdown_it import MarkdownIt
        self.projects_dir = d
        self._content_dir = d.parent
        self._md = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": True})

    monkeypatch.setattr(DataInterface, "__init__", patched_init)
    return d


def _make_post(projects_dir: Path, project: str, post: str, date: str | None = None):
    post_dir = projects_dir / project / post
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "index.html").write_text("<h1>test</h1>")
    if date is not None:
        (post_dir / "meta.json").write_text(json.dumps({"date": date}))


def _png_file_storage(name: str, size: tuple[int, int] = (40, 40)) -> FileStorage:
    """Return an in-memory PNG FileStorage so we don't need real fixture images."""
    buf = BytesIO()
    Image.new("RGB", size, color=(180, 200, 160)).save(buf, format="PNG")
    buf.seek(0)
    return FileStorage(stream=buf, filename=name, content_type="image/png")


class TestGetPostsByProjectSorting:
    def test_sorts_by_meta_date_descending(self, projects_dir):
        _make_post(projects_dir, "blog", "alpha", date="2024-01-01")
        _make_post(projects_dir, "blog", "beta", date="2024-06-01")
        _make_post(projects_dir, "blog", "gamma", date="2024-03-01")

        result = DataInterface().get_posts_by_project()
        assert result[0].posts == ["beta", "gamma", "alpha"]

    def test_falls_back_to_name_sort_when_no_meta(self, projects_dir):
        _make_post(projects_dir, "blog", "2024-03-zz")
        _make_post(projects_dir, "blog", "2024-01-aa")
        _make_post(projects_dir, "blog", "2024-06-bb")

        result = DataInterface().get_posts_by_project()
        assert result[0].posts == ["2024-06-bb", "2024-03-zz", "2024-01-aa"]

    def test_date_meta_takes_priority_over_name(self, projects_dir):
        _make_post(projects_dir, "blog", "zzz-newest-name", date="2023-01-01")
        _make_post(projects_dir, "blog", "aaa-oldest-name", date="2025-01-01")

        result = DataInterface().get_posts_by_project()
        assert result[0].posts == ["aaa-oldest-name", "zzz-newest-name"]


class TestMarkdownLifecycleAndAuthz:
    def test_markdown_create_render_edit_and_ownership(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        bob = User("bob", "x", "fb", is_admin=False)
        admin = User("root", "x", "fr", is_admin=True)

        proj, post = di.create_markdown_post(alice, "My Blog", "Hello World", "Hi **bob**.")

        # Slug + persistence + meta
        assert proj == "my-blog"
        assert post == "hello-world"
        meta_path = projects_dir / proj / post / "meta.json"
        meta = json.loads(meta_path.read_text())
        assert meta["owner"] == "alice"
        assert meta["template"] == "markdown"
        assert (projects_dir / proj / post / "source.md").read_text() == "Hi **bob**."

        # Rendered body contains the markdown→HTML conversion and the byline
        content = di.get_post_content(proj, post)
        assert "<strong>bob</strong>" in content
        assert 'class="hammock-post-author">alice' in content

        # Authorization: owner + admin allowed; stranger denied; legacy (no owner) is admin-only
        assert di.user_can_edit(alice, proj, post) is True
        assert di.user_can_edit(admin, proj, post) is True
        assert di.user_can_edit(bob, proj, post) is False

        _make_post(projects_dir, "legacy", "old-post")  # no owner field
        assert di.user_can_edit(admin, "legacy", "old-post") is True
        assert di.user_can_edit(alice, "legacy", "old-post") is False

        # Edit updates source.md, regenerates index.html, and bumps the title
        di.update_markdown_post(proj, post, "New Title", "Updated.")
        assert (projects_dir / proj / post / "source.md").read_text() == "Updated."
        assert json.loads(meta_path.read_text())["title"] == "New Title"
        assert "Updated." in di.get_post_content(proj, post)

    def test_duplicate_post_title_raises(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        di.create_markdown_post(alice, "blog", "Same Title", "a")
        with pytest.raises(APIError, match="already exists"):
            di.create_markdown_post(alice, "blog", "Same Title", "b")


class TestGalleryUploadAndDelete:
    def test_add_then_delete_image_generates_thumb_and_updates_state(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)

        proj, post = di.create_gallery_post(alice, "Album", "Trip", "desc")
        post_dir = projects_dir / proj / post

        n = di.add_gallery_images(alice, proj, post, [_png_file_storage("photo.png")])
        assert n == 1
        assert (post_dir / "photo.png").exists()
        assert (post_dir / "thumbs" / "photo.png.webp").exists()

        gallery = json.loads((post_dir / "gallery.json").read_text())
        assert gallery["images"] == ["photo.png"]

        # Rendered HTML references the thumb and the owner byline
        rendered = di.get_post_content(proj, post)
        assert 'src="thumbs/photo.png.webp"' in rendered
        assert "alice" in rendered

        di.delete_gallery_image(proj, post, "photo.png")
        assert not (post_dir / "photo.png").exists()
        assert not (post_dir / "thumbs" / "photo.png.webp").exists()
        assert json.loads((post_dir / "gallery.json").read_text())["images"] == []

    def test_quota_blocks_uploads_over_limit(self, projects_dir, monkeypatch):
        from web_app.config import ConfigManager
        monkeypatch.setattr(ConfigManager(), "hammock_non_admin_quota_bytes", 256)

        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "tiny", "Holiday", "")

        # A 200x200 PNG will exceed the 256-byte cap easily.
        with pytest.raises(APIError, match="quota"):
            di.add_gallery_images(alice, proj, post, [_png_file_storage("big.png", size=(200, 200))])

    def test_non_image_bytes_with_image_extension_are_rejected(self, projects_dir):
        """A file named .png that isn't actually a valid image must NOT be saved or listed."""
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "trip", "Album", "")
        post_dir = projects_dir / proj / post

        evil = FileStorage(
            stream=BytesIO(b"<script>alert(1)</script>"),
            filename="evil.png",
            content_type="image/png",
        )
        with pytest.raises(APIError, match="image"):
            di.add_gallery_images(alice, proj, post, [evil])

        # Critical: the original must not be left on disk and the gallery must
        # not list it.
        assert not (post_dir / "evil.png").exists()
        assert json.loads((post_dir / "gallery.json").read_text())["images"] == []

    def test_oversized_title_rejected(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        with pytest.raises(APIError, match="too long"):
            di.create_markdown_post(alice, "blog", "x" * 5000, "body")


class TestGalleryEditRoute:
    def test_update_post_uploads_selected_images(self, client, projects_dir, monkeypatch):
        if "hammock" not in client.application.blueprints:
            client.application.register_blueprint(hammock_api)
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

        edit_response = client.get(f"/hammock/{proj}/{post}/edit")
        assert b">Upload<" not in edit_response.data

        response = client.post(
            f"/hammock/{proj}/{post}/edit",
            data={
                "title": "Updated Trip",
                "description": "new desc",
                "files": (BytesIO(_png_file_storage("photo.png").read()), "photo.png"),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        assert response.get_json()["redirect_url"] == f"/hammock/{proj}/{post}/"
        post_dir = projects_dir / proj / post
        assert (post_dir / "photo.png").exists()
        assert json.loads((post_dir / "gallery.json").read_text())["images"] == ["photo.png"]
