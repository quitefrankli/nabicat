"""Unit tests for account API routes."""

import pytest
from unittest.mock import patch

import web_app.__main__ as main_module
import web_app.helpers as helpers
from web_app.users import User
from web_app.helpers import limiter


app = main_module.app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    limiter.enabled = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def regular_user() -> User:
    return User(username='testuser', password='testpass', folder='folder1', is_admin=False)


@pytest.fixture
def admin_user() -> User:
    return User(username='admin', password='adminpass', folder='adminfolder', is_admin=True)


@pytest.fixture
def logged_in_user(regular_user):
    original_user_loader = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = lambda username: regular_user if username == regular_user.id else None

    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = regular_user.id
            session['_fresh'] = True
        yield client

    helpers.login_manager._user_callback = original_user_loader


class TestDeleteAccountRoute:
    def test_delete_page_requires_login(self, client):
        response = client.get('/account/delete')
        assert response.status_code == 302
        assert '/account/login' in response.location

    @patch('web_app.account_api.FileStoreDataInterface')
    @patch('web_app.account_api.TubioDataInterface')
    @patch('web_app.account_api.JSwipeDataInterface')
    @patch('web_app.account_api.MetricsDataInterface')
    @patch('web_app.account_api.Todoist2DataInterface')
    @patch('web_app.account_api.APIDataInterface')
    @patch('web_app.account_api.DataInterface')
    def test_delete_account_success(self,
                                    mock_data_interface,
                                    mock_api_data_interface,
                                    mock_todoist2_data_interface,
                                    mock_metrics_data_interface,
                                    mock_jswipe_data_interface,
                                    mock_tubio_data_interface,
                                    mock_file_store_data_interface,
                                    logged_in_user,
                                    regular_user):
        mock_data_interface.return_value.load_users.return_value = {
            regular_user.id: regular_user,
            'admin2': User(username='admin2', password='admin2pass', folder='folder2', is_admin=True),
        }

        response = logged_in_user.post('/account/delete', data={'password': regular_user.password})

        assert response.status_code == 302
        assert response.location.endswith('/')
        mock_data_interface.return_value.save_users.assert_called_once()
        mock_api_data_interface.return_value.delete_user_data.assert_called_once_with(regular_user)
        mock_todoist2_data_interface.return_value.delete_user_data.assert_called_once_with(regular_user)
        mock_metrics_data_interface.return_value.delete_user_data.assert_called_once_with(regular_user)
        mock_jswipe_data_interface.return_value.delete_user_data.assert_called_once_with(regular_user)
        mock_tubio_data_interface.return_value.delete_user_data.assert_called_once_with(regular_user)
        mock_file_store_data_interface.return_value.delete_user_data.assert_called_once_with(regular_user)

        saved_users = mock_data_interface.return_value.save_users.call_args[0][0]
        assert all(user.id != regular_user.id for user in saved_users)

        with logged_in_user.session_transaction() as session:
            assert '_user_id' not in session

    @patch('web_app.account_api.DataInterface')
    def test_delete_account_wrong_password(self, mock_data_interface, logged_in_user, regular_user):
        mock_data_interface.return_value.load_users.return_value = {
            regular_user.id: regular_user,
            'admin2': User(username='admin2', password='admin2pass', folder='folder2', is_admin=True),
        }

        response = logged_in_user.post('/account/delete', data={'password': 'wrongpassword'})

        assert response.status_code == 302
        assert response.location.endswith('/account/delete')
        mock_data_interface.return_value.save_users.assert_not_called()

        with logged_in_user.session_transaction() as session:
            assert session.get('_user_id') == regular_user.id

    @patch('web_app.account_api.DataInterface')
    def test_delete_account_rejects_last_admin(self, mock_data_interface, admin_user):
        original_user_loader = helpers.login_manager._user_callback
        helpers.login_manager._user_callback = lambda username: admin_user if username == admin_user.id else None

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = admin_user.id
                session['_fresh'] = True

            mock_data_interface.return_value.load_users.return_value = {
                admin_user.id: admin_user,
            }

            response = client.post('/account/delete', data={'password': admin_user.password})

            assert response.status_code == 302
            assert response.location.endswith('/account/delete')
            mock_data_interface.return_value.save_users.assert_not_called()

            with client.session_transaction() as session:
                assert session.get('_user_id') == admin_user.id

        helpers.login_manager._user_callback = original_user_loader
