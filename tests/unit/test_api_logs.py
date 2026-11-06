"""Admin log retrieval preserves events across pagination and rotation."""

import json

import pytest

from web_app.api_auth import issue_api_token
from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.users import User


@pytest.fixture
def log_api(client):
    users = [User.create(username=name, password='pass', folder=name, is_admin=admin)
             for name, admin in [('admin', True), ('reader', False)]]
    with DataInterface().edit_users() as model:
        model.root = users

    def fetch(options, username='admin'):
        with client.application.app_context():
            token = issue_api_token(username)
        return client.post('/api/logs', json=options, headers={'Authorization': f'Bearer {token}'})

    return fetch


def record(event, level='INFO', app='api', request_id='trace', timestamp='2026-11-07 04:00:00,000'):
    return f'{timestamp} {level} worker=1 thread=1 ' + json.dumps({
        'event': event, 'app': app, 'request_id': request_id,
    }) + '\n'


def test_log_pagination_preserves_tracebacks_and_snapshot_across_rotation(log_api, monkeypatch):
    monkeypatch.setattr(ConfigManager(), 'api_logs_read_chunk_bytes', 17)
    path = ConfigManager().log_file_path
    path.parent.mkdir(parents=True)
    path.write_text(record('older') + record('failure', 'ERROR') + 'Traceback (most recent call last):\nValueError: broken\n')
    first = log_api({'limit': 1}).get_json()
    assert first['events'][0]['fields']['event'] == 'failure'
    assert first['events'][0]['traceback'] == 'Traceback (most recent call last):\nValueError: broken'
    with path.open('a') as output:
        output.write(record('appended'))
    path.rename(path.with_name(path.name + '.1'))
    path.write_text(record('newer'))
    second = log_api({'limit': 1, 'cursor': first['next_cursor']}).get_json()
    assert second['events'][0]['fields']['event'] == 'older'
    last = log_api({'cursor': second['next_cursor']}).get_json()
    assert last == {'events': [], 'next_cursor': None}


def test_log_filters_and_admin_access(log_api):
    path = ConfigManager().log_file_path
    path.parent.mkdir(parents=True)
    path.write_text(record('failure', 'ERROR') + record('failure', 'INFO')
                    + record('failure', 'ERROR', app='other') + record('failure', 'ERROR', request_id='other'))
    response = log_api({'app': 'api', 'event': 'failure', 'request_id': 'trace', 'level': 'error',
                        'since': '2026-11-07T03:00:00', 'until': '2026-11-07T05:00:00'})
    assert response.status_code == 200
    assert len(response.json['events']) == 1
    assert response.json['events'][0]['level'] == 'ERROR'
    assert log_api({}, username='reader').status_code == 403


@pytest.mark.parametrize('options', [
    {'limit': 0}, {'limit': True}, {'limit': 100000}, {'level': 'unknown'},
    {'since': 'bad'}, {'until': []}, {'cursor': 'tampered'}, {'cursor': []},
    {'since': '2026-11-08', 'until': '2026-11-07'},
])
def test_invalid_log_queries_are_rejected(log_api, options):
    assert log_api(options).status_code == 400


def test_missing_logs_and_expired_rotation(log_api):
    assert log_api({}).json == {'events': [], 'next_cursor': None}
    path = ConfigManager().log_file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record('first') + record('last'))
    cursor = log_api({'limit': 1}).json['next_cursor']
    path.unlink()
    assert log_api({'cursor': cursor}).status_code == 400
