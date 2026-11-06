import os
import subprocess
import sys
import importlib.util
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
        send.assert_called_once_with("api/update", {"patch": "patch contents\n"} if ahead else {}, confirm_payload=False)
        assert "Update requested" in result.output
    else:
        send.assert_not_called()
        assert "cancelled" in result.output


def test_download_creates_parent_directories(helper, monkeypatch, tmp_path):
    response = binary_response(b"notes")
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
    archive = send.call_args.kwargs["upload"].getvalue()
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
    response = MagicMock(status_code=200)
    response.json.return_value = {"success": True}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(helper.requests, "post", post)
    kwargs = {} if timeout is None else {"timeout": timeout}
    helper.send_request("api/list", base_url="https://saved.invalid", **kwargs)
    assert post.call_args.kwargs["timeout"] == (130.0 if timeout is None else timeout)
    post.side_effect = requests.Timeout("offline")
    with pytest.raises(helper.click.ClickException, match="Timeout"):
        helper.send_request("api/list", base_url="https://saved.invalid", **kwargs)


@pytest.mark.parametrize("failure", ["other-server", "http"])
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


def test_config_and_logout_do_not_expose_token(helper, monkeypatch):
    from web_app.config import ConfigManager

    runner = CliRunner()
    result = runner.invoke(helper.cli, ["config"])
    assert result.exit_code == 0, result.output
    assert "saved-user" in result.output
    assert "saved-token" not in result.output
    result = runner.invoke(helper.cli, ["logout"])
    assert result.exit_code == 0, result.output
    assert not ConfigManager().api_helper_config_path.exists()
    monkeypatch.setattr(helper.click, "prompt", MagicMock(side_effect=helper.click.Abort))
    result = runner.invoke(helper.cli, ["ls"])
    assert result.exit_code == 1
    assert "api-helper login" in result.output


def api_response(body, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    response._content_consumed = True
    response.headers['Content-Type'] = 'application/json'
    return response


def binary_response(data):
    response = requests.Response()
    response.status_code = 200
    response._content = data
    response._content_consumed = True
    response.headers['Content-Type'] = 'application/octet-stream'
    return response


def upload_post_mock():
    payloads = []

    def send(url, *, data, headers, params, **kwargs):
        assert headers['Content-Type'] == 'application/octet-stream'
        body = data.read()
        encoding = headers['Content-Encoding']
        payloads.append({**params, 'data': gzip.decompress(body) if encoding == 'gzip' else body,
                         'encoding': encoding})
        return api_response({'success': True})

    post = MagicMock(side_effect=send)
    post.payloads = payloads
    return post


@pytest.mark.parametrize('known_length', [True, False])
@pytest.mark.parametrize('filename', ['binary.dat', 'video.MP4'])
def test_transfer_progress_round_trip(helper, monkeypatch, tmp_path, known_length, filename):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from contextlib import contextmanager

    stored = {}
    transfers = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            from urllib.parse import urlsplit, parse_qs

            url = urlsplit(self.path)
            body = self.rfile.read(int(self.headers['Content-Length']))
            if url.path == '/api/push':
                stored['name'] = parse_qs(url.query)['name'][0]
                stored['encoding'] = self.headers['Content-Encoding']
                stored['data'] = gzip.decompress(body) if stored['encoding'] == 'gzip' else body
                stored['wire_size'] = len(body)
                transfers.append(len(body))
                response = json.dumps({'success': True}).encode()
                content_type = 'application/json'
            else:
                assert json.loads(body)['name'] == stored['name']
                response = stored['data']
                content_type = 'application/octet-stream'
                transfers.append(len(response))
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            if known_length:
                self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    bars = []
    original_bar = helper.click.progressbar

    @contextmanager
    def track_progress(**kwargs):
        with original_bar(**kwargs) as bar:
            update = bar.update
            counts = []
            bar.update = lambda count: (counts.append(count), update(count))[-1]
            bars.append((kwargs, counts))
            yield bar

    monkeypatch.setattr(helper.click, 'progressbar', track_progress)
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setattr(helper, '_authenticate', lambda **kwargs: (
            f'http://127.0.0.1:{server.server_port}', 'test-token',
        ))
        source = tmp_path / filename
        source.write_bytes(os.urandom(200_000))
        runner = CliRunner(mix_stderr=False)
        try:
            upload = runner.invoke(helper.cli, ['upload', str(source)])
            assert upload.exit_code == 0, upload.output
            download = runner.invoke(helper.cli, ['download', source.name, '--raw'])
            assert download.exit_code == 0, download.stderr
            assert download.stdout_bytes == source.read_bytes()
        finally:
            server.shutdown()
            thread.join()
    assert [options['label'] for options, counts in bars] == ['Uploading', 'Downloading']
    assert [sum(counts) for options, counts in bars] == transfers
    assert all(len(counts) > 1 for options, counts in bars)
    assert bars[0][0]['length'] == transfers[0]
    assert bars[1][0]['length'] == (transfers[1] if known_length else None)
    assert stored['encoding'] == ('identity' if filename.endswith('MP4') else 'gzip')
    if filename.endswith('MP4'):
        assert stored['wire_size'] == source.stat().st_size


@pytest.mark.parametrize('state', ['missing', 'expired'])
def test_command_logs_in_and_continues_without_saving_password(helper, monkeypatch, state):
    from web_app.config import ConfigManager

    path = ConfigManager().api_helper_config_path
    if state == 'missing':
        path.unlink()
    else:
        saved = json.loads(path.read_text())
        saved['expires_at'] = 0
        path.write_text(json.dumps(saved))
    post = MagicMock(side_effect=[
        api_response({'access_token': 'fresh-token', 'expires_in': 86400}),
        api_response({'success': True, 'entries': []}),
    ])
    monkeypatch.setattr(helper.requests, 'post', post)
    result = CliRunner(mix_stderr=False).invoke(helper.cli, ['ls', '--json'], input='alice\nsecret-password\n')
    assert result.exit_code == 0, (result.stdout + result.stderr)
    assert json.loads(result.stdout) == []
    assert post.call_args_list[0].kwargs['json'] == {'username': 'alice', 'password': 'secret-password'}
    assert post.call_args_list[1].kwargs['headers'] == {'Authorization': 'Bearer fresh-token'}
    assert 'secret-password' not in path.read_text()
    assert 'secret-password' not in (result.stdout + result.stderr)
    assert path.stat().st_mode & 0o777 == 0o600


def test_login_options_and_rejected_credentials_stop_original_command(helper, monkeypatch):
    post = MagicMock(return_value=api_response({'error': 'invalid credentials'}, 401))
    monkeypatch.setattr(helper.requests, 'post', post)
    result = CliRunner(mix_stderr=False).invoke(helper.cli, ['login', '--username', 'alice', '--base-url', 'https://other.invalid'], input='wrong-password\n')
    assert result.exit_code == 1
    assert 'Login failed (HTTP 401)' in (result.stdout + result.stderr)
    assert post.call_args.args == ('https://other.invalid/api/token',)
    assert 'wrong-password' not in (result.stdout + result.stderr)

    from web_app.config import ConfigManager
    ConfigManager().api_helper_config_path.unlink()
    post.reset_mock()
    result = CliRunner(mix_stderr=False).invoke(helper.cli, ['ls'], input='alice\nwrong-password\n')
    assert result.exit_code == 1
    assert post.call_count == 1


@pytest.mark.parametrize('command', [['backup'], ['delete-file', 'notes.txt'], ['upload', 'notes.txt']])
@pytest.mark.parametrize('status', [403, 500])
def test_server_failure_has_nonzero_exit(helper, monkeypatch, tmp_path, command, status):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'notes.txt').write_text('notes')
    monkeypatch.setattr(helper.requests, 'post', MagicMock(return_value=api_response({'error': 'rejected'}, status)))
    result = CliRunner(mix_stderr=False).invoke(helper.cli, command)
    assert result.exit_code == 1
    assert f'HTTP {status}' in (result.stdout + result.stderr)
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize('arguments,body,message', [
    (['ls'], [], 'expected an object'),
    (['ls'], {'entries': [None]}, 'invalid directory listing'),
    (['ls'], {'entries': [{'name': 'a', 'kind': 'file', 'uploaded_at': 'bad-date'}]}, 'invalid directory listing'),
    (['download', 'notes'], {'data': 'legacy JSON'}, 'invalid file response'),
    (['backup'], {'success': False, 'error': 'operation rejected'}, 'operation rejected'),
])
def test_invalid_responses_are_readable_errors(helper, monkeypatch, tmp_path, arguments, body, message):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(helper.requests, 'post', MagicMock(return_value=api_response(body)))
    result = CliRunner(mix_stderr=False).invoke(helper.cli, arguments)
    assert result.exit_code == 1
    assert message in (result.stdout + result.stderr)
    assert isinstance(result.exception, SystemExit)
    assert not (tmp_path / 'notes').exists()


@pytest.mark.parametrize('yes_position', ['global', 'command'])
def test_multiple_uploads_preserve_contents_and_directory_suffix(helper, monkeypatch, tmp_path, yes_position):
    first = tmp_path / 'notes.txt'
    first.write_bytes(b'notes')
    folder = tmp_path / 'reports.v2'
    folder.mkdir()
    (folder / 'nested.txt').write_bytes(b'report')
    (folder / 'photo.JPG').write_bytes(b'compressed image')
    post = upload_post_mock()
    monkeypatch.setattr(helper.requests, 'post', post)
    args = ['upload', str(first), str(folder), '--to', 'travel/']
    args = ['--yes', *args] if yes_position == 'global' else [*args, '--yes']
    result = CliRunner(mix_stderr=False).invoke(helper.cli, args)
    assert result.exit_code == 0, (result.stdout + result.stderr)
    uploaded = {payload['name']: payload['data'] for payload in post.payloads}
    assert uploaded['travel/notes.txt'] == b'notes'
    with helper.zipfile.ZipFile(io.BytesIO(uploaded['travel/reports.v2.zip'])) as archive:
        assert archive.namelist() == ['nested.txt', 'photo.JPG']
        assert archive.read('nested.txt') == b'report'
        assert archive.read('photo.JPG') == b'compressed image'
        assert archive.getinfo('nested.txt').compress_type == helper.zipfile.ZIP_DEFLATED
        assert archive.getinfo('photo.JPG').compress_type == helper.zipfile.ZIP_STORED
    assert post.payloads[-1]['encoding'] == 'identity'
    assert 'Preparing' in result.stderr
    assert 'Uploading' in result.stderr


def test_upload_destination_and_size_are_checked_before_sending(helper, monkeypatch, tmp_path):
    from web_app.config import ConfigManager

    monkeypatch.setattr(ConfigManager(), 'api_helper_upload_confirm_bytes', 4)
    first = tmp_path / 'a.txt'
    second = tmp_path / 'b.txt'
    first.write_bytes(b'12345')
    second.write_bytes(b'other')
    post = upload_post_mock()
    monkeypatch.setattr(helper.requests, 'post', post)
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(helper.cli, ['upload', str(first), str(second), '--to', 'one.txt'])
    assert result.exit_code == 2
    result = runner.invoke(helper.cli, ['upload', str(first)], input='n\n')
    assert result.exit_code == 1
    assert '5 bytes before compression' in result.stderr
    assert 'Upload cancelled' in result.stderr
    assert 'Preparing' not in result.stderr
    post.assert_not_called()
    result = runner.invoke(helper.cli, ['upload', str(first), '--to', 'renamed.txt'], input='y\n')
    assert result.exit_code == 0, (result.stdout + result.stderr)
    assert post.payloads[-1]['name'] == 'renamed.txt'


@pytest.mark.parametrize('size,options,prompted', [
    (4, [], False),
    (5, [], True),
    (5, ['--yes'], False),
])
def test_upload_confirmation_threshold(helper, monkeypatch, tmp_path, size, options, prompted):
    from web_app.config import ConfigManager

    monkeypatch.setattr(ConfigManager(), 'api_helper_upload_confirm_bytes', 4)
    source = tmp_path / 'file.txt'
    source.write_bytes(b'x' * size)
    post = upload_post_mock()
    monkeypatch.setattr(helper.requests, 'post', post)
    result = CliRunner(mix_stderr=False).invoke(
        helper.cli, ['upload', str(source), *options], input='y\n',
    )
    assert result.exit_code == 0, result.stderr
    assert ('before compression)?' in result.stderr) is prompted
    assert post.payloads[-1]['data'] == source.read_bytes()


@pytest.mark.parametrize('options,answer,expected', [
    ([], 'n\n', b'original'),
    ([], 'y\n', b'new data'),
    (['--force'], '', b'new data'),
    (['--yes'], '', b'new data'),
])
def test_download_output_protects_existing_files(helper, monkeypatch, tmp_path, options, answer, expected):
    target = tmp_path / 'local.txt'
    target.write_bytes(b'original')
    post = MagicMock(return_value=binary_response(b'new data'))
    monkeypatch.setattr(helper.requests, 'post', post)
    result = CliRunner(mix_stderr=False).invoke(helper.cli, ['download', 'remote/file.txt', '-o', str(target), *options], input=answer)
    assert result.exit_code == (1 if answer == 'n\n' else 0), (result.stdout + result.stderr)
    assert target.read_bytes() == expected
    if answer == 'n\n':
        post.assert_not_called()
    assert sorted(p.name for p in tmp_path.iterdir()) == ['api-helper.json', 'local.txt']


def test_failed_atomic_download_preserves_original_and_cleans_up(helper, monkeypatch, tmp_path):
    target = tmp_path / 'local.txt'
    target.write_bytes(b'original')
    monkeypatch.setattr(helper.requests, 'post', MagicMock(return_value=binary_response(b'new')))
    monkeypatch.setattr(helper.os, 'replace', MagicMock(side_effect=OSError('disk failure')))
    result = CliRunner(mix_stderr=False).invoke(helper.cli, ['download', 'remote.txt', '-o', str(target), '--force'])
    assert result.exit_code == 1
    assert 'disk failure' in (result.stdout + result.stderr)
    assert target.read_bytes() == b'original'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['api-helper.json', 'local.txt']


def test_interrupted_download_preserves_original_and_cleans_up(helper, monkeypatch, tmp_path):
    target = tmp_path / 'video.mp4'
    target.write_bytes(b'original')
    response = binary_response(b'')

    def interrupted_chunks(*args):
        yield b'partial transfer'
        raise requests.ConnectionError('connection lost')

    response.iter_content = interrupted_chunks
    monkeypatch.setattr(helper.requests, 'post', MagicMock(return_value=response))
    result = CliRunner(mix_stderr=False).invoke(
        helper.cli, ['download', 'remote.mp4', '-o', str(target), '--force'],
    )
    assert result.exit_code == 1
    assert 'Download failed (ConnectionError)' in result.stderr
    assert target.read_bytes() == b'original'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['api-helper.json', 'video.mp4']


def test_raw_download_and_json_listing_keep_stdout_machine_readable(helper, monkeypatch):
    entries = [{'name': 'a.txt', 'kind': 'file', 'uploaded_at': '', 'size': 1234}]
    post = MagicMock(side_effect=[
        binary_response(b'\x00binary\xff'),
        api_response({'entries': entries}), api_response({'entries': entries}),
    ])
    monkeypatch.setattr(helper.requests, 'post', post)
    runner = CliRunner(mix_stderr=False)
    assert runner.invoke(helper.cli, ['download', 'a.txt', '--raw']).stdout_bytes == b'\x00binary\xff'
    assert json.loads(runner.invoke(helper.cli, ['ls', '--json']).stdout) == entries
    assert '1,234 bytes' in runner.invoke(helper.cli, ['ls']).stdout


def test_stdin_llm_does_not_consume_prompt_as_login_credentials(helper, monkeypatch):
    from web_app.config import ConfigManager
    ConfigManager().api_helper_config_path.unlink()
    post = MagicMock()
    monkeypatch.setattr(helper.requests, 'post', post)
    result = CliRunner(mix_stderr=False).invoke(helper.cli, ['llm', '-'], input='private prompt text\n')
    assert result.exit_code == 1
    assert "api-helper login" in (result.stdout + result.stderr)
    assert 'private prompt text' not in (result.stdout + result.stderr)
    post.assert_not_called()


def test_patch_failure_reports_committed_prefix_and_aborts_failed_patch(helper, monkeypatch, tmp_path):
    from git import Repo

    source = Repo.init(tmp_path / 'source')
    with source.config_writer() as config:
        config.set_value('user', 'name', 'Test User').set_value('user', 'email', 'test@example.invalid')
    shared = Path(source.working_tree_dir) / 'shared.txt'
    shared.write_text('base\n')
    source.index.add(['shared.txt'])
    source.index.commit('Base')
    target = Repo.clone_from(source.working_tree_dir, tmp_path / 'target')
    with target.config_writer() as config:
        config.set_value('user', 'name', 'Test User').set_value('user', 'email', 'test@example.invalid')
    (Path(target.working_tree_dir) / 'shared.txt').write_text('local change\n')
    target.index.add(['shared.txt'])
    target.index.commit('Local change')

    (Path(source.working_tree_dir) / 'new.txt').write_text('first patch\n')
    source.index.add(['new.txt'])
    first = source.index.commit('First patch')
    shared.write_text('conflicting change\n')
    source.index.add(['shared.txt'])
    second = source.index.commit('Conflicting patch')
    with io.BytesIO() as buffer:
        with helper.zipfile.ZipFile(buffer, 'w') as archive:
            for index, commit in enumerate([first, second], start=1):
                archive.writestr(f'{index:04d}.patch', source.git.format_patch('-1', commit.hexsha, stdout=True))
        archive_data = buffer.getvalue()
    monkeypatch.setattr(helper.requests, 'post', MagicMock(return_value=binary_response(archive_data)))
    monkeypatch.chdir(target.working_tree_dir)
    result = CliRunner(mix_stderr=False).invoke(helper.cli, ['apply-remote-patches', '--yes'])
    assert result.exit_code == 1, (result.stdout + result.stderr)
    assert 'Failed to apply 0002.patch' in (result.stdout + result.stderr)
    assert '1 earlier patch(es) remain committed' in (result.stdout + result.stderr)
    assert 'Failed patch aborted' in (result.stdout + result.stderr)
    assert target.head.commit.message.strip() == 'First patch'
    assert (Path(target.working_tree_dir) / 'new.txt').read_text() == 'first patch\n'
    assert (Path(target.working_tree_dir) / 'shared.txt').read_text() == 'local change\n'
    assert not target.is_dirty(untracked_files=True)
    assert not (Path(target.git_dir) / 'rebase-apply').exists()


def test_invalid_json_response_is_a_cli_error(helper, monkeypatch):
    response = api_response({})
    response._content = b'<html>upstream error</html>'
    monkeypatch.setattr(helper.requests, 'post', MagicMock(return_value=response))
    result = CliRunner().invoke(helper.cli, ['backup'])
    assert result.exit_code == 1
    assert 'Server returned invalid JSON' in result.output
    assert isinstance(result.exception, SystemExit)


def test_post_upload_uses_shared_archive_checks_and_preserves_metadata(helper, monkeypatch, tmp_path):
    folder = tmp_path / 'post.v2'
    folder.mkdir()
    (folder / 'index.html').write_text('post content')
    post = upload_post_mock()
    monkeypatch.setattr(helper.requests, 'post', post)
    runner = CliRunner()
    args = ['upload-post', str(folder), '--project', 'travel', '--name', 'my-post', '--yes']
    result = runner.invoke(helper.cli, [*args, '--date', 'invalid-date'])
    assert result.exit_code == 2
    post.assert_not_called()
    result = runner.invoke(helper.cli, [*args, '--max-size', '1'])
    assert result.exit_code == 2
    post.assert_not_called()
    result = runner.invoke(helper.cli, [*args, '--date', '2026-11-04'])
    assert result.exit_code == 0, result.output
    payload = post.payloads[-1]
    assert (payload['project'], payload['post_name'], payload['date']) == ('travel', 'my-post', '2026-11-04')
    assert payload['encoding'] == 'identity'
    with helper.zipfile.ZipFile(io.BytesIO(payload['data'])) as archive:
        assert archive.read('index.html') == b'post content'


def test_large_payload_confirmation_can_be_accepted_or_skipped(helper, monkeypatch):
    from web_app.config import ConfigManager
    monkeypatch.setattr(ConfigManager(), 'api_helper_payload_confirm_bytes', 1)
    post = MagicMock(return_value=api_response({'success': True, 'output': 'answer'}))
    monkeypatch.setattr(helper.requests, 'post', post)
    runner = CliRunner()
    result = runner.invoke(helper.cli, ['llm', 'hello'], input='n\n')
    assert result.exit_code == 1
    post.assert_not_called()
    for options, answer in [([], 'y\n'), (['--yes'], '')]:
        result = runner.invoke(helper.cli, ['llm', 'hello', *options], input=answer)
        assert result.exit_code == 0, result.output
    assert post.call_count == 2


def test_cookie_sync_only_sends_supported_domains_and_reports_browser_failure(helper, monkeypatch):
    from types import SimpleNamespace

    cookies = [SimpleNamespace(domain=domain, secure=True, path='/', expires=None, name='cookie', value=domain)
               for domain in ['.youtube.com', 'accounts.google.com', 'evilyoutube.com']]
    extract = MagicMock(return_value=cookies)
    post = MagicMock(return_value=api_response({'success': True}))
    monkeypatch.setattr(helper, 'extract_cookies_from_browser', extract)
    monkeypatch.setattr(helper.requests, 'post', post)
    result = CliRunner().invoke(helper.cli, ['sync-cookies', '--yes'])
    assert result.exit_code == 0, result.output
    content = post.call_args.kwargs['json']['cookie']
    assert '.youtube.com' in content
    assert 'accounts.google.com' in content
    assert 'evilyoutube.com' not in content
    extract.side_effect = helper.CookieLoadError('unavailable')
    result = CliRunner().invoke(helper.cli, ['sync-cookies', '--yes'])
    assert result.exit_code == 1
    assert 'Cannot read browser cookies' in result.output
    assert post.call_count == 1


def test_upload_current_directory_uses_its_actual_name(helper, monkeypatch, tmp_path):
    folder = tmp_path / 'reports.v2'
    folder.mkdir()
    (folder / 'notes.txt').write_text('notes')
    monkeypatch.chdir(folder)
    post = upload_post_mock()
    monkeypatch.setattr(helper.requests, 'post', post)
    result = CliRunner().invoke(helper.cli, ['upload', '.', '--yes'])
    assert result.exit_code == 0, result.output
    assert post.payloads[-1]['name'] == 'reports.v2.zip'


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='Requires POSIX named pipes')
def test_upload_rejects_special_files(helper, monkeypatch, tmp_path):
    pipe = tmp_path / 'pipe'
    os.mkfifo(pipe)
    post = MagicMock()
    monkeypatch.setattr(helper.requests, 'post', post)
    result = CliRunner().invoke(helper.cli, ['upload', str(pipe)])
    assert result.exit_code == 1
    assert 'Not a regular file or directory' in result.output
    post.assert_not_called()


@pytest.mark.parametrize('as_json', [False, True])
def test_logs_paginate_filters_and_write_output(helper, monkeypatch, tmp_path, as_json):
    def event(name):
        return {'timestamp': '2026-11-07T04:00:00+00:00', 'level': 'ERROR',
                'fields': {'app': 'api', 'event': name, 'request_id': 'trace'},
                'message': name, 'traceback': 'ValueError: broken'}

    pages = [
        {'events': [event('first')], 'next_cursor': 'page2'},
        {'events': [event('second')], 'next_cursor': None},
    ]
    calls = []

    def send(endpoint, payload, **kwargs):
        calls.append((endpoint, dict(payload)))
        response = MagicMock()
        response.__enter__.return_value = response
        response.json.return_value = pages.pop(0)
        return response

    monkeypatch.setattr(helper, 'send_request', send)
    output = tmp_path / 'logs.txt'
    args = ['logs', '-n', '2', '--since', '1h', '--level', 'error', '--app', 'api',
            '--event', 'request.exception', '--request-id', 'trace', '-o', str(output)]
    result = CliRunner().invoke(helper.cli, args + (['--json'] if as_json else []))
    assert result.exit_code == 0, result.output
    assert result.output == ''
    assert calls[0][0] == 'api/logs'
    assert calls[0][1]['limit'] == 2
    assert calls[0][1]['app'] == 'api'
    assert calls[0][1]['event'] == 'request.exception'
    assert calls[0][1]['request_id'] == 'trace'
    assert 'T' in calls[0][1]['since']
    assert calls[1][1] == {'cursor': 'page2', 'limit': 1}
    text = output.read_text()
    if as_json:
        assert [json.loads(line)['fields']['event'] for line in text.splitlines()] == ['first', 'second']
    else:
        assert 'request_id=trace' in text
        assert text.count('ValueError: broken') == 2
