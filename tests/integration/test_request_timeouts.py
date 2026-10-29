"""Integration HTTP calls fail within a bounded time when a server stalls."""

import pytest
import requests

from web_app.config import ConfigManager


@pytest.mark.parametrize('explicit_timeout', [None, 2])
def test_integration_http_requests_have_a_timeout(monkeypatch, explicit_timeout):
    observed = []

    def send(adapter, request, **kwargs):
        observed.append(kwargs['timeout'])
        response = requests.Response()
        response.status_code = 200
        response._content = b'ok'
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    options = {} if explicit_timeout is None else {'timeout': explicit_timeout}
    assert requests.get('http://integration.invalid/', **options).text == 'ok'
    assert observed == [explicit_timeout or ConfigManager().integration_request_timeout_s]
