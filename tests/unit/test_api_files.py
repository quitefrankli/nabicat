"""File API and File Store share the same user-visible files."""
import gzip
import io
from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import FileStorage

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.file_store.data_interface import DataInterface as FileStore
from web_app.users import User
from web_app.api_auth import issue_api_token


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
        if operation == 'push':
            with client.application.app_context():
                token = issue_api_token(username)
            data = payload.pop('data', b'')
            encoding = payload.pop('encoding', 'identity')
            return client.post('/api/push', data=data, query_string=payload, headers={
                'Authorization': f'Bearer {token}', 'Content-Type': 'application/octet-stream',
                'Content-Encoding': encoding,
            })
        return client.post(f"/api/{operation}", json={
            "username": username, "password": "pass", **payload,
        })

    return FileStore(), users, request


def encoded(data):
    return {"data": gzip.compress(data), "encoding": "gzip"}


@pytest.mark.parametrize('encoding', ['identity', 'gzip'])
def test_upload_stream_without_tell(storage, client, monkeypatch, encoding):
    import web_app.api as api

    store, (alice, _), request = storage
    original = b'video content'
    body = io.BytesIO(gzip.compress(original) if encoding == 'gzip' else original)

    class Body:
        def read(self, size=-1):
            return body.read(size)

    events = []
    monkeypatch.setattr(api, 'log_event', lambda *args, **kwargs: events.append((args, kwargs)))
    with client.application.app_context():
        token = issue_api_token(alice.id)
    response = client.post('/api/push?name=video.mp4', environ_overrides={
        'wsgi.input': Body(), 'wsgi.input_terminated': True,
    }, headers={
        'Authorization': f'Bearer {token}', 'Content-Type': 'application/octet-stream',
        'Content-Encoding': encoding,
    })
    assert response.status_code == 200
    assert store.get_file_path('video.mp4', alice).read_bytes() == original
    assert events[-1][0] == ('api', 'api.data_pushed')
    assert events[-1][1]['bytes'] == len(original)


@pytest.mark.parametrize('name,compression', [
    ('video.MP4', 'identity'), ('photo.jpeg', 'identity'), ('image.png', 'identity'),
    ('audio.flac', 'identity'), ('backup.tar.gz', 'identity'), ('document.docx', 'identity'),
    ('font.woff2', 'identity'), ('notes.txt', 'gzip'), ('drawing.svg', 'gzip'),
    ('archive.tar', 'gzip'), ('document.pdf', 'gzip'),
])
def test_file_transfer_compression(storage, name, compression):
    store, (alice, _), request = storage
    original = b'\x00binary\xff content' * 20
    payload = {"data": gzip.compress(original) if compression == "gzip" else original, "encoding": compression}
    assert request('push', name=name, **payload).status_code == 200
    assert store.get_file_path(name, alice).read_bytes() == original
    response = request('pull', name=name)
    assert response.status_code == 200
    assert response.headers.get('Content-Encoding', 'identity') == compression
    assert (gzip.decompress(response.data) if compression == "gzip" else response.data) == original
    if compression == 'identity':
        assert response.content_length == len(original)


def test_binary_upload_rejects_json_and_allows_empty_files(storage, client):
    store, (alice, _), request = storage
    assert request('push', name='empty.mp4', data=b'').status_code == 200
    assert store.get_file_path('empty.mp4', alice).read_bytes() == b''
    with client.application.app_context():
        token = issue_api_token(alice.id)
    response = client.post('/api/push', json={'name': 'empty.mp4', 'data': 'old payload'},
                           headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 400
    assert store.get_file_path('empty.mp4', alice).read_bytes() == b''


@pytest.mark.parametrize('payload', [
    {'data': b'not gzip', 'encoding': 'gzip'},
    {'data': b'test', 'encoding': 'unknown'},
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
    assert gzip.decompress(response.data) == b"from UI"

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
@pytest.mark.parametrize("name", ["../outside.txt", "/outside.txt", None])
def test_file_api_rejects_invalid_paths(storage, operation, name):
    store, (alice, _), request = storage
    payload = encoded(b"content") if operation == "push" else {}
    assert request(operation, name=name, **payload).status_code == 400
    assert store.list_files(alice) == []
