"""Focused Loft tests."""

from io import BytesIO
from web_app.config import ConfigManager
from web_app.loft import loft_api
from web_app.loft.data_interface import DataInterface
from web_app.users import User
import json
import pytest
import web_app.auth as helpers
import web_app.loft as loft_module

from .helpers import _make_post


class TestLoftFailureAudit:
    @pytest.mark.parametrize("data,reason", [
        ({"template": "gallery", "title": "private title"}, "topic_required"),
        ({"project_new": "private topic", "title": "private title"}, "invalid_template"),
        ({"project_new": "private topic", "template": "gallery"}, "title_required"),
    ])
    def test_creation_rejections_are_audited_without_content(
        self, client, projects_dir, monkeypatch, caplog, data, reason,
    ):
        alice = User("alice", "x", "fa", is_admin=False)
        monkeypatch.setattr(helpers.login_manager, "_user_callback", lambda _: alice)
        with client.session_transaction() as session:
            session["_user_id"] = alice.id
        response = client.post("/loft/new", data=data,
                               headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 400
        events = [json.loads(record.message) for record in caplog.records
                  if '"event": "loft.operation_rejected"' in record.message]
        assert len(events) == 1
        assert events[0]["reason"] == reason
        assert events[0]["operation"] == "new_post"
        assert events[0]["user"] == "alice"
        assert "private" not in caplog.text

    def test_csrf_rejection_is_audited(self, client, monkeypatch, caplog):
        monkeypatch.setitem(client.application.config, "WTF_CSRF_ENABLED", True)
        response = client.post("/loft/new", data={"title": "private title"})
        assert response.status_code == 400
        assert '"reason": "csrf_failed"' in caplog.text
        assert '"error_type": "CSRFError"' in caplog.text
        assert '"csrf_reason": "token_missing"' in caplog.text
        assert "private title" not in caplog.text

    def test_duplicate_title_has_safe_reason(self, client, projects_dir, monkeypatch, caplog):
        alice = User("alice", "x", "fa", is_admin=False)
        monkeypatch.setattr(helpers.login_manager, "_user_callback", lambda _: alice)
        DataInterface().create_gallery_post(alice, "album", "private title", "")
        with client.session_transaction() as session:
            session["_user_id"] = alice.id
        response = client.post("/loft/new", data={"project_existing": "album",
            "title": "private title", "template": "gallery"},
            headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 400
        assert '"reason": "post_already_exists"' in caplog.text
        assert "private title" not in caplog.text

    def test_upload_count_limit_is_audited(self, client, projects_dir, monkeypatch, caplog):
        alice = User("alice", "x", "fa", is_admin=False)
        monkeypatch.setattr(helpers.login_manager, "_user_callback", lambda _: alice)
        monkeypatch.setattr(ConfigManager().loft, "gallery_max_files_per_upload", 0)
        with client.session_transaction() as session:
            session["_user_id"] = alice.id
        response = client.post("/loft/new", data={"project_new": "album",
            "title": "private title", "template": "gallery",
            "files": (BytesIO(b"secret bytes"), "private-file.png")},
            headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 400
        assert '"reason": "too_many_media_files"' in caplog.text
        assert '"stage": "add_media"' in caplog.text
        assert "private title" not in caplog.text
        assert "private-file.png" not in caplog.text
        assert not (projects_dir / "album" / "private-title").exists()

    def test_unexpected_storage_failure_is_audited_and_reraised(
        self, client, projects_dir, monkeypatch, caplog,
    ):
        alice = User("alice", "x", "fa", is_admin=False)
        monkeypatch.setattr(helpers.login_manager, "_user_callback", lambda _: alice)
        private_error = "secret storage details"
        def fail(*args):
            raise OSError(private_error)
        monkeypatch.setattr(DataInterface, "create_gallery_post", fail)
        with client.session_transaction() as session:
            session["_user_id"] = alice.id
        with pytest.raises(OSError):
            client.post("/loft/new", data={"project_new": "album",
                "title": "private title", "template": "gallery"})
        assert '"event": "loft.operation_failed"' in caplog.text
        assert '"error_type": "OSError"' in caplog.text
        assert '"stage": "create_post"' in caplog.text
        assert "secret storage details" not in caplog.text


class TestQuiteFrankliPage:
    @pytest.fixture
    def client(self):
        from flask import Flask
        from web_app.loft import quitefrankli_api

        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="test-secret-key")
        helpers.login_manager.init_app(app)
        app.register_blueprint(loft_api)
        app.register_blueprint(quitefrankli_api)
        return app.test_client()

    def test_standalone_page_serves_only_the_post(self, client, projects_dir):
        _make_post(projects_dir, "quitefrankli", "quitefrankli")
        with DataInterface().edit_meta() as meta:
            meta.projects["quitefrankli"].posts["quitefrankli"].visibility = loft_module.PostVisibility.UNLISTED
        content = '<!DOCTYPE html><html><head><title>Frank Li</title></head><body><h1>Frank Li</h1></body></html>'
        (projects_dir / "quitefrankli" / "quitefrankli" / "index.html").write_text(content)

        response = client.get("/quitefrankli")

        assert response.status_code == 200
        assert response.mimetype == "text/html"
        assert "Location" not in response.headers
        assert response.text == content

    @pytest.mark.parametrize("path", [
        "/loft/quitefrankli", "/loft/quitefrankli/",
        "/loft/quitefrankli/quitefrankli", "/loft/quitefrankli/quitefrankli/",
    ])
    def test_old_urls_redirect_directly_to_standalone_page(self, client, projects_dir, path):
        response = client.get(path)
        assert response.status_code == 301
        assert response.location == "/quitefrankli"

    def test_portfolio_assets_remain_accessible(self, client, projects_dir):
        _make_post(projects_dir, "quitefrankli", "quitefrankli")
        post_dir = projects_dir / "quitefrankli" / "quitefrankli"
        for name, content in [("frank-and-nabi.webp", b"portrait"), ("resume-hft.pdf", b"resume")]:
            (post_dir / name).write_bytes(content)
            response = client.get(f"/loft/quitefrankli/quitefrankli/{name}")
            assert response.status_code == 200
            assert response.data == content

    def test_missing_or_restricted_portfolio_is_not_exposed(self, client, projects_dir):
        assert client.get("/quitefrankli").status_code == 404
        _make_post(projects_dir, "quitefrankli", "quitefrankli")
        with DataInterface().edit_meta() as meta:
            meta.projects["quitefrankli"].posts["quitefrankli"].visibility = loft_module.PostVisibility.RESTRICTED
        assert client.get("/quitefrankli").status_code == 404


