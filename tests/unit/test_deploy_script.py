import io
import json
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from web_app.config import ConfigManager


@pytest.mark.parametrize('health', [
    {'status': 'ok', 'commit': 'previous'},
    {'status': 'error', 'commit': 'release'},
    URLError('unavailable'),
])
def test_deployment_verifies_release_and_skips_later_duplicate(monkeypatch, health):
    from scripts import deploy

    git = Mock(return_value='release\n')
    monkeypatch.setattr(deploy.subprocess, 'check_output', git)
    opened = []
    responses = [health, {'success': True}, {'status': 'ok', 'commit': 'release'}]

    def urlopen(request, *, timeout):
        opened.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(deploy, 'urlopen', urlopen)
    verify = Mock(return_value=True)
    monkeypatch.setattr(deploy, 'wait_for_deployment', verify)
    config = ConfigManager()

    assert deploy.deploy_main('release', config, 'admin', 'secret')
    assert deploy.deploy_main('release', config, 'admin', 'secret')

    assert [request.get_method() for request in opened] == ['GET', 'POST', 'GET']
    assert opened[1].full_url == f'{config.site_url}/api/update'
    assert json.loads(opened[1].data) == {'username': 'admin', 'password': 'secret'}
    verify.assert_called_once_with('release', config)


def test_superseded_release_never_requests_deployment(monkeypatch):
    from scripts import deploy

    monkeypatch.setattr(deploy.subprocess, 'check_output', Mock(return_value='newer\n'))
    request = Mock()
    monkeypatch.setattr(deploy, 'urlopen', request)
    assert deploy.deploy_main('old', ConfigManager(), 'admin', 'secret')
    request.assert_not_called()


@pytest.mark.parametrize('response,verified', [({'success': False}, True), ({'success': True}, False)])
def test_failed_update_or_verification_fails_deployment(monkeypatch, response, verified):
    from scripts import deploy

    monkeypatch.setattr(deploy.subprocess, 'check_output', Mock(return_value='release\n'))
    responses = [{'status': 'ok', 'commit': 'previous'}, response]
    monkeypatch.setattr(deploy, 'urlopen', lambda *args, **kwargs: io.BytesIO(json.dumps(responses.pop(0)).encode()))
    monkeypatch.setattr(deploy, 'wait_for_deployment', Mock(return_value=verified))
    assert not deploy.deploy_main('release', ConfigManager(), 'admin', 'secret')
