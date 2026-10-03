import stat

from flask import Blueprint, Flask
import pytest

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface as HostDataInterface
from web_app.helpers import login_manager
from web_app.users import User
from web_app.jswipe import jswipe_api
from web_app.jswipe.app import create_blueprint
from web_app.jswipe.models import (
    AppState,
    Decision,
    Job,
    ScanSummary,
    SearchSettings,
)
from web_app.jswipe.personalization import CandidateProfile, JSwipeUser, RankingResult, SearchPreferences, UserDataInterface
from web_app.jswipe.scanner import ScannerResult
from web_app.jswipe.storage import JobRepository, user_data_lock


def _temporary_data_root(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ConfigManager,
        "save_data_path",
        property(lambda _self: tmp_path / "data"),
    )


def _test_app(blueprint):
    app = Flask("jswipe-test")
    app.secret_key = "test-secret"
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    login_manager.init_app(app)
    app.register_blueprint(blueprint)
    app.add_url_rule("/", "home", lambda: "home")
    return app


def _login(client, username):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def test_job_repository_writes_plain_per_user_json(monkeypatch, tmp_path):
    _temporary_data_root(monkeypatch, tmp_path)
    user = User.create("admin", "password", "admin-folder", is_admin=True)
    with HostDataInterface().edit_users() as users:
        users.add(user)

    root = ConfigManager().save_data_path / "jswipe" / user.folder
    repository = JobRepository(root, maximum_retained_jobs=10)
    job = Job.from_career_ops(
        {
            "company": "Example",
            "title": "Software Engineer",
            "url": "https://example.com/jobs/1",
            "location": "Sydney",
            "source": "greenhouse",
        },
        discovered_at="2026-10-04T00:00:00+00:00",
    )
    settings = SearchSettings(("Software Engineer",), ("Sydney",), ("greenhouse",), 7)
    summary = ScanSummary("2026-10-04T00:00:00+00:00", 1, 0, 1, 0, False, (), "rev")

    state = repository.merge_scan((job,), settings=settings, summary=summary)
    repository.set_decision(job.job_id, Decision.SHORTLISTED)

    import json

    persisted = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert set(persisted) == {"version", "jobs", "decisions", "settings", "last_scan", "assessments"}
    assert persisted["decisions"][job.job_id] == "shortlisted"
    assert state.last_scan.added == 1
    assert stat.S_IMODE((root / "state.json").stat().st_mode) == ConfigManager().app_data_file_mode


def test_storage_rejects_stale_account_and_symlink_scopes(monkeypatch, tmp_path):
    _temporary_data_root(monkeypatch, tmp_path)
    config = ConfigManager().jswipe
    stale_root = ConfigManager().save_data_path / "jswipe" / "deleted-folder"
    with pytest.raises(PermissionError, match="inactive account"):
        UserDataInterface(stale_root, config).update_profile(
            CandidateProfile(), SearchPreferences(), expected_profile_revision=0
        )

    app_root = ConfigManager().save_data_path / "jswipe"
    app_root.mkdir(parents=True)
    (app_root / "linked-user").symlink_to(stale_root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        with user_data_lock("linked-user"):
            pass

    (app_root / "linked-user").unlink()
    app_root.rmdir()
    external_root = tmp_path / "outside-jswipe"
    external_root.mkdir()
    app_root.symlink_to(external_root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        with user_data_lock("admin-folder"):
            pass


def test_access_gate_and_decision_route_preserve_admin_boundary(monkeypatch, tmp_path):
    _temporary_data_root(monkeypatch, tmp_path)
    regular_user = User.create("regular", "password", "regular-folder")
    admin_user = User.create("admin", "password", "admin-folder", is_admin=True)
    with HostDataInterface().edit_users() as users:
        users.add(regular_user)
        users.add(admin_user)
    users = {regular_user.id: regular_user, admin_user.id: admin_user}
    original_loader = login_manager._user_callback
    login_manager._user_callback = users.get
    root = ConfigManager().save_data_path / "jswipe" / admin_user.folder
    job = Job.from_career_ops(
        {"company": "Example", "title": "Engineer", "url": "https://example.com/job/2"},
        discovered_at="2026-10-04T00:00:00+00:00",
    )
    repository = JobRepository(root, maximum_retained_jobs=10)
    repository.merge_scan(
        (job,),
        settings=SearchSettings(("Engineer",), (), ("greenhouse",), 7),
        summary=ScanSummary("2026-10-04T00:00:00+00:00", 1, 0, 1, 0, False, (), "rev"),
    )
    try:
        app = _test_app(jswipe_api)
        client = app.test_client()
        _login(client, regular_user.id)
        assert client.get("/jswipe/").status_code == 302
        assert client.post("/jswipe/api/resume").status_code == 403
        assert app.test_client().post("/jswipe/api/resume").status_code == 403

        _login(client, admin_user.id)
        response = client.post(
            f"/jswipe/api/jobs/{job.job_id}/decision",
            json={"decision": "shortlisted"},
        )
        assert response.status_code == 200
        assert response.json["jobs"][0]["decision"] == "shortlisted"
    finally:
        login_manager._user_callback = original_loader


def test_scan_route_persists_mocked_scan_without_calling_model(monkeypatch, tmp_path):
    _temporary_data_root(monkeypatch, tmp_path)
    admin = User.create("admin", "password", "admin-scan", is_admin=True)
    with HostDataInterface().edit_users() as users:
        users.add(admin)
    root = ConfigManager().save_data_path / "jswipe" / admin.folder
    UserDataInterface(root, ConfigManager().jswipe).update_profile(
        CandidateProfile(headline="Software Engineer"),
        SearchPreferences(sources=("greenhouse",)),
        expected_profile_revision=0,
    )

    job = Job.from_career_ops(
        {
            "company": "Example",
            "title": "Software Engineer",
            "url": "https://example.com/jobs/3",
            "location": "Sydney",
            "source": "greenhouse",
        },
        discovered_at="2026-10-04T00:00:00+00:00",
    )

    class FakeScanner:
        def search(self, settings):
            return ScannerResult((job,), 1, 0, False, (), "test-revision")

    monkeypatch.setattr(
        JSwipeUser,
        "rank",
        lambda self, jobs: RankingResult({}, 0, 0),
    )
    app = _test_app(create_blueprint(scanner_factory=lambda _config: FakeScanner()))
    original_loader = login_manager._user_callback
    login_manager._user_callback = lambda username: admin if username == admin.id else None
    try:
        client = app.test_client()
        _login(client, admin.id)
        response = client.post(
            "/jswipe/api/scans",
            json={
                "keywords": ["Engineer"],
                "locations": [],
                "sources": ["greenhouse"],
                "since_days": 7,
            },
        )
        assert response.status_code == 200
        assert response.json["last_scan"]["career_ops_revision"] == "test-revision"
        assert response.json["jobs"][0]["job_id"] == job.job_id
        saved = JobRepository(root, maximum_retained_jobs=10).read()
        assert saved.jobs[job.job_id] == job
    finally:
        login_manager._user_callback = original_loader
