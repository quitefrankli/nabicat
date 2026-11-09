from __future__ import annotations

import io
import json
import logging

import pytest


TOKEN = "local-jhunt-token-" + "x" * 32


class FakeClient:
    snapshots = []
    observed = []

    def __init__(self, settings):
        self.settings = settings
        self.observed.append(settings)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def load_inbox(self):
        return self.snapshots.pop(0)


def run_pair(monkeypatch, payload):
    from scripts.pair_jhunt import main

    out = io.StringIO()
    status = main(io.StringIO(json.dumps(payload)), out)
    return status, out.getvalue()


def test_pairing_invalid_token_is_rejected_without_client(monkeypatch, caplog):
    from scripts import pair_jhunt

    monkeypatch.setattr(pair_jhunt, "JHuntClient", lambda *_: pytest.fail("client created"))
    caplog.set_level(logging.INFO)
    status, output = run_pair(monkeypatch, {
        "token": "short-secret", "expected_store_id": "store-1", "remote_port": 9321,
    })
    assert status != 0
    assert "short-secret" not in output + caplog.text


def test_pairing_verifies_store_id_before_saving(monkeypatch, tmp_path, caplog):
    from scripts import pair_jhunt
    from web_app.config import ConfigManager
    from web_app.jhunt_ui.connection_store import load_connection

    config = ConfigManager()
    monkeypatch.setattr(pair_jhunt, "JHuntClient", FakeClient)
    caplog.set_level(logging.INFO)
    FakeClient.snapshots = [{"store_id": "other-store", "revision": 1}]
    FakeClient.observed = []
    status, output = run_pair(monkeypatch, {
        "token": TOKEN, "expected_store_id": "store-1", "remote_port": 9321,
    })
    assert status != 0
    assert load_connection() is None
    assert FakeClient.observed[0]["base_url"] == "http://127.0.0.1:9321"
    assert TOKEN not in output + caplog.text


def test_pairing_is_idempotent_and_rotates_on_changed_token(monkeypatch, caplog):
    from scripts import pair_jhunt
    from web_app.jhunt_ui.connection_store import load_connection

    monkeypatch.setattr(pair_jhunt, "JHuntClient", FakeClient)
    caplog.set_level(logging.INFO)
    FakeClient.snapshots = [
        {"store_id": "store-1", "revision": 1},
        {"store_id": "store-1", "revision": 1},
        {"store_id": "store-1", "revision": 1},
    ]
    FakeClient.observed = []
    request = {"token": TOKEN, "expected_store_id": "store-1", "remote_port": 9321}
    first, output1 = run_pair(monkeypatch, request)
    generation1 = load_connection()["generation"]
    second, output2 = run_pair(monkeypatch, request)
    generation2 = load_connection()["generation"]
    changed, output3 = run_pair(monkeypatch, {**request, "token": TOKEN + "y"})
    generation3 = load_connection()["generation"]
    assert (first, second, changed) == (0, 0, 0)
    assert generation1 == generation2 != generation3
    assert all("Pairing complete." in text for text in (output1, output2, output3))
    assert TOKEN not in output1 + output2 + output3 + caplog.text


def test_pairing_malformed_json_does_not_log_submitted_token(caplog):
    from scripts.pair_jhunt import main

    caplog.set_level(logging.INFO)
    out = io.StringIO()
    status = main(io.StringIO('{"token":"' + TOKEN + '"'), out)
    assert status != 0
    assert TOKEN not in out.getvalue() + caplog.text


@pytest.mark.parametrize("payload", [
    {"token": TOKEN, "expected_store_id": "store-1", "remote_port": True},
    {"token": TOKEN, "expected_store_id": "store-1", "remote_port": 0},
    {"token": TOKEN, "expected_store_id": "store-1", "remote_port": 65536},
    {"token": TOKEN, "expected_store_id": "store-1", "remote_port": 9321, "url": "http://evil"},
])
def test_pairing_rejects_bad_port_or_unexpected_fields(monkeypatch, payload):
    from scripts import pair_jhunt

    monkeypatch.setattr(pair_jhunt, "JHuntClient", lambda *_: pytest.fail("client created"))
    status, output = run_pair(monkeypatch, payload)
    assert status != 0
    assert output == "Pairing failed.\n"
