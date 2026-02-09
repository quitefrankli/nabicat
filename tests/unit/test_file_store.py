"""Unit tests for file_store module"""

import pytest
import io
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from werkzeug.datastructures import FileStorage

# Import app from __main__ where blueprints are registered
import web_app.__main__ as main_module
from web_app.users import User
from web_app.file_store import file_store_api
from web_app.file_store.data_interface import DataInterface, NON_ADMIN_MAX_STORAGE, ADMIN_MAX_STORAGE
from web_app.helpers import limiter
import web_app.helpers as helpers

app = main_module.app


@pytest.fixture
def client():
    """Create a test client for the Flask app"""
    app.config['TESTING'] = True
    # Disable rate limiting for tests
    limiter.enabled = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_user():
    """Create a regular test user"""
    return User(username='testuser', password='testpass', folder='test_folder', is_admin=False)


@pytest.fixture
def admin_user():
    """Create an admin test user"""
    return User(username='admin', password='admin', folder='admin_folder', is_admin=True)


@pytest.fixture
def auth_mock(test_user):
    """Setup authentication mocking for tests"""
    # Mock the user_loader to return our test user
    original_user_loader = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = lambda username: test_user if username == test_user.id else None
    
    yield test_user
    
    # Restore original user_loader
    helpers.login_manager._user_callback = original_user_loader


class TestDataInterface:
    """Tests for FileStoreDataInterface"""

    @patch('web_app.file_store.data_interface.DataInterface._get_user_dir')
    def test_save_file(self, mock_get_user_dir):
        """Test saving a file"""
        mock_get_user_dir.return_value = Path('/fake/user/dir')
        
        data_interface = DataInterface()
        data_interface.atomic_write = Mock()
        
        # Create a mock FileStorage
        file_storage = Mock(spec=FileStorage)
        file_storage.filename = 'test.txt'
        file_storage.stream = io.BytesIO(b'test content')
        
        user = Mock(spec=User)
        
        data_interface.save_file(file_storage, user)
        
        data_interface.atomic_write.assert_called_once()
        call_args = data_interface.atomic_write.call_args
        assert call_args[0][0] == Path('/fake/user/dir/test.txt')

    @patch('web_app.file_store.data_interface.DataInterface._get_user_dir')
    def test_save_file_prevents_path_traversal(self, mock_get_user_dir):
        """Test that save_file prevents path traversal attacks"""
        mock_get_user_dir.return_value = Path('/fake/user/dir')
        
        data_interface = DataInterface()
        data_interface.atomic_write = Mock()
        
        # Create a mock FileStorage with malicious filename
        file_storage = Mock(spec=FileStorage)
        file_storage.filename = '../../../etc/passwd'
        file_storage.stream = io.BytesIO(b'malicious content')
        
        user = Mock(spec=User)
        
        data_interface.save_file(file_storage, user)
        
        # Verify the path was sanitized (secure_filename removes path traversal)
        call_args = data_interface.atomic_write.call_args
        saved_path = call_args[0][0]
        # The path should NOT contain parent directory references
        assert '..' not in str(saved_path)
        # Should be flattened to just the filename
        assert saved_path.name == 'etc_passwd' or saved_path.name == 'passwd'

    @patch('web_app.file_store.data_interface.DataInterface._get_user_dir')
    def test_get_file_path_prevents_path_traversal(self, mock_get_user_dir):
        """Test that get_file_path prevents path traversal attacks"""
        mock_get_user_dir.return_value = Path('/fake/user/dir')
        
        data_interface = DataInterface()
        user = Mock(spec=User)
        
        # Try path traversal attack
        result = data_interface.get_file_path('../../../etc/passwd', user)
        
        # Verify the path was sanitized
        assert '..' not in str(result)
        # Should still be under the user directory
        assert '/fake/user/dir' in str(result)

    @patch('web_app.file_store.data_interface.DataInterface._get_user_dir')
    def test_get_file_path(self, mock_get_user_dir):
        """Test getting file path"""
        mock_get_user_dir.return_value = Path('/fake/user/dir')
        
        data_interface = DataInterface()
        user = Mock(spec=User)
        
        result = data_interface.get_file_path('test.txt', user)
        
        assert result == Path('/fake/user/dir/test.txt')

    @patch('web_app.file_store.data_interface.DataInterface._get_user_dir')
    def test_get_total_storage_size_empty_dir(self, mock_get_user_dir):
        """Test getting storage size when directory doesn't exist"""
        mock_get_user_dir.return_value = Path('/nonexistent/dir')
        
        data_interface = DataInterface()
        user = Mock(spec=User)
        
        result = data_interface.get_total_storage_size(user)
        
        assert result == 0

    @patch('web_app.file_store.data_interface.DataInterface._get_user_dir')
    def test_get_total_storage_size_with_files(self, mock_get_user_dir, tmp_path):
        """Test getting storage size with existing files"""
        # Create temporary files
        test_dir = tmp_path / 'user_dir'
        test_dir.mkdir()
        (test_dir / 'file1.txt').write_text('a' * 100)
        (test_dir / 'file2.txt').write_text('b' * 200)
        
        mock_get_user_dir.return_value = test_dir
        
        data_interface = DataInterface()
        user = Mock(spec=User)
        
        result = data_interface.get_total_storage_size(user)
        
        assert result == 300


class TestFileStoreRoutes:
    """Tests for file_store routes"""

    @patch('web_app.file_store.DataInterface')
    def test_index(self, mock_di_class, client, auth_mock):
        """Test index page"""
        mock_di = mock_di_class.return_value
        mock_di.list_files_with_metadata.return_value = [
            {'name': 'file1.txt', 'size': 100, 'size_formatted': '100.0 B', 'modified': None, 'modified_formatted': '2024-01-01 00:00'},
            {'name': 'file2.txt', 'size': 200, 'size_formatted': '200.0 B', 'modified': None, 'modified_formatted': '2024-01-01 00:00'}
        ]
        mock_di.get_total_storage_size.return_value = 300

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/file_store/')

        assert response.status_code == 200
        mock_di.list_files_with_metadata.assert_called_once_with(auth_mock)

    @patch('web_app.file_store.DataInterface')
    def test_upload_file_success(self, mock_di_class, client, auth_mock):
        """Test successful file upload"""
        mock_di = mock_di_class.return_value
        mock_di.get_total_storage_size.return_value = 0
        mock_di.save_file = Mock()

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        data = {'file': (io.BytesIO(b'test content'), 'test.txt')}
        response = client.post('/file_store/upload', data=data, content_type='multipart/form-data')

        assert response.status_code == 302  # Redirect

    def test_upload_no_file_part(self, client, auth_mock):
        """Test upload with no file part"""
        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.post('/file_store/upload', data={})

        assert response.status_code == 302  # Redirect with flash error

    def test_upload_empty_filename(self, client, auth_mock):
        """Test upload with empty filename"""
        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        data = {'file': (io.BytesIO(b''), '')}
        response = client.post('/file_store/upload', data=data, content_type='multipart/form-data')

        assert response.status_code == 302  # Redirect with flash error

    @patch('web_app.file_store.DataInterface')
    def test_upload_non_admin_exceeds_limit(self, mock_di_class, client, auth_mock):
        """Test non-admin upload exceeding storage limit"""
        mock_di = mock_di_class.return_value
        # Simulate already using almost all storage
        mock_di.get_total_storage_size.return_value = NON_ADMIN_MAX_STORAGE - 100

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        # Upload a file that would exceed the limit
        data = {'file': (io.BytesIO(b'a' * 1000), 'large.txt')}
        response = client.post('/file_store/upload', data=data, content_type='multipart/form-data')

        assert response.status_code == 302  # Redirect with flash error

    @patch('web_app.file_store.DataInterface')
    def test_upload_admin_has_limit(self, mock_di_class, client, admin_user):
        """Test admin upload has 1GB storage limit"""
        # Set up auth mock for admin
        original_user_loader = helpers.login_manager._user_callback
        helpers.login_manager._user_callback = lambda username: admin_user if username == admin_user.id else None
        
        try:
            mock_di = mock_di_class.return_value
            # Simulate using less than admin limit
            mock_di.get_total_storage_size.return_value = ADMIN_MAX_STORAGE - 100000

            with client.session_transaction() as sess:
                sess['_user_id'] = admin_user.id

            # Upload should work for admin under limit
            data = {'file': (io.BytesIO(b'a' * 1000), 'large.txt')}
            response = client.post('/file_store/upload', data=data, content_type='multipart/form-data')

            assert response.status_code == 302  # Redirect success
        finally:
            helpers.login_manager._user_callback = original_user_loader

    @patch('web_app.file_store.DataInterface')
    def test_upload_admin_exceeds_limit(self, mock_di_class, client, admin_user):
        """Test admin upload exceeding 1GB storage limit fails"""
        # Set up auth mock for admin
        original_user_loader = helpers.login_manager._user_callback
        helpers.login_manager._user_callback = lambda username: admin_user if username == admin_user.id else None
        
        try:
            mock_di = mock_di_class.return_value
            # Simulate already using almost all admin storage
            mock_di.get_total_storage_size.return_value = ADMIN_MAX_STORAGE - 100

            with client.session_transaction() as sess:
                sess['_user_id'] = admin_user.id

            # Upload a file that would exceed the limit
            data = {'file': (io.BytesIO(b'a' * 1000), 'large.txt')}
            response = client.post('/file_store/upload', data=data, content_type='multipart/form-data')

            assert response.status_code == 302  # Redirect with flash error
        finally:
            helpers.login_manager._user_callback = original_user_loader

    @patch('web_app.file_store.DataInterface')
    def test_download_file(self, mock_di_class, client, auth_mock, tmp_path):
        """Test downloading a file"""
        mock_di = mock_di_class.return_value
        
        # Create a real temporary file
        test_file = tmp_path / 'test.txt'
        test_file.write_text('download test content')
        mock_di.get_file_path.return_value = test_file

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/file_store/download/test.txt')

        assert response.status_code == 200

    @patch('web_app.file_store.DataInterface')
    def test_files_list(self, mock_di_class, client, auth_mock):
        """Test files list API"""
        mock_di = mock_di_class.return_value
        mock_di.list_files.return_value = ['file1.txt', 'file2.txt']

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/file_store/files_list')

        assert response.status_code == 200
        import json
        data = json.loads(response.data)
        assert data['files'] == ['file1.txt', 'file2.txt']

    @patch('web_app.file_store.DataInterface')
    def test_delete_file_success(self, mock_di_class, client, auth_mock):
        """Test successful file deletion"""
        mock_di = mock_di_class.return_value
        mock_di.delete_file = Mock()

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.post('/file_store/delete/test.txt')

        assert response.status_code == 302  # Redirect success

    @patch('web_app.file_store.DataInterface')
    def test_delete_file_not_found(self, mock_di_class, client, auth_mock):
        """Test deleting non-existent file"""
        mock_di = mock_di_class.return_value
        mock_di.delete_file.side_effect = FileNotFoundError()

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.post('/file_store/delete/nonexistent.txt')

        assert response.status_code == 302  # Redirect with flash error


class TestFileStoreBlueprint:
    """Tests for file_store blueprint"""

    def test_blueprint_name(self):
        """Test blueprint name"""
        assert file_store_api.name == 'file_store'

    def test_blueprint_url_prefix(self):
        """Test blueprint URL prefix"""
        assert file_store_api.url_prefix == '/file_store'

    def test_non_admin_max_storage_constant(self):
        """Test the storage limit constant"""
        assert NON_ADMIN_MAX_STORAGE == 30 * 1024 * 1024  # 30 MB

    def test_admin_max_storage_constant(self):
        """Test the admin storage limit constant"""
        assert ADMIN_MAX_STORAGE == 1 * 1024 * 1024 * 1024  # 1 GB


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
