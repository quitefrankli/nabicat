import os
import subprocess
import sys
import importlib.util
import base64
import gzip
import io
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from click.testing import CliRunner


@pytest.fixture
def helper(monkeypatch, tmp_path):
    from web_app.config import ConfigManager

    path = tmp_path / "api-helper.json"
    monkeypatch.setattr(ConfigManager(), "api_helper_config_path", path)
    path.write_text(json.dumps({
        "base_url": "https://saved.invalid", "username": "saved-user",
        "access_token": "saved-token", "expires_at": time.time() + 86400,
    }))
    source = Path(__file__).resolve().parents[2] / "scripts" / "api_helper.py"
    spec = importlib.util.spec_from_file_location("api_helper", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("ahead", [False, True])
@pytest.mark.parametrize("answer", ["y", "n"])
def test_update_selects_pull_or_patch_and_requires_confirmation(helper, monkeypatch, ahead, answer):
    repo = MagicMock()
    repo.is_dirty.return_value = False
    repo.head.commit.hexsha = "local" if ahead else "remote"
    repo.remotes.origin.refs.main.commit.hexsha = "remote"
    repo.iter_commits.return_value = [MagicMock()] if ahead else []
    repo.git.format_patch.return_value = "patch contents\n"
    monkeypatch.setattr(helper, "Repo", lambda path: repo)
    send = MagicMock(return_value=MagicMock(status_code=200))
    monkeypatch.setattr(helper, "send_request", send)
    result = CliRunner().invoke(helper.cli, ["update"], input=answer + "\n")
    assert result.exit_code == 0, result.output
    if answer == "y":
        send.assert_called_once_with("api/update", {"patch": "patch contents\n"} if ahead else {})
        assert "Update requested" in result.output
    else:
        send.assert_not_called()
        assert "cancelled" in result.output


def test_download_creates_parent_directories(helper, monkeypatch, tmp_path):
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": base64.b64encode(gzip.compress(b"notes")).decode()}
    monkeypatch.setattr(helper, "send_request", lambda *args: response)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(helper.cli, ["download", "docs/notes.txt"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "docs/notes.txt").read_bytes() == b"notes"


def test_helper_runs_outside_project(tmp_path):
    source = Path(__file__).resolve().parents[2] / "scripts" / "api_helper.py"
    script = tmp_path / "api_helper.py"
    script.write_bytes(source.read_bytes())
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    for arguments in (["--help"], ["llm", "--help"]):
        result = subprocess.run(
            [sys.executable, str(script), *arguments],
            cwd=tmp_path, env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "Usage:" in result.stdout

    rsync = tmp_path / "rsync"
    rsync.write_text(f"#!{sys.executable}\nimport sys\nprint(sys.argv[1:])\n")
    rsync.chmod(0o755)
    env["PATH"] = str(tmp_path) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [sys.executable, str(script), "sync-data-from-prod", "--dry-run", "--dest", str(tmp_path)],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--exclude=backups/" in result.stdout
    assert "--exclude=data/logs/" in result.stdout
    assert "--dry-run" in result.stdout


@pytest.mark.parametrize("options,model,effort", [
    ([], "gpt-6-luna", "low"),
    (["--model", "model", "--effort", "high"], "model", "high"),
])
def test_llm_uses_authenticated_request(monkeypatch, helper, options, model, effort):
    sent = []

    class Response:
        status_code = 200

        def json(self):
            return {"success": True, "output": "answer"}

    def send(endpoint, payload, **kwargs):
        sent.append((endpoint, payload, kwargs))
        return Response()

    monkeypatch.setattr(helper, "send_request", send)
    runner = CliRunner()
    help_result = runner.invoke(helper.cli, ["llm", "--help"])
    assert "--username" not in help_result.output
    assert "Luna 6.0" in help_result.output
    assert "[default: gpt-6-luna]" in help_result.output
    assert "[default: low]" in help_result.output
    result = runner.invoke(helper.cli, ["llm", *options, "hello"])
    assert result.exit_code == 0, result.output
    assert result.output == "answer\n"
    assert sent == [("api/llm", {
        "model": model, "effort": effort, "input": "hello",
    }, {"base_url": None, "timeout": 130.0})]


def test_commit_patch_upload_round_trips_binary_zip(monkeypatch, helper):
    repo = MagicMock()
    repo.is_dirty.return_value = False
    repo.iter_commits.return_value = [MagicMock(hexsha="abcdef123456")]
    repo.git.format_patch.return_value = "patch contents\n"
    monkeypatch.setattr(helper, "Repo", lambda path: repo)
    send = MagicMock()
    send.return_value.status_code = 200
    monkeypatch.setattr(helper, "send_request", send)
    result = CliRunner().invoke(helper.cli, ["upload-commit-patches"], input="y\n")
    assert result.exit_code == 0, result.output
    endpoint, payload = send.call_args.args
    assert endpoint == "api/push"
    assert payload["name"] == "_commit_patches.zip"
    archive = gzip.decompress(base64.b64decode(payload["data"]))
    with helper.zipfile.ZipFile(io.BytesIO(archive)) as patches:
        assert patches.namelist() == ["0001-abcdef1.patch"]
        assert patches.read("0001-abcdef1.patch") == b"patch contents\n"


@pytest.mark.parametrize('path', ['', 'docs'])
def test_ls_shows_dates_newest_first(helper, monkeypatch, path):
    response = MagicMock(status_code=200)
    response.json.return_value = {"entries": [
        {"name": "old.txt", "kind": "file", "uploaded_at": "2026-01-01T10:00:00"},
        {"name": "new.txt", "kind": "file", "uploaded_at": "2026-10-29T12:30:00"},
        {"name": "archive", "kind": "folder", "uploaded_at": ""},
    ]}
    send = MagicMock(return_value=response)
    monkeypatch.setattr(helper, "send_request", send)
    result = CliRunner().invoke(helper.cli, ["ls", *([path] if path else [])])
    assert result.exit_code == 0, result.output
    assert result.output == "Files available (newest first):\n2026-10-29T12:30:00  new.txt\n2026-01-01T10:00:00  old.txt\nUnknown date  archive/\n"
    send.assert_called_once_with('api/list', {'path': path})
    assert 'list-files' not in CliRunner().invoke(helper.cli, ['--help']).output


@pytest.mark.parametrize("command", ["ls", "login"])
@pytest.mark.parametrize("error", [requests.ConnectionError, requests.Timeout])
def test_network_failures_are_cli_errors(monkeypatch, helper, command, error):
    monkeypatch.setattr(helper.requests, "post", MagicMock(side_effect=error("offline")))
    result = CliRunner().invoke(helper.cli, [command], input="admin\npass\nhttps://saved.invalid\n")
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert error.__name__ in result.output
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize("timeout", [None, 42.0])
def test_api_request_has_timeout_and_handles_network_failure(monkeypatch, helper, timeout):
    post = MagicMock()
    monkeypatch.setattr(helper.requests, "post", post)
    kwargs = {} if timeout is None else {"timeout": timeout}
    helper.send_request("api/list", base_url="https://saved.invalid", **kwargs)
    assert post.call_args.kwargs["timeout"] == (130.0 if timeout is None else timeout)
    post.side_effect = requests.Timeout("offline")
    with pytest.raises(helper.click.ClickException, match="Timeout"):
        helper.send_request("api/list", base_url="https://saved.invalid", **kwargs)


@pytest.mark.parametrize("failure", ["expired", "other-server", "http"])
def test_token_is_not_sent_when_expired_or_server_does_not_match(helper, monkeypatch, failure):
    from web_app.config import ConfigManager

    path = ConfigManager().api_helper_config_path
    saved = json.loads(path.read_text())
    if failure == "expired":
        saved["expires_at"] = 0
        path.write_text(json.dumps(saved))
    post = MagicMock()
    monkeypatch.setattr(helper.requests, "post", post)
    options = {"base_url": "https://other.invalid"} if failure == "other-server" else {}
    if failure == "http":
        options["base_url"] = "http://saved.invalid"
    with pytest.raises(helper.click.ClickException):
        helper.send_request("api/list", **options)
    post.assert_not_called()


def test_server_rejection_prompts_for_login_and_does_not_retry(helper, monkeypatch):
    post = MagicMock(return_value=MagicMock(status_code=401))
    monkeypatch.setattr(helper.requests, "post", post)
    result = CliRunner().invoke(helper.cli, ["ls"])
    assert result.exit_code == 1
    assert "api-helper login" in result.output
    assert post.call_count == 1
    assert post.call_args.kwargs["allow_redirects"] is False


def test_config_and_logout_do_not_expose_token(helper):
    from web_app.config import ConfigManager

    runner = CliRunner()
    result = runner.invoke(helper.cli, ["config"])
    assert result.exit_code == 0, result.output
    assert "saved-user" in result.output
    assert "saved-token" not in result.output
    result = runner.invoke(helper.cli, ["logout"])
    assert result.exit_code == 0, result.output
    assert not ConfigManager().api_helper_config_path.exists()
    result = runner.invoke(helper.cli, ["ls"])
    assert result.exit_code == 1
    assert "api-helper login" in result.output
