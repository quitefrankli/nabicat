"""Tests for security fixes: path traversal, CSRF, ProxyFix, log redaction"""

from unittest.mock import patch

import pytest

from web_app.users import User, UsersFile


@pytest.fixture
def csrf_client(app):
    app.config['WTF_CSRF_ENABLED'] = True
    return app.test_client()


class TestCSRFProtection:
    """State-changing POST endpoints should reject requests without CSRF token"""

    def test_post_without_csrf_token_rejected(self, csrf_client):
        with csrf_client.session_transaction() as sess:
            sess['_user_id'] = 'admin'

        response = csrf_client.post('/metrics/new', data={'name': 'test', 'type': 'counter'})
        assert response.status_code == 400


class TestAPIAdminOnly:
    """API endpoints (push/pull/delete/list) should require admin"""

    @patch('web_app.api_auth.DataInterface')
    def test_api_push_rejects_non_admin(self, mock_di, client):
        non_admin = User.create(
            username='user',
            password='pass',
            folder='uf',
            is_admin=False,
        )
        from web_app.api_auth import issue_api_token

        mock_di.return_value.load_users.return_value = UsersFile(root=[non_admin])
        with client.application.app_context():
            token = issue_api_token('user')
        response = client.post('/api/push', data=b'test', query_string={'name': 'test.txt'}, headers={
            'Authorization': f'Bearer {token}', 'Content-Type': 'application/octet-stream',
        })
        assert response.status_code == 403
        assert 'Admin access required' in response.get_json().get('error', '')
