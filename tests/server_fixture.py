"""Shared isolated Flask and Redis server for integration and browser tests."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import fakeredis
import redis
import requests

from web_app.config import ConfigManager


@dataclass(frozen=True)
class TestServer:
    base_url: str
    data_root: Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=ConfigManager().test_server_stop_timeout_s)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@contextmanager
def test_server(project_root: Path):
    """Run the app against temporary data and a private Redis TCP endpoint."""
    config = ConfigManager()
    with tempfile.TemporaryDirectory(prefix="nabicat-test-") as temp_dir, ExitStack() as resources:
        temp_path = Path(temp_dir)
        server_log = resources.enter_context((temp_path / "server.log").open("w+b"))
        isolated_redis = resources.enter_context(fakeredis.TcpFakeServer(("127.0.0.1", 0)))
        redis_thread = threading.Thread(target=isolated_redis.serve_forever, daemon=True)
        redis_thread.start()
        redis_url = f"redis://127.0.0.1:{isolated_redis.server_address[1]}/0"
        app_process = None
        redis_client = None
        started = False
        try:
            redis_client = redis.Redis.from_url(redis_url)
            deadline = time.monotonic() + config.redis_readiness_timeout_s
            while time.monotonic() < deadline:
                try:
                    redis_client.ping()
                    break
                except redis.RedisError:
                    time.sleep(config.redis_readiness_poll_s)
            else:
                raise RuntimeError("Isolated test Redis did not become ready")

            port = _free_port()
            env = os.environ.copy()
            env.update(
                REDIS_URL=redis_url,
                NABICAT_TEST_DATA_ROOT=str(temp_path / "data"),
                NABICAT_TEST_PORT=str(port),
                PYTHONPATH=os.pathsep.join(
                    filter(None, [str(project_root), env.get("PYTHONPATH", "")])
                ),
            )
            startup_script = """
import os
from pathlib import Path
from werkzeug.serving import make_server
from web_app.config import ConfigManager

cfg = ConfigManager()
cfg.debug_mode = True
cfg.debug_data_root = Path(os.environ['NABICAT_TEST_DATA_ROOT'])
from web_app.app import create_app
app = create_app({
    'RATELIMIT_ENABLED': False,
    'SESSION_COOKIE_SECURE': False,
    'SECRET_KEY': cfg.flask_secret_key,
})
server = make_server('127.0.0.1', int(os.environ['NABICAT_TEST_PORT']), app, threaded=True)
server.serve_forever()
"""
            app_process = subprocess.Popen(
                [sys.executable, "-u", "-c", startup_script],
                cwd=project_root,
                env=env,
                stdout=server_log,
                stderr=subprocess.STDOUT,
            )
            base_url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + config.test_server_startup_timeout_s
            while time.monotonic() < deadline:
                if app_process.poll() is not None:
                    break
                try:
                    response = requests.get(
                        base_url, timeout=config.test_server_request_timeout_s
                    )
                    response.raise_for_status()
                    break
                except requests.RequestException:
                    time.sleep(config.test_server_poll_interval_s)
            else:
                raise RuntimeError(f"Test server failed to start on port {port}")
            if app_process.poll() is not None:
                raise RuntimeError(f"Test server exited on port {port}")
            started = True
            yield TestServer(base_url=base_url, data_root=temp_path / "data")
        except RuntimeError as error:
            if started:
                raise
            server_log.seek(0)
            diagnostics = server_log.read().decode(errors="replace")
            raise RuntimeError(f"{error}\n{diagnostics}") from error
        finally:
            if app_process is not None:
                _stop(app_process)
            if redis_client is not None:
                redis_client.close()
            isolated_redis.shutdown()
            redis_thread.join(timeout=config.test_server_stop_timeout_s)
