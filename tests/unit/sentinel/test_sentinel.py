from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from flask import Flask, g

from web_app.config import ConfigManager
from web_app.redis_client import get_redis
from web_app.sentinel.data_interface import DataInterface
from web_app.sentinel.models import ExecutionStatus, Report, RunStatus
from web_app.sentinel.runtime import ExecutionLease, state_get, state_put, state_replace
from web_app.users import User


@pytest.fixture
def sentinel_data_root(monkeypatch, tmp_path):
    import fakeredis
    import web_app.redis_client as redis_client

    config = ConfigManager()
    monkeypatch.setattr(config, "debug_mode", True)
    monkeypatch.setattr(config, "debug_data_root", tmp_path / "debug-data")
    monkeypatch.setattr(config, "production_data_root", tmp_path / "production-data")
    monkeypatch.setattr(config.sentinel, "lease_ttl_s", 60)
    monkeypatch.setattr(config.sentinel, "lease_recovery_grace_s", 5)
    monkeypatch.setattr(redis_client, "_client", fakeredis.FakeRedis())
    return config.debug_data_root


def _stale_running_report(run_id: str = "a" * 32) -> Report:
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    return Report(
        run_id=run_id,
        status=RunStatus.RUNNING,
        lifecycle=ExecutionStatus.RUNNING,
        created_at=old,
        updated_at=old,
    )


def test_reports_and_evidence_use_configured_sentinel_data_root(sentinel_data_root):
    data = DataInterface()
    report = Report(run_id="b" * 32, created_at="2026-01-01T00:00:00+00:00")

    data._save_report(report)
    data.write_screenshot(report.run_id, "step-00.png", b"png")

    expected = sentinel_data_root / "sentinel" / "runs" / report.run_id
    assert (expected / "report.json").is_file()
    assert (expected / "screenshots" / "step-00.png").read_bytes() == b"png"
    assert data.load_report(report.run_id).run_id == report.run_id


def test_account_deletion_removes_only_that_users_sentinel_files(sentinel_data_root):
    data = DataInterface()
    alice_dir = data.sentinel_directory / "alice-folder"
    bob_dir = data.sentinel_directory / "bob-folder"
    alice_dir.mkdir(parents=True)
    bob_dir.mkdir(parents=True)
    (alice_dir / "private.json").write_text("{}", encoding="utf-8")
    (bob_dir / "private.json").write_text("{}", encoding="utf-8")

    data.delete_user_data(User("alice", "", "alice-folder"))

    assert not alice_dir.exists()
    assert (bob_dir / "private.json").is_file()


def test_stale_active_snapshot_cannot_overwrite_terminal_report(sentinel_data_root):
    data = DataInterface()
    completed = _stale_running_report("c" * 32)
    completed.lifecycle = ExecutionStatus.FINISHED
    completed.status = RunStatus.COMPLETED
    data._save_report(completed, touch_updated_at=False)

    stale = _stale_running_report(completed.run_id)
    saved = data._save_report(stale)

    assert saved.lifecycle == ExecutionStatus.FINISHED
    assert data.load_report(completed.run_id).status == RunStatus.COMPLETED


def test_expired_active_report_recovers_only_when_global_execution_lease_is_free(
    sentinel_data_root,
):
    from web_app.sentinel.runtime import execution_lease

    data = DataInterface()
    report = _stale_running_report()
    data._save_report(report, touch_updated_at=False)

    with execution_lease() as lease:
        assert lease is not None
        assert data.load_report(report.run_id).lifecycle == ExecutionStatus.RUNNING

    recovered = data.load_report(report.run_id)
    assert recovered.lifecycle == ExecutionStatus.ABANDONED
    assert recovered.status == RunStatus.FAILED


def test_cancel_marker_replacement_preserves_expiry_and_missing_marker_stays_missing(
    sentinel_data_root,
):
    assert not state_replace("cancel/missing", b"1")
    assert state_put("cancel/run", b"0", ttl_s=60, if_absent=True)
    before = get_redis().pttl("nabicat:app:sentinel:state:cancel/run")

    assert state_replace("cancel/run", b"1")
    after = get_redis().pttl("nabicat:app:sentinel:state:cancel/run")
    assert state_get("cancel/run") == b"1"
    assert 0 < after <= before


def test_execution_lease_is_exclusive_and_release_checks_its_token(sentinel_data_root):
    first = ExecutionLease("nabicat:app:sentinel:lease:execution", 60)
    second = ExecutionLease("nabicat:app:sentinel:lease:execution", 60)

    assert first.acquire()
    assert not second.acquire()
    get_redis().set(first.key, b"another-holder", ex=60)
    assert not first.release()
    assert get_redis().get(first.key) == b"another-holder"


def test_start_run_executes_inline_persists_owner_logs_actor_and_releases_lease(
    monkeypatch, caplog, sentinel_data_root
):
    from web_app.sentinel import runner
    from web_app.sentinel.runtime import execution_lease
    from web_app.sentinel.target_policy import ValidatedTarget

    def finish_run(report):
        report.lifecycle = ExecutionStatus.FINISHED
        report.status = RunStatus.COMPLETED
        runner._save(report)
        return report

    monkeypatch.setattr(runner, "execute_run", finish_run)
    caplog.set_level(logging.INFO, logger="sentinel")
    app = Flask("sentinel-runner-test")
    target = ValidatedTarget(
        url="https://example.com/",
        hostname="example.com",
        allowed_hosts=frozenset({"example.com"}),
        addresses={"example.com": ("93.184.216.34",)},
    )

    with app.test_request_context("/sentinel/api/runs"):
        g.request_user = "qa-user"
        report = runner.start_run(
            target=target,
            prompt="Check the home page",
            limit_s=60,
            owner="qa-user",
        )

    persisted = DataInterface().load_report(report.run_id)
    assert persisted.owner == "qa-user"
    assert persisted.status == RunStatus.COMPLETED
    queued_event = next(
        json.loads(record.message)
        for record in caplog.records
        if '"event": "sentinel.run_queued"' in record.message
    )
    assert queued_event["user"] == "qa-user"
    assert queued_event["owner"] == "qa-user"
    with execution_lease() as lease:
        assert lease is not None


def test_blueprint_uses_debug_data_root_selected_after_import(sentinel_data_root):
    from flask import Flask

    from web_app.helpers import login_manager
    from web_app.sentinel import sentinel_api

    app = Flask(
        "sentinel-test",
        template_folder=str(Path(__file__).resolve().parents[3] / "web_app" / "templates"),
    )
    app.secret_key = "test-secret"
    login_manager.init_app(app)
    app.register_blueprint(sentinel_api)
    app.add_url_rule("/", endpoint="home", view_func=lambda: "home")
    client = app.test_client()
    root = sentinel_data_root
    (root / "users.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "users.json").write_text(
        json.dumps(
            [
                {"username": "regular", "password": "x", "folder": "regular"},
                {
                    "username": "elevated",
                    "password": "x",
                    "folder": "elevated",
                    "is_elevated": True,
                },
            ]
        ),
        encoding="utf-8",
    )

    anonymous = client.post("/sentinel/api/runs", json={})
    assert anonymous.status_code == 403

    with client.session_transaction() as session:
        session["_user_id"] = "regular"
    regular_page = client.get("/sentinel/")
    assert regular_page.status_code == 302

    with client.session_transaction() as session:
        session["_user_id"] = "elevated"
    report = Report(run_id="d" * 32)
    DataInterface()._save_report(report)
    response = client.get(f"/sentinel/api/runs/{report.run_id}")
    assert response.status_code == 200
    assert response.json["run_id"] == report.run_id
    assert not (ConfigManager().production_data_root / "sentinel").exists()
