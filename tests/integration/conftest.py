"""Pytest configuration for integration tests."""

from pathlib import Path

import pytest
import requests

from tests.server_fixture import test_server as run_test_server
from web_app.config import ConfigManager


@pytest.fixture(autouse=True)
def bounded_http_requests(monkeypatch):
    """Prevent a stalled integration server from hanging pytest indefinitely."""
    original_request = requests.Session.request

    def request(session, *args, **kwargs):
        kwargs.setdefault("timeout", ConfigManager().integration_request_timeout_s)
        return original_request(session, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", request)


@pytest.fixture(scope="session")
def test_server_instance():
    """Start an isolated Flask and Redis pair for integration tests."""
    project_root = Path(__file__).parent.parent.parent
    with run_test_server(project_root) as server:
        yield server


@pytest.fixture(scope="session")
def server_url(test_server_instance):
    return test_server_instance.base_url


@pytest.fixture(scope="session")
def server_data_root(test_server_instance):
    return test_server_instance.data_root
