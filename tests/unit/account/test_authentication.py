from unittest.mock import patch

import pytest

from web_app.auth import authenticate_user
from web_app.users import User


class TestAPIAuthentication:
    @patch('web_app.auth.DataInterface')
    def test_accepts_hashed_admin_password(self, mock_data_interface):
        user = User.create(
            username='admin',
            password='admin',
            folder='admin_folder',
            is_admin=True,
        )
        mock_data_interface.return_value.load_users_local.return_value = {user.id: user}

        assert authenticate_user('admin', 'admin', require_admin=True)

    @patch('web_app.auth.DataInterface')
    def test_rejects_wrong_password_without_changing_hash(
        self,
        mock_data_interface,
    ):
        user = User.create(
            username='admin',
            password='admin',
            folder='admin_folder',
            is_admin=True,
        )
        original_hash = user.password
        mock_data_interface.return_value.load_users_local.return_value = {user.id: user}

        assert not authenticate_user(
            'admin',
            'wrongpass',
            require_admin=True,
        )
        assert user.password == original_hash

    @patch('web_app.auth.DataInterface')
    def test_rejects_nonexistent_user(self, mock_data_interface):
        mock_data_interface.return_value.load_users_local.return_value = {}

        assert not authenticate_user(
            'nonexistent',
            'pass',
            require_admin=True,
        )

    @patch('web_app.auth.DataInterface')
    def test_enforces_admin_requirement(self, mock_data_interface):
        user = User.create(
            username='user',
            password='pass',
            folder='user_folder',
            is_admin=False,
        )
        mock_data_interface.return_value.load_users_local.return_value = {user.id: user}

        assert not authenticate_user('user', 'pass', require_admin=True)

    @patch('web_app.auth.DataInterface')
    def test_allows_non_admin_when_admin_not_required(
        self,
        mock_data_interface,
    ):
        user = User.create(
            username='user',
            password='pass',
            folder='user_folder',
            is_admin=False,
        )
        mock_data_interface.return_value.load_users_local.return_value = {user.id: user}

        assert authenticate_user('user', 'pass', require_admin=False)

    @pytest.mark.parametrize('stored_password', ['pass', 'nabicat$invalid'])
    @patch('web_app.auth.DataInterface')
    def test_rejects_plaintext_and_malformed_passwords(
        self, mock_data_interface, stored_password,
    ):
        user = User('admin', password=stored_password, folder='admin', is_admin=True)
        mock_data_interface.return_value.load_users_local.return_value = {user.id: user}

        assert not authenticate_user('admin', stored_password)
        assert user.password == stored_password
        mock_data_interface.return_value.edit_users.assert_not_called()


class TestUserPasswords:
    def test_creation_and_verification_are_centralized_on_user(self):
        user = User.create(
            username='alice',
            password='correct horse battery staple',
            folder='alice-folder',
        )

        assert user.password != 'correct horse battery staple'
        assert user.verify_password('correct horse battery staple')
        assert not user.verify_password('incorrect password')
