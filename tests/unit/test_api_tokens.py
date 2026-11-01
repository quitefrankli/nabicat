"""API token authentication through HTTP, with isolated users and storage."""
import pytest
from flask import Flask

from web_app.api import api_api
from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.users import User


@pytest.fixture
def token_client(monkeypatch, tmp_path):
    config = ConfigManager()
    monkeypatch.setattr(config, "debug_mode", False)
    monkeypatch.setattr(config, "production_data_root", tmp_path)
    monkeypatch.setattr(config, "use_offline_syncer", True)
    with DataInterface().edit_users() as users:
        users.root = [User.create(username="admin", password="pass", folder="admin", is_admin=True)]
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="isolated-test-secret-key-for-api-tokens")
    app.register_blueprint(api_api)
    return app.test_client()


def test_login_issues_one_day_token_that_authenticates_api_requests(token_client, caplog):
    caplog.set_level("INFO")
    response = token_client.post("/api/token", json={"username": "admin", "password": "pass"})
    assert response.status_code == 200
    assert response.json["token_type"] == "Bearer"
    assert response.json["expires_in"] == 86400
    assert response.headers["Cache-Control"] == "no-store"
    token = response.json["access_token"]
    listing = token_client.post("/api/list", json={}, headers={"Authorization": f"Bearer {token}"})
    assert listing.status_code == 200
    assert listing.json["files"] == []
    assert token not in caplog.text
    assert '"event": "api.token_issued"' in caplog.text


def test_cli_login_saves_only_token_and_uses_it_for_requests(token_client, monkeypatch, tmp_path):
    import json
    import requests
    from click.testing import CliRunner
    from scripts import api_helper

    path = tmp_path / "client" / "auth.json"
    monkeypatch.setattr(ConfigManager(), "api_helper_config_path", path)
    sent = []

    def post(url, *, json, headers=None, **kwargs):
        sent.append((url, json, headers))
        response = token_client.post(url, json=json, headers=headers)
        result = requests.Response()
        result.status_code = response.status_code
        result._content = response.data
        return result

    monkeypatch.setattr(api_helper.requests, "post", post)
    runner = CliRunner()
    result = runner.invoke(api_helper.cli, ["login"], input="admin\npass\nhttps://nabicat.site\n")
    assert result.exit_code == 0, result.output
    saved = json.loads(path.read_text())
    assert saved["username"] == "admin"
    assert "password" not in saved
    assert path.stat().st_mode & 0o777 == 0o600
    assert saved["access_token"] not in result.output
    result = runner.invoke(api_helper.cli, ["ls"])
    assert result.exit_code == 0, result.output
    assert sent[-1] == (
        "https://nabicat.site/api/list", {"path": ""},
        {"Authorization": f"Bearer {saved['access_token']}"},
    )
    assert len(sent) == 2


@pytest.mark.parametrize("credentials", [
    {"username": "admin", "password": "wrong"},
    {"username": "missing", "password": "pass"},
    {"username": [], "password": "pass"},
])
def test_login_rejects_bad_credentials_without_issuing_token(token_client, credentials):
    response = token_client.post("/api/token", json=credentials)
    assert response.status_code == 401
    assert "access_token" not in response.json


def test_token_has_one_day_expiry_and_rejects_tampering(token_client):
    import jwt

    token = token_client.post("/api/token", json={"username": "admin", "password": "pass"}).json["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 86400
    forged = jwt.encode(claims, "attacker-controlled-key-with-enough-length", algorithm="HS256")
    for invalid in (forged, "malformed-token"):
        response = token_client.post("/api/list", json={"username": "admin", "password": "pass"},
                                     headers={"Authorization": f"Bearer {invalid}"})
        assert response.status_code == 401


def test_expired_token_is_rejected(token_client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import web_app.api_auth

    class PastClock:
        @classmethod
        def now(cls, tz):
            return datetime.now(timezone.utc) - timedelta(days=2)

    monkeypatch.setattr(web_app.api_auth, "datetime", PastClock)
    token = token_client.post("/api/token", json={"username": "admin", "password": "pass"}).json["access_token"]
    assert token_client.post("/api/list", json={}, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_uses_current_identity_and_permissions(token_client):
    token = token_client.post("/api/token", json={"username": "admin", "password": "pass"}).json["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert token_client.post("/api/list", json={"username": "another-user"}, headers=headers).status_code == 200
    with DataInterface().edit_users() as users:
        users.get("admin").is_admin = False
    assert token_client.post("/api/list", json={}, headers=headers).status_code == 403
    with DataInterface().edit_users() as users:
        users.remove("admin")
    assert token_client.post("/api/list", json={}, headers=headers).status_code == 401
