import os
import subprocess
import sys
import importlib.util
import base64
import gzip
import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from click.testing import CliRunner


@pytest.fixture
def helper():
    source = Path(__file__).resolve().parents[2] / "scripts" / "api_helper.py"
    spec = importlib.util.spec_from_file_location("api_helper", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
def test_llm_always_uses_cached_credentials(monkeypatch, helper, options, model, effort):
    monkeypatch.setattr(helper, "generate_cred_payload", lambda: {"username": "saved-user", "password": "saved-password"})
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
        "username": "saved-user", "password": "saved-password",
        "model": model, "effort": effort, "input": "hello",
    }, {"require_cred": False, "base_url": None, "timeout": 130.0})]


def test_handshake_command_uses_saved_url(monkeypatch, helper):
    monkeypatch.setattr(helper, "get_base_url", lambda: "https://saved.invalid")
    post = MagicMock()
    post.return_value.status_code = 200
    post.return_value.json.return_value = {"success": True, "session_id": "session", "public_key": "key"}
    monkeypatch.setattr(helper.requests, "post", post)
    result = CliRunner().invoke(helper.cli, ["test-handshake"])
    assert result.exit_code == 0, result.output
    assert "Handshake successful!" in result.output
    post.assert_called_once_with("https://saved.invalid/api/handshake", timeout=10)


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


@pytest.mark.parametrize("command", ["list-files", "test-handshake", "config"])
@pytest.mark.parametrize("error", [requests.ConnectionError, requests.Timeout])
def test_network_failures_are_cli_errors(monkeypatch, helper, command, error):
    monkeypatch.setattr(helper.keyring, "get_password", lambda *args: "saved")
    monkeypatch.setattr(helper.requests, "post", MagicMock(side_effect=error("offline")))
    result = CliRunner().invoke(helper.cli, [command])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert error.__name__ in result.output
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize("timeout", [None, 42.0])
def test_api_request_has_timeout_and_handles_network_failure(monkeypatch, helper, timeout):
    monkeypatch.setattr(helper, "do_handshake", lambda base_url: ("session", "key"))
    monkeypatch.setattr(helper, "hybrid_encrypt", lambda *args: {"encrypted": "data"})
    post = MagicMock()
    monkeypatch.setattr(helper.requests, "post", post)
    kwargs = {} if timeout is None else {"timeout": timeout}
    helper.send_request("api/list", require_cred=False, base_url="https://saved.invalid", **kwargs)
    assert post.call_args.kwargs["timeout"] == (130.0 if timeout is None else timeout)
    post.side_effect = requests.Timeout("offline")
    with pytest.raises(helper.click.ClickException, match="Timeout"):
        helper.send_request("api/list", require_cred=False, base_url="https://saved.invalid", **kwargs)
