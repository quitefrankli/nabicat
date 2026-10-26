"""File API and File Store share the same user-visible files."""
import base64
import gzip
import io

import pytest
from werkzeug.datastructures import FileStorage

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.file_store.data_interface import DataInterface as FileStore
from web_app.users import User


@pytest.fixture
def storage(client, monkeypatch, tmp_path):
    import web_app.__main__  # noqa: F401

    config = ConfigManager()
    monkeypatch.setattr(config, "debug_mode", True)
    monkeypatch.setattr(config, "debug_data_root", tmp_path)
    monkeypatch.setattr(config, "use_offline_syncer", True)
    users = [User.create(username=name, password="pass", folder=name, is_admin=True)
             for name in ("alice", "bob")]
    with DataInterface().edit_users() as model:
        model.root = users

    def request(operation, username="alice", **payload):
        return client.post(f"/api/{operation}", json={
            "username": username, "password": "pass", **payload,
        })

    return FileStore(), users, request


def encoded(data):
    return base64.b64encode(gzip.compress(data)).decode()


def test_api_and_file_store_share_files_and_preserve_user_isolation(storage):
    store, (alice, bob), request = storage
    store.save_files([(FileStorage(io.BytesIO(b"from UI"), "notes.txt"), "docs/notes.txt")], [], alice)
    assert request("list").json["files"] == ["docs/notes.txt"]
    response = request("pull", name="docs/notes.txt")
    assert gzip.decompress(base64.b64decode(response.json["data"])) == b"from UI"

    assert request("push", name="docs/notes.txt", data=encoded(b"from API")).status_code == 200
    assert store.get_file_path("docs/notes.txt", alice).read_bytes() == b"from API"
    assert store.list_files(alice) == ["docs/notes.txt"]
    assert request("list", username="bob").json["files"] == []
    assert request("pull", username="bob", name="docs/notes.txt").status_code == 404
    assert request("delete", username="bob", name="docs/notes.txt").status_code == 404
    assert request("delete", name="docs").status_code == 404
    assert store.get_file_path("docs/notes.txt", alice).exists()
    assert request("delete", name="docs/notes.txt").status_code == 200
    assert store.list_files(alice) == []


def test_api_upload_obeys_shared_quota_and_preserves_rejected_overwrite(storage, monkeypatch):
    store, (alice, _), request = storage
    monkeypatch.setattr(ConfigManager().file_store, "admin_quota_bytes", 8)
    store.save_files([(FileStorage(io.BytesIO(b"1234"), "a.txt"), "a.txt")], [], alice)
    assert request("push", name="b.txt", data=encoded(b"12345")).status_code == 400
    assert request("push", name="a.txt", data=encoded(b"123456789")).status_code == 400
    assert store.list_files(alice) == ["a.txt"]
    assert store.get_file_path("a.txt", alice).read_bytes() == b"1234"
    assert request("push", name="a.txt", data=encoded(b"12345678")).status_code == 200


@pytest.mark.parametrize("operation", ["push", "pull", "delete"])
@pytest.mark.parametrize("name", ["../outside.txt", "/outside.txt", None, 42])
def test_file_api_rejects_invalid_paths(storage, operation, name):
    store, (alice, _), request = storage
    assert request(operation, name=name, data=encoded(b"content")).status_code == 400
    assert store.list_files(alice) == []
