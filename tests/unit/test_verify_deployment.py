import io
import json
from urllib.error import URLError

import pytest

from web_app.config import ConfigManager


@pytest.mark.parametrize("updated", [True, False])
def test_public_deployment_verification_retries_until_exact_commit_or_timeout(
    monkeypatch, capsys, updated,
):
    from scripts import verify_deployment

    config = ConfigManager()
    monkeypatch.setattr(config, "deployment_verification_timeout_s", 4)
    monkeypatch.setattr(config, "deployment_verification_interval_s", 1)
    monkeypatch.setattr(config, "deployment_verification_request_timeout_s", 1)
    clock = [0]
    monkeypatch.setattr(verify_deployment.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        verify_deployment.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    responses = [
        URLError("temporarily unavailable"),
        b"not json",
        json.dumps({"status": "ok", "commit": "previous"}).encode(),
        json.dumps({"status": "ok", "commit": "release" if updated else "previous"}).encode(),
    ]
    calls = []

    def open_health(request, *, timeout):
        calls.append((request, timeout))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response)

    monkeypatch.setattr(verify_deployment, "urlopen", open_health)

    assert verify_deployment.wait_for_deployment("release", config) is updated
    assert calls
    assert all(request.full_url == f"{config.site_url}/api/health" for request, _ in calls)
    assert all(request.get_header("Cache-control") == "no-cache" for request, _ in calls)
    assert all(timeout <= 1 for _, timeout in calls)
    output = capsys.readouterr().out
    assert "release" in output
    assert ("Verified" if updated else "Timed out") in output
