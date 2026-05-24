from unittest.mock import patch

import pytest

from web_app.app import app
from web_app.config import ConfigManager
from web_app.helpers import limiter
from web_app.sentinel import _limit_from_report, _limit_from_request
from web_app.sentinel.actions import ActionValidationError, parse_agent_action
from web_app.sentinel.runner import _add_finding, _build_codex_cmd, _codex_text, _final_report_prompt, _host_allowed
from web_app.sentinel.target_policy import TargetValidationError, validate_public_web_url
from web_app.users import User


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.secret_key = "test-secret"
    limiter.enabled = False
    with app.test_client() as client:
        yield client


def _addrinfo(ip: str):
    return [(2, 1, 6, "", (ip, 443))]


def test_validate_public_web_url_rejects_local_and_private_targets():
    bad_urls = [
        "ftp://example.com",
        "http://localhost",
        "http://127.0.0.1",
        "http://10.0.0.5",
        "http://192.168.1.2",
        "http://[::1]",
    ]

    for url in bad_urls:
        with pytest.raises(TargetValidationError):
            validate_public_web_url(url)

    with patch("web_app.sentinel.target_policy.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert validate_public_web_url("https://example.com/path").url == "https://example.com/path"
        assert validate_public_web_url("example.com/path").url == "https://example.com/path"
        assert (
            validate_public_web_url("https://user:pass@example.com/path").url
            == "https://user:pass@example.com/path"
        )


def test_parse_agent_action_allows_only_known_actions_and_elements():
    action = parse_agent_action('{"action": "click", "element_id": "e1", "reason": "Open nav"}', {"e1"})
    assert action.action == "click"
    assert action.element_id == "e1"

    with pytest.raises(ActionValidationError):
        parse_agent_action('{"action": "delete_database"}', {"e1"})

    with pytest.raises(ActionValidationError):
        parse_agent_action('{"action": "click", "element_id": "missing"}', {"e1"})


def test_runner_allows_only_exact_host_or_www_redirect_variant():
    assert _host_allowed("google.com", "google.com")
    assert _host_allowed("www.google.com", "google.com")
    assert _host_allowed("google.com", "www.google.com")
    assert not _host_allowed("accounts.google.com", "google.com")
    assert not _host_allowed("example.com", "google.com")


def test_codex_command_includes_screenshot_images(tmp_path):
    output = tmp_path / "out.txt"
    image = tmp_path / "step.png"
    image.write_bytes(b"png")

    cmd = _build_codex_cmd(str(output), [image])

    assert "--image" in cmd
    assert str(image) in cmd
    assert cmd.index("--image") < cmd.index("--output-last-message")


def test_codex_command_disables_project_docs_and_uses_minimal_permissions(tmp_path):
    cmd = _build_codex_cmd(str(tmp_path / "out.txt"))

    assert "-c" in cmd
    assert "project_doc_max_bytes=0" in cmd
    assert "project_doc_fallback_filenames=[]" in cmd
    assert 'default_permissions="sentinel_qa"' in cmd
    assert 'permissions.sentinel_qa.filesystem.:minimal="read"' in cmd
    assert "--sandbox" not in cmd
    assert "--ignore-rules" in cmd


def test_codex_runs_from_project_dir_with_project_docs_disabled():
    class DummyOutput:
        name = "/tmp/sentinel-output.txt"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def seek(self, _pos):
            return None

        def read(self):
            return "done"

    with patch("web_app.sentinel.runner.tempfile.NamedTemporaryFile", return_value=DummyOutput()), patch(
        "web_app.sentinel.runner.subprocess.run"
    ) as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        mock_run.return_value.stdout = ""

        _codex_text("system", "prompt", image_paths=None, timeout_s=1)

    assert mock_run.call_args.kwargs["cwd"] == str(ConfigManager().project_dir)
    assert "project_doc_max_bytes=0" in mock_run.call_args.args[0]


def test_time_limit_input_is_minutes_capped_at_ten():
    assert _limit_from_request("1") == 60
    assert _limit_from_request("10") == 600
    assert _limit_from_request("99") == 600
    assert _limit_from_report({"limit_s": 9999}) == 600


def test_finding_details_are_truncated_and_single_line():
    report = {"findings": []}
    _add_finding(report, "info", "Console", "x" * 1000 + "\n" + "y" * 1000)

    detail = report["findings"][0]["detail"]
    assert "\n" not in detail
    assert len(detail) <= 503
    assert detail.endswith("...")


def test_final_report_prompt_directly_includes_original_prompt():
    prompt = _final_report_prompt(
        {
            "prompt": "Does checkout work?",
            "target_url": "https://example.com",
            "status": "completed",
            "steps": [{"action": "click", "reason": "Open cart", "result": {"ok": True}}],
            "findings": [{"severity": "error", "title": "Broken", "detail": "Button failed"}],
            "screenshots": ["screenshots/step-01.png"],
        }
    )

    assert "Does checkout work?" in prompt
    assert "Button failed" in prompt


def test_sentinel_routes_require_admin_and_start_run(client):
    non_admin = User(username="user", password="pass", folder="uf", is_admin=False)
    admin = User(username="admin", password="pass", folder="af", is_admin=True)

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"user": non_admin, "admin": admin}
        with client.session_transaction() as sess:
            sess["_user_id"] = "user"
        assert client.get("/sentinel/").status_code == 403

        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        with patch("web_app.sentinel.validate_public_web_url") as mock_validate, patch(
            "web_app.sentinel.start_run"
        ) as mock_start:
            mock_validate.return_value.url = "https://example.com"
            mock_start.return_value = {"run_id": "run-123", "status": "queued"}

            res = client.post("/sentinel/api/runs", json={"url": "https://example.com", "limit": 5})

    assert res.status_code == 202
    assert res.get_json()["run_id"] == "run-123"
    mock_start.assert_called_once()


def test_sentinel_rerun_starts_from_existing_report(client):
    admin = User(username="admin", password="pass", folder="af", is_admin=True)
    report = {"target_url": "https://example.com", "prompt": "check nav", "limit_s": 120}

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"admin": admin}
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        with patch("web_app.sentinel.get_run", return_value=report), patch(
            "web_app.sentinel.validate_public_web_url"
        ) as mock_validate, patch("web_app.sentinel.start_run") as mock_start:
            mock_start.return_value = {"run_id": "new-run", "status": "queued"}

            res = client.post("/sentinel/api/runs/old-run/rerun")

    assert res.status_code == 202
    assert res.get_json()["run_id"] == "new-run"
    mock_validate.assert_called_once_with("https://example.com")
    mock_start.assert_called_once_with(mock_validate.return_value, "check nav", 120)
