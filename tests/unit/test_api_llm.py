"""Exercise the LLM API through authentication and the mocked CLI boundary."""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from web_app.config import ConfigManager
from web_app.users import User, UsersFile


@pytest.fixture
def llm_request(client, monkeypatch):
    import web_app.__main__  # noqa: F401

    users = UsersFile(root=[
        User.create(username="admin", password="pass", folder="admin", is_admin=True),
        User.create(username="user", password="pass", folder="user", is_admin=False),
    ])
    data = MagicMock()
    data.edit_users.return_value.__enter__.return_value = users
    monkeypatch.setattr("web_app.helpers.DataInterface", lambda: data)
    payload = dict(username="admin", password="pass", model="test-model",
                   effort="medium", input="  answer this\n")
    return lambda **changes: client.post("/api/llm", json=payload | changes)


def test_llm_success(llm_request, monkeypatch):
    directories = []

    def run(command, **kwargs):
        directory = Path(kwargs["cwd"])
        directories.append(directory)
        assert directory.is_dir() and not list(directory.iterdir())
        assert directory != ConfigManager().project_dir
        assert kwargs["input"] == "  answer this\n"
        assert command[-1] == "-"
        assert command[command.index("--model") + 1] == "test-model"
        assert 'model_reasoning_effort="medium"' in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "project_doc_max_bytes=0" in command
        assert "--ephemeral" in command
        assert command[command.index("-a") + 1] == "never"
        assert kwargs["timeout"] == ConfigManager().api_llm_timeout_s
        Path(command[command.index("--output-last-message") + 1]).write_text("answer")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("web_app.helpers.subprocess.run", run)
    response = llm_request()
    assert response.status_code == 200
    assert response.json == {"success": True, "output": "answer"}
    assert not directories[0].exists()


@pytest.mark.parametrize("changes", [
    {"username": "user"}, {"password": "wrong"}, {"input": " "},
    {"model": None}, {"effort": 3}, {"input": []}, {"model": ""},
    {"username": []}, {"password": {}},
])
def test_llm_rejections_do_not_launch(llm_request, monkeypatch, changes):
    run = Mock()
    monkeypatch.setattr("web_app.helpers.subprocess.run", run)
    assert llm_request(**changes).status_code == 400
    run.assert_not_called()


@pytest.mark.parametrize("failure,status", [
    (FileNotFoundError("secret diagnostic"), 503),
    (subprocess.TimeoutExpired("secret prompt", 120), 504),
    (subprocess.CompletedProcess([], 1, "", "secret diagnostic"), 502),
    (subprocess.CompletedProcess([], 0, "", ""), 502),
])
def test_llm_failures_are_sanitized(llm_request, monkeypatch, caplog, failure, status):
    directories = []
    outputs = []

    def run(command, **kwargs):
        directories.append(Path(kwargs["cwd"]))
        outputs.append(Path(command[command.index("--output-last-message") + 1]))
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr("web_app.helpers.subprocess.run", run)
    response = llm_request()
    assert response.status_code == status
    assert "secret" not in response.get_data(as_text=True) + caplog.text
    assert "api.llm.failed" in caplog.text
    assert not directories[0].exists()
    assert not outputs[0].exists()


def test_llm_encrypted_request(client, monkeypatch):
    import web_app.__main__  # noqa: F401

    decode = Mock(return_value=dict(username="admin", password="pass",
                                    model="model", effort="high", input="prompt"))
    authenticate = Mock(return_value=True)
    generate = Mock(return_value="answer")
    monkeypatch.setattr("web_app.helpers.decode_decrypt_decompress", decode)
    monkeypatch.setattr("web_app.helpers.authenticate_user", authenticate)
    monkeypatch.setattr("web_app.api.codex_cli_text", generate)
    response = client.post("/api/llm", json={"req": {"session_id": "test"}})
    assert response.json == {"success": True, "output": "answer"}
    authenticate.assert_called_once_with("admin", "pass", require_admin=True)
    assert generate.call_args.args == ("prompt",)


@pytest.mark.parametrize("payload", [[], {"username": "admin", "password": "pass"}])
def test_llm_malformed_or_missing_fields(client, monkeypatch, payload):
    import web_app.__main__  # noqa: F401
    monkeypatch.setattr("web_app.helpers.authenticate_user", Mock(return_value=True))
    generate = Mock()
    monkeypatch.setattr("web_app.api.codex_cli_text", generate)
    assert client.post("/api/llm", json=payload).status_code == 400
    generate.assert_not_called()
