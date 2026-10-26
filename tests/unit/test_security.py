"""Tests for security fixes: path traversal, CSRF, ProxyFix, log redaction"""

from unittest.mock import patch

import pytest

from web_app.app import app
from web_app.config import ConfigManager
from web_app.users import User, UsersFile


@pytest.fixture
def client():
    import web_app.__main__  # noqa: F401

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.secret_key = 'test-secret'
    with app.test_client() as client:
        yield client


@pytest.fixture
def csrf_client():
    import web_app.__main__  # noqa: F401

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = True
    app.secret_key = 'test-secret'
    with app.test_client() as client:
        yield client


class TestCSRFProtection:
    """State-changing POST endpoints should reject requests without CSRF token"""

    def test_post_without_csrf_token_rejected(self, csrf_client):
        with csrf_client.session_transaction() as sess:
            sess['_user_id'] = 'admin'

        response = csrf_client.post('/metrics/new', data={'name': 'test', 'type': 'counter'})
        assert response.status_code == 400


class TestAPIAdminOnly:
    """API endpoints (push/pull/delete/list) should require admin"""

    @patch('web_app.helpers.DataInterface')
    def test_api_push_rejects_non_admin(self, mock_di, client):
        non_admin = User.create(
            username='user',
            password='pass',
            folder='uf',
            is_admin=False,
        )
        mock_di.return_value.edit_users.return_value.__enter__.return_value = (
            UsersFile(root=[non_admin])
        )
        response = client.post('/api/push', json={
            'username': 'user', 'password': 'pass',
            'name': 'test.txt', 'data': 'dGVzdA=='
        })
        assert response.status_code == 400
        assert 'Invalid credentials' in response.get_json().get('error', '')
