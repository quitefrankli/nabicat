"""Unit tests for web_app module"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from web_app.app import app
from web_app.config import ConfigManager
from web_app.users import User
from web_app.errors import APIError, AuthenticationError
from web_app.helpers import (
    authenticate_user,
    parse_request,
    get_ip,
    cur_user,
    from_req,
)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def app_context():
    with app.app_context():
        yield app


class TestHelpers:
    @patch('web_app.helpers.DataInterface')
    def test_authenticate_user_success(self, mock_data_interface):
        """Test successful user authentication"""
        mock_users = {
            'admin': User(username='admin', password='admin', folder='admin_folder', is_admin=True)
        }
        mock_data_interface.return_value.load_users.return_value = mock_users

        assert authenticate_user('admin', 'admin', require_admin=True)

    @patch('web_app.helpers.DataInterface')
    def test_authenticate_user_wrong_password(self, mock_data_interface):
        """Test authentication with wrong password"""
        mock_users = {
            'admin': User(username='admin', password='admin', folder='admin_folder', is_admin=True)
        }
        mock_data_interface.return_value.load_users.return_value = mock_users

        assert not authenticate_user('admin', 'wrongpass', require_admin=True)

    @patch('web_app.helpers.DataInterface')
    def test_authenticate_user_nonexistent(self, mock_data_interface):
        """Test authentication with nonexistent user"""
        mock_data_interface.return_value.load_users.return_value = {}

        assert not authenticate_user('nonexistent', 'pass', require_admin=True)

    @patch('web_app.helpers.DataInterface')
    def test_authenticate_user_not_admin(self, mock_data_interface):
        """Test authentication requiring admin when user is not admin"""
        mock_users = {
            'user': User(username='user', password='pass', folder='user_folder', is_admin=False)
        }
        mock_data_interface.return_value.load_users.return_value = mock_users

        assert not authenticate_user('user', 'pass', require_admin=True)

    @patch('web_app.helpers.DataInterface')
    def test_authenticate_user_not_admin_when_not_required(self, mock_data_interface):
        """Test authentication of non-admin user when admin not required"""
        mock_users = {
            'user': User(username='user', password='pass', folder='user_folder', is_admin=False)
        }
        mock_data_interface.return_value.load_users.return_value = mock_users

        assert authenticate_user('user', 'pass', require_admin=False)

    def test_get_ip_from_x_forwarded_for(self, app_context):
        """Test get_ip with X-Forwarded-For header"""
        with app.test_request_context(headers={'X-Forwarded-For': '192.168.1.1'}):
            ip = get_ip()
            assert ip == '192.168.1.1'

    def test_get_ip_from_remote_addr(self, app_context):
        """Test get_ip with remote_addr"""
        with app.test_request_context(environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            ip = get_ip()
            assert ip == '127.0.0.1'

    def test_get_ip_multiple_forwarded_addresses(self, app_context):
        """Test get_ip with multiple X-Forwarded-For addresses"""
        with app.test_request_context(headers={'X-Forwarded-For': '192.168.1.1, 192.168.1.2'}):
            ip = get_ip()
            # When multiple IPs are provided, the first is returned with full string
            assert '192.168.1.1' in ip

    def test_from_req_from_form(self, app_context):
        """Test from_req with form data"""
        with app.test_request_context(method='POST', data={'test_key': 'test_value'}):
            result = from_req('test_key')
            assert result == 'test_value'

    def test_from_req_from_args(self, app_context):
        """Test from_req with query args"""
        with app.test_request_context(query_string='test_key=test_value'):
            result = from_req('test_key')
            assert result == 'test_value'

    def test_from_req_removes_non_ascii(self, app_context):
        """Test from_req removes non-ASCII characters"""
        with app.test_request_context(method='POST', data={'test_key': 'value_with_émoji_🎉'}):
            result = from_req('test_key')
            # Non-ASCII characters should be removed
            assert 'é' not in result
            assert '🎉' not in result


class TestParseRequest:
    def test_parse_request_json(self, app_context):
        """Test parse_request with JSON content"""
        with app.test_request_context(
            method='POST',
            data=json.dumps({'key': 'value'}),
            content_type='application/json'
        ):
            result = parse_request(require_login=False, require_admin=False)
            assert result == {'key': 'value'}

    def test_parse_request_invalid_json(self, app_context):
        """Test parse_request with invalid JSON"""
        with app.test_request_context(
            method='POST',
            data='invalid json',
            content_type='application/json'
        ):
            with pytest.raises(APIError):
                parse_request(require_login=False, require_admin=False)

    def test_parse_request_form_data(self, app_context):
        """Test parse_request with form data"""
        with app.test_request_context(
            method='POST',
            data={'key': 'value'},
            content_type='application/x-www-form-urlencoded'
        ):
            result = parse_request(require_login=False, require_admin=False)
            assert result['key'] == 'value'

    def test_parse_request_multipart_form_data(self, app_context):
        """Test parse_request with multipart form data"""
        with app.test_request_context(
            method='POST',
            data={'key': 'value'},
            content_type='multipart/form-data'
        ):
            result = parse_request(require_login=False, require_admin=False)
            assert result['key'] == 'value'

    def test_parse_request_unsupported_content_type(self, app_context):
        """Test parse_request with unsupported content type"""
        with app.test_request_context(
            method='POST',
            data='some data',
            content_type='text/plain'
        ):
            with pytest.raises(APIError):
                parse_request(require_login=False, require_admin=False)

    def test_parse_request_default_content_type(self, app_context):
        """Test parse_request with no content type"""
        with app.test_request_context(method='POST'):
            with pytest.raises(APIError):
                parse_request(require_login=False, require_admin=False)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
