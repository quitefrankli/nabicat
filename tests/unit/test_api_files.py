"""File API and File Store share the same user-visible files."""
import base64
import gzip
import io
from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import FileStorage

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.file_store.data_interface import DataInterface as FileStore
from web_app.users import User
from web_app.file_transfer import decode_file_payload, encode_file_payload


@pytest.fixture
def storage(client, monkeypatch, tmp_path):
    from web_app.api import api_api

    if api_api.name not in client.application.blueprints:
        client.application.register_blueprint(api_api)

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
    return encode_file_payload(data, "notes.txt")


@pytest.mark.parametrize('name,compression', [
    ('video.MP4', 'none'), ('photo.jpeg', 'none'), ('image.png', 'none'),
    ('audio.flac', 'none'), ('backup.tar.gz', 'none'), ('document.docx', 'none'),
    ('font.woff2', 'none'), ('notes.txt', 'gzip'), ('drawing.svg', 'gzip'),
    ('archive.tar', 'gzip'), ('document.pdf', 'gzip'),
])
def test_file_transfer_compression(storage, name, compression):
    store, (alice, _), request = storage
    original = b'\x00binary\xff content' * 20
    payload = encode_file_payload(original, name)
    assert payload['compression'] == compression
    assert request('push', name=name, **payload).status_code == 200
    assert store.get_file_path(name, alice).read_bytes() == original
    response = request('pull', name=name)
    assert response.status_code == 200
    assert response.json['compression'] == compression
    assert decode_file_payload(response.json) == original


@pytest.mark.parametrize('payload', [
    {'data': 'dGVzdA=='},
    {'data': '%%%invalid', 'compression': 'none'},
    {'data': 'dGVzdA==', 'compression': 'gzip'},
    {'data': 'dGVzdA==', 'compression': 'unknown'},
])
def test_invalid_explicit_encoding_preserves_existing_file(storage, payload):
    store, (alice, _), request = storage
    assert request('push', name='video.mp4', **encoded(b'original')).status_code == 200
    assert request('push', name='video.mp4', **payload).status_code == 400
    assert store.get_file_path('video.mp4', alice).read_bytes() == b'original'


def test_api_and_file_store_share_files_and_preserve_user_isolation(storage):
    store, (alice, bob), request = storage
    store.save_files([(FileStorage(io.BytesIO(b"from UI"), "notes.txt"), "docs/notes.txt")], [], alice)
    assert request("list").json["files"] == ["docs/notes.txt"]
    response = request("pull", name="docs/notes.txt")
    assert gzip.decompress(base64.b64decode(response.json["data"])) == b"from UI"

    assert request("push", name="docs/notes.txt", **encoded(b"from API")).status_code == 200
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
    assert next(item for item in nested if item['name'] == 'notes.txt')['size'] == 14
    assert next(item for item in nested if item['name'] == 'deep')['size'] is None
    assert request('list', path='../outside').status_code == 400
    assert request('list', path=42).status_code == 400


def test_api_upload_obeys_shared_quota_and_preserves_rejected_overwrite(storage, monkeypatch):
    store, (alice, _), request = storage
    monkeypatch.setattr(ConfigManager().file_store, "admin_quota_bytes", 8)
    store.save_files([(FileStorage(io.BytesIO(b"1234"), "a.txt"), "a.txt")], [], alice)
    assert request("push", name="b.txt", **encoded(b"12345")).status_code == 400
    assert request("push", name="a.txt", **encoded(b"123456789")).status_code == 400
    assert store.list_files(alice) == ["a.txt"]
    assert store.get_file_path("a.txt", alice).read_bytes() == b"1234"
    assert request("push", name="a.txt", **encoded(b"12345678")).status_code == 200


def test_list_includes_latest_upload_dates_in_newest_first_order(storage):
    store, (alice, _), request = storage
    for name in ('old.txt', 'docs/new.txt'):
        store.save_file(FileStorage(io.BytesIO(name.encode()), name), alice)
    # Use dates safely in the past so a real overwrite is always the newest.
    now = datetime.now()
    old_date = (now - timedelta(days=2)).isoformat()
    new_date = (now - timedelta(days=1)).isoformat()
    with store.edit_metadata() as metadata:
        for entry, date in zip(metadata.users[alice.id].files, (old_date, new_date)):
            entry.uploaded_at = date
    assert request('list').json['file_details'] == [
        {'name': 'docs/new.txt', 'uploaded_at': new_date},
        {'name': 'old.txt', 'uploaded_at': old_date},
    ]
    assert request('push', name='old.txt', **encoded(b'updated')).status_code == 200
    details = request('list').json['file_details']
    assert details[0]['name'] == 'old.txt'
    assert details[0]['uploaded_at'] > new_date


@pytest.mark.parametrize("operation", ["push", "pull", "delete"])
@pytest.mark.parametrize("name", ["../outside.txt", "/outside.txt", None, 42])
def test_file_api_rejects_invalid_paths(storage, operation, name):
    store, (alice, _), request = storage
    assert request(operation, name=name, **encoded(b"content")).status_code == 400
    assert store.list_files(alice) == []
