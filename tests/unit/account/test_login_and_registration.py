from unittest.mock import patch

import pytest

from web_app.users import User, UsersFile


class TestLoginRoute:
    @patch('web_app.account_api.DataInterface')
    def test_login_accepts_hashed_password(
        self,
        mock_data_interface,
        client,
    ):
        user = User.create(
            username='testuser',
            password='testpass',
            folder='folder1',
        )
        mock_data_interface.return_value.load_users_local.return_value = {user.id: user}

        response = client.post(
            '/account/login',
            data={'username': 'testuser', 'password': 'testpass'},
        )

        assert response.status_code == 302
        assert response.location.endswith('/')
        with client.session_transaction() as session:
            assert session.get('_user_id') == user.id

    @pytest.mark.parametrize('stored_password', [
        'legacy-password', '', 'nabicat$', 'nabicat$invalid',
        'nabicat$scrypt:invalid$salt$hash',
    ])
    @patch('web_app.account_api.DataInterface')
    def test_login_rejects_plaintext_and_malformed_passwords(
        self,
        mock_data_interface,
        client,
        stored_password,
    ):
        user = User(
            username='legacy',
            password=stored_password,
            folder='legacy-folder',
        )
        mock_data_interface.return_value.load_users_local.return_value = {user.id: user}

        response = client.post(
            '/account/login',
            data={'username': 'legacy', 'password': stored_password},
        )

        assert response.status_code == 302
        assert response.location.endswith('/account/login')
        assert user.password == stored_password
        with client.session_transaction() as session:
            assert '_user_id' not in session
        mock_data_interface.return_value.edit_users.assert_not_called()

    @patch('web_app.account_api.DataInterface')
    def test_login_rejects_incorrect_password_without_changing_hash(
        self,
        mock_data_interface,
        client,
    ):
        user = User.create(
            username='testuser',
            password='testpass',
            folder='folder1',
        )
        original_hash = user.password
        mock_data_interface.return_value.load_users_local.return_value = {user.id: user}

        response = client.post(
            '/account/login',
            data={'username': 'testuser', 'password': 'wrong-password'},
        )

        assert response.status_code == 302
        assert response.location.endswith('/account/login')
        assert user.password == original_hash
        with client.session_transaction() as session:
            assert '_user_id' not in session


class TestRegistrationRoute:
    @patch('web_app.account_api.DataInterface')
    def test_registration_stores_a_password_hash(
        self,
        mock_data_interface,
        client,
        wire_edit_users,
    ):
        users_file = UsersFile()
        wire_edit_users(mock_data_interface, users_file)
        mock_data_interface.return_value.generate_new_user.side_effect = (
            lambda username, password: User.create(
                username=username,
                password=password,
                folder='new-folder',
            )
        )

        response = client.post(
            '/account/register',
            data={'username': 'new-user', 'password': 'new-password'},
        )

        assert response.status_code == 302
        new_user = users_file.get('new-user')
        assert new_user is not None
        assert new_user.password != 'new-password'
        assert new_user.verify_password('new-password')
