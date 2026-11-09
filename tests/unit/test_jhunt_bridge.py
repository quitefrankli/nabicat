from __future__ import annotations

import json

import pytest

from web_app.config import ConfigManager
from web_app.users import User


class FakeResponse:
    def __init__(self, *, status=200, payload=None, headers=None, lines=()):
        self.status_code = status
        self.payload = payload or {}
        self.headers = headers or {"Content-Type": "application/json"}
        self.content = json.dumps(self.payload).encode()
        self.lines = lines
        self.closed = False

    def json(self):
        return self.payload

    def iter_lines(self, chunk_size=512, decode_unicode=False):
        self.chunk_size = chunk_size
        yield from self.lines

    def iter_content(self, chunk_size):
        yield b"%PDF-1.4 fixture"

    def close(self):
        self.closed = True


@pytest.fixture

def admin_client(client, auth_loader):
    user = User(username="admin", password="pw", folder="admin", is_admin=True)
    auth_loader(user)
    with client.session_transaction() as session:
        session["_user_id"] = user.id
    return client


@pytest.fixture
def viewer_client(client, auth_loader):
    user = User(username="viewer", password="pw", folder="viewer", is_admin=False)
    auth_loader(user)
    with client.session_transaction() as session:
        session["_user_id"] = user.id
    return client


@pytest.fixture
def anonymous_client(client):
    return client.application.test_client()


def configure_debug(monkeypatch, *, generation="local-1"):
    config = ConfigManager()
    config.debug_mode = True
    monkeypatch.setenv("JHUNT_DEBUG_ENABLED", "true")
    monkeypatch.setenv("JHUNT_DEBUG_BASE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("JHUNT_DEBUG_TOKEN", "server-only-secret")
    monkeypatch.setenv("JHUNT_DEBUG_GENERATION", generation)
    monkeypatch.setenv("JHUNT_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("JHUNT_PRODUCTION_BASE_URL", "http://127.0.0.1:9321")
    monkeypatch.setenv("JHUNT_PRODUCTION_TOKEN", "production-secret")
    monkeypatch.setenv("JHUNT_PRODUCTION_GENERATION", "prod-9")
    return config


def test_connection_settings_are_independent_and_require_loopback(monkeypatch):
    from web_app.jhunt_ui.bridge import BridgeError, connection_settings

    config = configure_debug(monkeypatch)
    assert connection_settings()["base_url"] == "http://127.0.0.1:8080"
    config.debug_mode = False
    assert connection_settings()["base_url"] == "http://127.0.0.1:9321"
    monkeypatch.setenv("JHUNT_PRODUCTION_BASE_URL", "http://example.com:9321")
    with pytest.raises(BridgeError, match="unavailable"):
        connection_settings()
    monkeypatch.setenv("JHUNT_PRODUCTION_BASE_URL", "http://[invalid:9321")
    with pytest.raises(BridgeError, match="unavailable"):
        connection_settings()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/jhunt-ui/api/inbox", None),
        ("get", "/jhunt-ui/api/search/commands/key-1", None),
        ("get", "/jhunt-ui/api/search/requests/request-1", None),
        ("post", "/jhunt-ui/api/search/criteria", {"text": "roles"}),
        ("post", "/jhunt-ui/api/search/requests", {"text": "roles"}),
        ("get", "/jhunt-ui/api/events", None),
        ("get", "/jhunt-ui/api/artifacts/" + "a" * 64, None),
        ("post", "/jhunt-ui/api/tasks/task-2/input", {"answer": {"text": "x"}}),
        ("post", "/jhunt-ui/api/applications/package-3/decision", {"decision": "approve"}),
    ],
)
@pytest.mark.parametrize("identity", ["anonymous", "viewer"])
def test_bridge_routes_reject_non_admin_and_anonymous_without_upstream(
    anonymous_client, viewer_client, monkeypatch, method, path, body, identity
):
    calls = []
    monkeypatch.setattr(
        "requests.Session.request", lambda *args, **kwargs: calls.append(args)
    )
    selected = anonymous_client if identity == "anonymous" else viewer_client
    response = getattr(selected, method)(path, json=body) if body is not None else getattr(selected, method)(path)
    assert response.status_code == (302 if identity == "anonymous" else 403)
    assert not calls


def test_inbox_uses_server_bearer_and_returns_private_no_store(admin_client, monkeypatch):
    configure_debug(monkeypatch)
    snapshot = {
        "connected": True,
        "store_id": "store-1",
        "revision": 7,
        "notifications": [],
        "tasks": [],
        "applications": [],
    }
    observed = {}
    closed = []

    def request(session, method, url, **kwargs):
        observed.update(method=method, url=url, **kwargs)
        return FakeResponse(payload=snapshot)

    monkeypatch.setattr("requests.Session.request", request)
    monkeypatch.setattr("requests.Session.close", lambda session: closed.append(True))
    response = admin_client.get("/jhunt-ui/api/inbox")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.json["connection_generation"] == "local-1"
    assert observed["url"] == "http://127.0.0.1:8080/review/inbox"
    assert observed["headers"]["Authorization"] == "Bearer server-only-secret"
    assert observed["allow_redirects"] is False
    assert observed["timeout"] == ConfigManager().jhunt_request_timeout_s
    assert b"server-only-secret" not in response.data
    assert b"production-secret" not in response.data
    assert closed == [True]


def test_answer_forwards_store_and_question_hash_but_not_generation(admin_client, monkeypatch):
    configure_debug(monkeypatch)
    observed = {}

    def request(session, method, url, **kwargs):
        observed.update(method=method, url=url, **kwargs)
        return FakeResponse(payload={"work_id": "work-2"})

    monkeypatch.setattr("requests.Session.request", request)
    response = admin_client.post(
        "/jhunt-ui/api/tasks/task-2/input",
        json={
            "answer": {"text": "Available after 5pm"},
            "expected_store_id": "store-1",
            "expected_connection_generation": "local-1",
            "expected_question_hash": "a" * 64,
        },
    )
    assert response.status_code == 200
    assert observed["json"] == {
        "answer": {"text": "Available after 5pm"},
        "expected_store_id": "store-1",
        "expected_question_hash": "a" * 64,
    }
    assert observed["headers"]["Authorization"] == "Bearer server-only-secret"
    assert b"Available after 5pm" not in response.data
    assert response.headers["Cache-Control"] == "private, no-store"


def test_changed_connection_and_invalid_hash_never_reach_upstream(admin_client, monkeypatch):
    configure_debug(monkeypatch, generation="local-2")
    calls = []
    monkeypatch.setattr("requests.Session.request", lambda *args, **kwargs: calls.append(args))
    stale = admin_client.post(
        "/jhunt-ui/api/tasks/task-2/input",
        json={
            "answer": {"text": "answer"},
            "expected_store_id": "store-1",
            "expected_connection_generation": "local-1",
        },
    )
    artifact = admin_client.get("/jhunt-ui/api/artifacts/not-a-hash")
    assert stale.status_code == 409
    assert artifact.status_code == 404
    assert not calls
    assert stale.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("manifest_hash", [None, {}, []])
def test_non_string_manifest_is_rejected_before_upstream(admin_client, monkeypatch, manifest_hash):
    configure_debug(monkeypatch)
    calls = []
    monkeypatch.setattr("requests.Session.request", lambda *args, **kwargs: calls.append(args))
    response = admin_client.post(
        "/jhunt-ui/api/applications/package-3/decision",
        json={
            "manifest_hash": manifest_hash,
            "decision": "approve",
            "expected_store_id": "store-1",
            "expected_connection_generation": "local-1",
        },
    )
    assert response.status_code == 400
    assert response.headers["Cache-Control"] == "private, no-store"
    assert not calls


def test_decision_forwards_exact_manifest_and_rejects_upstream_conflict(admin_client, monkeypatch):
    configure_debug(monkeypatch)
    observed = {}

    def request(session, method, url, **kwargs):
        observed.update(method=method, url=url, **kwargs)
        return FakeResponse(status=409, payload={"detail": "stale package"})

    monkeypatch.setattr("requests.Session.request", request)
    response = admin_client.post(
        "/jhunt-ui/api/applications/package-3/decision",
        json={
            "manifest_hash": "b" * 64,
            "decision": "approve",
            "expected_store_id": "store-1",
            "expected_connection_generation": "local-1",
        },
    )
    assert response.status_code == 409
    assert observed["json"] == {
        "manifest_hash": "b" * 64,
        "decision": "approve",
        "expected_store_id": "store-1",
    }
    assert b"stale package" not in response.data


def test_csrf_is_required_for_mutation(admin_client, monkeypatch):
    configure_debug(monkeypatch)
    admin_client.application.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setattr("requests.Session.request", lambda *args, **kwargs: FakeResponse(payload={}))
    body = {
        "answer": {"text": "answer"},
        "expected_store_id": "store-1",
        "expected_connection_generation": "local-1",
    }
    denied = admin_client.post("/jhunt-ui/api/tasks/task-2/input", json=body)
    assert denied.status_code == 400
    page = admin_client.get("/jhunt-ui/")
    import re
    token = re.search(rb'<meta name="csrf-token" content="([^"]+)"', page.data).group(1).decode()
    accepted = admin_client.post(
        "/jhunt-ui/api/tasks/task-2/input",
        json=body,
        headers={"X-CSRFToken": token},
    )
    assert accepted.status_code == 200


def test_stream_disconnect_closes_upstream_and_releases_slot(admin_client, monkeypatch):
    configure_debug(monkeypatch)
    stream = FakeResponse(
        headers={"Content-Type": "text/event-stream"},
        lines=("event: change", 'data: {"store_id":"store-1","revision":8}', ""),
    )
    observed = {}
    closed = []

    def request(session, method, url, **kwargs):
        observed.update(method=method, url=url, **kwargs)
        return stream

    monkeypatch.setattr("requests.Session.request", request)
    monkeypatch.setattr("requests.Session.close", lambda session: closed.append(True))
    response = admin_client.get("/jhunt-ui/api/events?cursor=store-1%3A7", buffered=False)
    assert response.status_code == 200
    assert response.headers["X-Accel-Buffering"] == "no"
    assert observed["params"] == {"cursor": "store-1:7"}
    assert observed["headers"]["Authorization"] == "Bearer server-only-secret"
    next(response.response)
    response.close()
    assert stream.closed
    assert stream.chunk_size == 1
    assert closed == [True]
    redis = __import__("web_app.redis_client", fromlist=["get_redis"]).get_redis()
    assert not any(redis.exists(f"nabicat:jhunt:stream:{index}") for index in range(ConfigManager().jhunt_stream_max_active))


def test_pairing_verifies_persists_rotates_and_isolates_modes(admin_client, monkeypatch, caplog):
    from web_app.jhunt_ui.bridge import connection_settings
    from web_app.jhunt_ui.connection_store import ConnectionStore

    config = configure_debug(monkeypatch)
    monkeypatch.delenv("JHUNT_DEBUG_ENABLED")
    monkeypatch.delenv("JHUNT_DEBUG_TOKEN")
    token = "new-private-token-" + "a" * 32
    snapshot = {"store_id": "store-1", "revision": 1}
    calls = []

    def upstream(session, method, url, **kwargs):
        calls.append((url, kwargs))
        if kwargs["headers"]["Authorization"] != "Bearer " + token:
            return FakeResponse(status=401)
        return FakeResponse(payload=snapshot)

    monkeypatch.setattr("requests.Session.request", upstream)
    response = admin_client.post("/jhunt-ui/api/connection", json={"token": token})
    assert response.status_code == 200
    assert response.json["connected"] is True
    assert response.headers["Cache-Control"] == "private, no-store"
    generation = response.json["connection_generation"]
    assert generation != "local-1"
    path = ConnectionStore().path
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert token not in response.get_data(as_text=True)
    assert token not in caplog.text
    # A fresh store/client reads the persisted credentials, without env changes.
    assert connection_settings()["token"] == token
    assert connection_settings()["enabled"] is True
    assert admin_client.get("/jhunt-ui/api/inbox").json["connection_generation"] == generation
    assert all(url == "http://127.0.0.1:8080/review/inbox" for url, _ in calls)
    before = path.read_bytes()
    rejected = admin_client.post("/jhunt-ui/api/connection", json={"token": "wrong-" * 8})
    assert rejected.status_code == 502
    assert path.read_bytes() == before
    assert admin_client.post("/jhunt-ui/api/connection", json={"token": token}).json["connection_generation"] == generation
    config.debug_mode = False
    assert connection_settings()["token"] == "production-secret"
    config.debug_mode = True
    # Persisted configuration survives recreation of ConfigManager as on restart.
    old_root = config.debug_data_root
    monkeypatch.setattr(ConfigManager, "_instance", None)
    fresh = ConfigManager()
    fresh.debug_mode = True
    fresh.debug_data_root = old_root
    assert connection_settings()["token"] == token


def test_pairing_rejects_invalid_input_without_sending_credentials(admin_client, monkeypatch):
    configure_debug(monkeypatch)
    calls = []
    monkeypatch.setattr("requests.Session.request", lambda *a, **kw: calls.append(kw))
    for body in ({"token": "short"}, {"token": "a" * 24 + "\r\n"},
                 {"token": "é" * 32}, {"token": 123}, {"token": "a" * 24, "base_url": "http://example.com"}):
        assert admin_client.post("/jhunt-ui/api/connection", json=body).status_code == 400
    assert not calls


@pytest.mark.parametrize(("identity", "expected"), [("anonymous_client", 302), ("viewer_client", 403)])
def test_pairing_requires_admin(request, monkeypatch, identity, expected):
    client = request.getfixturevalue(identity)
    configure_debug(monkeypatch).debug_mode = False
    calls = []
    monkeypatch.setattr("requests.Session.request", lambda *a, **kw: calls.append(kw))
    assert client.post("/jhunt-ui/api/connection", json={"token": "x" * 32}).status_code == expected
    assert not calls


def test_pairing_requires_csrf(admin_client, monkeypatch):
    configure_debug(monkeypatch)
    calls = []
    monkeypatch.setattr("requests.Session.request", lambda *a, **kw: calls.append(kw))
    admin_client.application.config["WTF_CSRF_ENABLED"] = True
    assert admin_client.post("/jhunt-ui/api/connection", json={"token": "x" * 32}).status_code == 400
    assert not calls


@pytest.mark.parametrize("path", ["criteria", "requests"])
def test_search_commands_guard_and_forward(admin_client, monkeypatch, path):
    configure_debug(monkeypatch)
    calls = []
    receipt = {"criteria": {"id": "criteria-1", "data": {"text": "Systems"}},
               "search": None, "request_key": "command-1", "work_id": None}
    def request(session, method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse(payload=receipt)
    monkeypatch.setattr("requests.Session.request", request)
    body = {"text": "Systems", "expected_criteria_id": None, "expected_store_id": "store-1",
            "expected_connection_generation": "local-1", "request_key": "command-1"}
    response = admin_client.post("/jhunt-ui/api/search/" + path, json=body)
    assert response.status_code == 200
    assert response.json == receipt
    assert response.headers["Cache-Control"] == "private, no-store"
    assert calls[0][0:2] == ("POST", "http://127.0.0.1:8080/search/" + path)
    assert "expected_connection_generation" not in calls[0][2]["json"]
    assert calls[0][2]["headers"]["Authorization"] == "Bearer server-only-secret"
    for invalid in [{"text": " "}, {"text": "x" * 20001}, {"expected_criteria_id": {}},
                    {"expected_connection_generation": "old"}, {"request_key": "../bad"}]:
        response = admin_client.post("/jhunt-ui/api/search/" + path, json={**body, **invalid})
        assert response.status_code in (400, 409)
    assert len(calls) == 1
    admin_client.application.config["WTF_CSRF_ENABLED"] = True
    assert admin_client.post("/jhunt-ui/api/search/" + path, json=body).status_code == 400
    assert len(calls) == 1


def test_search_snapshot_receipts_and_upstream_errors(admin_client, monkeypatch):
    configure_debug(monkeypatch)
    search = {"criteria": None, "sources": ["fixture"], "active_requests": [], "latest_request": None}
    status = [200]
    def request(session, method, url, **kwargs):
        if url.endswith("/review/inbox"):
            return FakeResponse(payload={"store_id": "store-1", "revision": 1, "search": search})
        return FakeResponse(status=status[0], payload={"request_key": "command-1"})
    monkeypatch.setattr("requests.Session.request", request)
    assert admin_client.get("/jhunt-ui/api/inbox").json["search"] == search
    assert admin_client.get("/jhunt-ui/api/search/commands/command-1").json["request_key"] == "command-1"
    status[0] = 404
    assert admin_client.get("/jhunt-ui/api/search/commands/command-1").status_code == 404
    status[0] = 503
    assert admin_client.get("/jhunt-ui/api/search/requests/request-1").status_code == 502
