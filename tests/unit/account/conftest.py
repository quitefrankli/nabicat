from contextlib import contextmanager

import pytest

from web_app.users import User, UsersFile

@pytest.fixture
def regular_user() -> User:
    return User.create(
        username='testuser',
        password='testpass',
        folder='folder1',
        is_admin=False,
    )


@pytest.fixture
def admin_user() -> User:
    return User.create(
        username='admin',
        password='adminpass',
        folder='adminfolder',
        is_admin=True,
    )


@pytest.fixture
def logged_in_user(app, regular_user, auth_loader):
    auth_loader(regular_user)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = regular_user.id
            session['_fresh'] = True
        yield client


@pytest.fixture
def wire_edit_users():
    def _wire(mock_data_interface, users_file: UsersFile):
        @contextmanager
        def _edit_users():
            yield users_file

        mock_data_interface.return_value.edit_users.side_effect = _edit_users

    return _wire
