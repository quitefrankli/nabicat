"""Focused Loft tests."""

from web_app.errors import APIError
from web_app.loft.data_interface import DataInterface
from web_app.users import User
import json
import pytest
import shutil

from .helpers import _make_post, _post_meta


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

    def test_unlisted_posts_are_sidebar_hidden_except_for_admins(self, projects_dir):
        _make_post(projects_dir, "ideas", "public")
        _make_post(projects_dir, "ideas", "private")
        _make_post(projects_dir, "private-project", "only-post")
        meta = json.loads((projects_dir.parent / "meta.json").read_text())
        meta["projects"]["ideas"]["posts"]["private"]["visibility"] = "unlisted"
        meta["projects"]["private-project"]["posts"]["only-post"]["visibility"] = "unlisted"
        (projects_dir.parent / "meta.json").write_text(json.dumps(meta))

        normal = User("alice", "x", "fa", is_admin=False)
        elevated = User("eve", "x", "fe", is_elevated=True)
        admin = User("root", "x", "fr", is_admin=True)

        assert DataInterface().get_posts_by_project()[0].posts == ["public"]
        assert DataInterface().get_posts_by_project(normal)[0].posts == ["public"]
        assert [p.name for p in DataInterface().get_posts_by_project()] == ["ideas"]
        assert DataInterface().get_posts_by_project(elevated)[0].posts == ["public"]
        assert DataInterface().get_posts_by_project(admin)[0].posts == ["public", "private"]
        assert "<h1>test</h1>" in DataInterface().get_post_content("ideas", "private")

    def test_restricted_visibility_requires_elevated_access(self, projects_dir):
        _make_post(projects_dir, "ideas", "public")
        _make_post(projects_dir, "ideas", "private")
        meta = json.loads((projects_dir.parent / "meta.json").read_text())
        meta["projects"]["ideas"]["posts"]["private"]["visibility"] = "restricted"
        (projects_dir.parent / "meta.json").write_text(json.dumps(meta))

        normal = User("alice", "x", "fa", is_admin=False)
        elevated = User("eve", "x", "fe", is_elevated=True)

        assert DataInterface().get_posts_by_project(normal)[0].posts == ["public"]
        assert DataInterface().get_posts_by_project(elevated)[0].posts == ["public", "private"]
        assert DataInterface().user_can_view(normal, "ideas", "private") is False
        assert DataInterface().user_can_view(elevated, "ideas", "private") is True
        assert DataInterface().user_can_view(None, "ideas", "private") is False


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
        post_dir = projects_dir / proj / post
        meta = _post_meta(projects_dir, proj, post)
        assert meta["owner"] == "alice"
        assert meta["type"] == "markdown"
        assert (post_dir / "source.md").read_text() == "Hi **bob**."
        assert not (post_dir / "meta.json").exists()
        assert not (post_dir / "index.html").exists()

        # Rendered body contains the markdown→HTML conversion and the byline
        content = di.get_post_content(proj, post)
        assert "<strong>bob</strong>" in content
        assert 'class="loft-post-author">alice' in content

        # Authorization: owner + admin allowed; stranger denied; legacy (no owner) is admin-only
        assert di.user_can_edit(alice, proj, post) is True
        assert di.user_can_edit(admin, proj, post) is True
        assert di.user_can_edit(bob, proj, post) is False

        _make_post(projects_dir, "legacy", "old-post")
        assert di.user_can_edit(admin, "legacy", "old-post") is True
        assert di.user_can_edit(alice, "legacy", "old-post") is False

        di.update_markdown_post(proj, post, "New Title", "Updated.")
        assert (post_dir / "source.md").read_text() == "Updated."
        assert _post_meta(projects_dir, proj, post)["title"] == "New Title"
        assert not (post_dir / "index.html").exists()
        assert "Updated." in di.get_post_content(proj, post)

    def test_duplicate_post_title_raises(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        di.create_markdown_post(alice, "blog", "Same Title", "a")
        with pytest.raises(APIError, match="already exists"):
            di.create_markdown_post(alice, "blog", "Same Title", "b")


class TestLoftBackup:
    def test_backup_copies_meta_and_nested_posts(self, projects_dir, tmp_path):
        """backup_data copies the whole content dir (meta.json + per-post files)
        into a namespaced 'loft/' subdir of the shared backup dir."""
        di = DataInterface()
        _make_post(projects_dir, "blog", "hello", date="2026-01-01")

        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        di.backup_data(backup_dir)

        assert (backup_dir / "loft" / "meta.json").exists()
        assert (backup_dir / "loft" / "projects" / "blog" / "hello" / "index.html").exists()
        # The copied meta still describes the post.
        copied_meta = json.loads((backup_dir / "loft" / "meta.json").read_text())
        assert "hello" in copied_meta["projects"]["blog"]["posts"]

    def test_backup_is_noop_when_no_content_dir(self, projects_dir, tmp_path):
        """A missing content dir must not raise (fresh install, nothing posted)."""
        di = DataInterface()
        shutil.rmtree(di._content_dir)
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        di.backup_data(backup_dir)  # must not raise
        assert not (backup_dir / "loft").exists()

    def test_backup_excludes_raw_gallery_upload_staging(
        self,
        projects_dir,
        tmp_path,
    ):
        di = DataInterface()
        staging = di._content_dir / ".gallery-upload-staging"
        staging.mkdir()
        (staging / "raw-with-private-metadata.source").write_bytes(
            b"raw EXIF/GPS/device metadata"
        )
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()

        di.backup_data(backup_dir)

        assert not (
            backup_dir
            / "loft"
            / ".gallery-upload-staging"
        ).exists()


