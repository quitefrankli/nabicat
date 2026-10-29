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


def test_list_directory_shows_only_immediate_files_and_folders(storage):
    store, (alice, _), request = storage
    for name in ('root.txt', 'docs/notes.txt', 'docs/deep/hidden.txt'):
        store.save_file(FileStorage(io.BytesIO(name.encode()), name), alice)
    root = request('list', path='').json['entries']
    assert {(item['name'], item['kind']) for item in root} == {('root.txt', 'file'), ('docs', 'folder')}
    nested = request('list', path='docs').json['entries']
    assert {(item['name'], item['kind']) for item in nested} == {('notes.txt', 'file'), ('deep', 'folder')}
    assert all(item['uploaded_at'] for item in nested)
    assert request('list', path='../outside').status_code == 400
    assert request('list', path=42).status_code == 400


def test_api_upload_obeys_shared_quota_and_preserves_rejected_overwrite(storage, monkeypatch):
    store, (alice, _), request = storage
    monkeypatch.setattr(ConfigManager().file_store, "admin_quota_bytes", 8)
    store.save_files([(FileStorage(io.BytesIO(b"1234"), "a.txt"), "a.txt")], [], alice)
    assert request("push", name="b.txt", data=encoded(b"12345")).status_code == 400
    assert request("push", name="a.txt", data=encoded(b"123456789")).status_code == 400
    assert store.list_files(alice) == ["a.txt"]
    assert store.get_file_path("a.txt", alice).read_bytes() == b"1234"
    assert request("push", name="a.txt", data=encoded(b"12345678")).status_code == 200


def test_list_includes_latest_upload_dates_in_newest_first_order(storage):
    store, (alice, _), request = storage
    for name in ('old.txt', 'docs/new.txt'):
        store.save_file(FileStorage(io.BytesIO(name.encode()), name), alice)
    with store.edit_metadata() as metadata:
        for entry, date in zip(metadata.users[alice.id].files, (
            '2026-01-01T10:00:00', '2026-10-29T12:30:00',
        )):
            entry.uploaded_at = date
    assert request('list').json['file_details'] == [
        {'name': 'docs/new.txt', 'uploaded_at': '2026-10-29T12:30:00'},
        {'name': 'old.txt', 'uploaded_at': '2026-01-01T10:00:00'},
    ]
    assert request('push', name='old.txt', data=encoded(b'updated')).status_code == 200
    details = request('list').json['file_details']
    assert details[0]['name'] == 'old.txt'
    assert details[0]['uploaded_at'] > '2026-10-29T12:30:00'


@pytest.mark.parametrize("operation", ["push", "pull", "delete"])
@pytest.mark.parametrize("name", ["../outside.txt", "/outside.txt", None, 42])
def test_file_api_rejects_invalid_paths(storage, operation, name):
    store, (alice, _), request = storage
    assert request(operation, name=name, data=encoded(b"content")).status_code == 400
    assert store.list_files(alice) == []
