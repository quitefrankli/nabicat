"""Unit tests for file_store module"""

import pytest
import io
import binascii
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch
from werkzeug.datastructures import FileStorage

# Import app from __main__ where blueprints are registered
import web_app.__main__ as main_module
from web_app.users import User
from web_app.file_store import file_store_api
from web_app.file_store.data_interface import (
    DataInterface,
    NON_ADMIN_MAX_STORAGE,
    ADMIN_MAX_STORAGE,
    Metadata,
    UserMetadata,
    FileMetadata,
    UserFileEntry
)
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
def test_user2():
    """Create a second test user"""
    return User(username='testuser2', password='testpass2', folder='test_folder2', is_admin=False)


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


@pytest.fixture
def data_interface(tmp_path):
    """Create a DataInterface with temporary directory"""
    di = DataInterface()
    di.file_store_dir = tmp_path / "file_store"
    di.files_dir = di.file_store_dir / "files"
    di.metadata_file = di.file_store_dir / "metadata.json"
    return di


class TestDataInterface:
    """Tests for FileStoreDataInterface with metadata-based storage"""

    def test_get_metadata_empty(self, data_interface):
        """Test getting metadata when file doesn't exist"""
        metadata = data_interface.get_metadata()

        assert isinstance(metadata, Metadata)
        assert metadata.users == {}
        assert metadata.files == {}

    def test_save_and_load_metadata(self, data_interface):
        """Test saving and loading metadata"""
        metadata = Metadata()
        metadata.users['user1'] = UserMetadata(
            user_id='user1',
            files=[UserFileEntry(crc=123, original_name='test.txt')]
        )
        metadata.files[123] = FileMetadata(
            crc=123,
            original_name='test.txt',
            size=100,
            upload_date=datetime.now().isoformat()
        )

        data_interface.save_metadata(metadata)

        loaded = data_interface.get_metadata()
        assert 'user1' in loaded.users
        assert 123 in loaded.files
        assert loaded.files[123].original_name == 'test.txt'
        assert len(loaded.users['user1'].files) == 1

    def test_save_file_new(self, data_interface, test_user):
        """Test saving a new file"""
        file_data = b'test content'
        file_storage = Mock()
        file_storage.filename = 'test.txt'
        file_storage.read.return_value = file_data
        file_storage.content_type = 'text/plain'

        crc = data_interface.save_file(file_storage, test_user)

        assert crc == binascii.crc32(file_data)

        # Verify file was saved
        file_path = data_interface.files_dir / str(crc)
        assert file_path.exists()
        assert file_path.read_bytes() == file_data

        # Verify metadata
        metadata = data_interface.get_metadata()
        assert test_user.id in metadata.users
        assert len(metadata.users[test_user.id].files) == 1
        assert metadata.users[test_user.id].files[0].crc == crc
        assert metadata.users[test_user.id].files[0].original_name == 'test.txt'
        assert crc in metadata.files
        assert metadata.files[crc].original_name == 'test.txt'

    def test_save_file_duplicate_dedup(self, data_interface, test_user):
        """Test that saving the same file twice only stores it once"""
        file_data = b'test content'
        file_storage1 = Mock()
        file_storage1.filename = 'test1.txt'
        file_storage1.read.return_value = file_data
        file_storage1.content_type = 'text/plain'

        file_storage2 = Mock()
        file_storage2.filename = 'test2.txt'
        file_storage2.read.return_value = file_data
        file_storage2.content_type = 'text/plain'

        crc1 = data_interface.save_file(file_storage1, test_user)
        crc2 = data_interface.save_file(file_storage2, test_user)

        # CRCs should be the same
        assert crc1 == crc2

        # Should have both files in user's list
        metadata = data_interface.get_metadata()
        assert len(metadata.users[test_user.id].files) == 2
        assert metadata.users[test_user.id].files[0].original_name == 'test1.txt'
        assert metadata.users[test_user.id].files[1].original_name == 'test2.txt'
        assert metadata.users[test_user.id].files[0].crc == crc1
        assert metadata.users[test_user.id].files[1].crc == crc1

        # But only one file metadata entry (from first upload)
        assert metadata.files[crc1].original_name == 'test1.txt'

    def test_save_file_different_users_same_content(self, data_interface, test_user, test_user2):
        """Test that different users can share the same file content"""
        file_data = b'shared content'

        file_storage1 = Mock()
        file_storage1.filename = 'user1.txt'
        file_storage1.read.return_value = file_data
        file_storage1.content_type = 'text/plain'

        file_storage2 = Mock()
        file_storage2.filename = 'user2.txt'
        file_storage2.read.return_value = file_data
        file_storage2.content_type = 'text/plain'

        crc1 = data_interface.save_file(file_storage1, test_user)
        crc2 = data_interface.save_file(file_storage2, test_user2)

        assert crc1 == crc2

        metadata = data_interface.get_metadata()
        assert any(f.crc == crc1 for f in metadata.users[test_user.id].files)
        assert any(f.crc == crc1 for f in metadata.users[test_user2.id].files)

        # Only one physical file
        file_path = data_interface.files_dir / str(crc1)
        assert file_path.exists()

    def test_get_file_path(self, data_interface, test_user):
        """Test getting file path by original filename"""
        file_data = b'test content'
        file_storage = Mock()
        file_storage.filename = 'myfile.txt'
        file_storage.read.return_value = file_data
        file_storage.content_type = 'text/plain'

        crc = data_interface.save_file(file_storage, test_user)

        path = data_interface.get_file_path('myfile.txt', test_user)

        assert path == data_interface.files_dir / str(crc)
        assert path.exists()

    def test_get_file_path_not_found(self, data_interface, test_user):
        """Test getting non-existent file"""
        with pytest.raises(FileNotFoundError):
            data_interface.get_file_path('nonexistent.txt', test_user)

    def test_delete_file_single_user(self, data_interface, test_user):
        """Test deleting a file when only one user has it"""
        file_data = b'test content'
        file_storage = Mock()
        file_storage.filename = 'test.txt'
        file_storage.read.return_value = file_data
        file_storage.content_type = 'text/plain'

        crc = data_interface.save_file(file_storage, test_user)
        file_path = data_interface.files_dir / str(crc)

        assert file_path.exists()

        data_interface.delete_file('test.txt', test_user)

        # File should be deleted from disk
        assert not file_path.exists()

        # Metadata should be cleaned up
        metadata = data_interface.get_metadata()
        assert crc not in metadata.files
        assert not any(f.crc == crc for f in metadata.users[test_user.id].files)

    def test_delete_file_multiple_users(self, data_interface, test_user, test_user2):
        """Test deleting file when multiple users have it"""
        file_data = b'shared content'

        file_storage1 = Mock()
        file_storage1.filename = 'file1.txt'
        file_storage1.read.return_value = file_data
        file_storage1.content_type = 'text/plain'

        file_storage2 = Mock()
        file_storage2.filename = 'file2.txt'
        file_storage2.read.return_value = file_data
        file_storage2.content_type = 'text/plain'

        crc = data_interface.save_file(file_storage1, test_user)
        data_interface.save_file(file_storage2, test_user2)

        file_path = data_interface.files_dir / str(crc)
        assert file_path.exists()

        # Delete from first user
        data_interface.delete_file('file1.txt', test_user)

        # File should still exist (user2 still has it)
        assert file_path.exists()

        # Metadata should still have the file
        metadata = data_interface.get_metadata()
        assert crc in metadata.files
        assert not any(f.crc == crc for f in metadata.users[test_user.id].files)
        assert any(f.crc == crc for f in metadata.users[test_user2.id].files)

        # Delete from second user
        data_interface.delete_file('file2.txt', test_user2)

        # Now file should be deleted
        assert not file_path.exists()
        metadata = data_interface.get_metadata()
        assert crc not in metadata.files

    def test_list_files(self, data_interface, test_user):
        """Test listing user files"""
        # New user should have empty list
        files = data_interface.list_files(test_user)
        assert files == []

        # Add some files
        for filename in ['file1.txt', 'file2.txt']:
            file_storage = Mock()
            file_storage.filename = filename
            file_storage.read.return_value = filename.encode()
            file_storage.content_type = 'text/plain'
            data_interface.save_file(file_storage, test_user)

        files = data_interface.list_files(test_user)
        assert sorted(files) == ['file1.txt', 'file2.txt']

    def test_list_files_with_metadata(self, data_interface, test_user):
        """Test listing files with metadata"""
        file_storage = Mock()
        file_storage.filename = 'test.txt'
        file_storage.read.return_value = b'test content'
        file_storage.content_type = 'text/plain'

        data_interface.save_file(file_storage, test_user)

        files = data_interface.list_files_with_metadata(test_user)

        assert len(files) == 1
        assert files[0]['name'] == 'test.txt'
        assert files[0]['size'] == 12
        assert 'size_formatted' in files[0]
        assert 'modified_formatted' in files[0]
        assert files[0]['mime_type'] == 'text/plain'

    def test_get_total_storage_size_new_user(self, data_interface, test_user):
        """Test storage size for new user"""
        size = data_interface.get_total_storage_size(test_user)
        assert size == 0

    def test_get_total_storage_size_with_files(self, data_interface, test_user):
        """Test storage size calculation"""
        file1_data = b'a' * 100
        file2_data = b'b' * 200

        for filename, data in [('file1.txt', file1_data), ('file2.txt', file2_data)]:
            file_storage = Mock()
            file_storage.filename = filename
            file_storage.read.return_value = data
            file_storage.content_type = 'text/plain'
            data_interface.save_file(file_storage, test_user)

        size = data_interface.get_total_storage_size(test_user)
        assert size == 300

    def test_get_user_metadata_new_user(self, data_interface, test_user):
        """Test getting metadata for new user"""
        user_meta = data_interface.get_user_metadata(test_user)

        assert user_meta.user_id == test_user.id
        assert user_meta.files == []


class TestFileStoreRoutes:
    """Tests for file_store routes"""

    @patch('web_app.file_store.DataInterface')
    def test_index_list_mode(self, mock_di_class, client, auth_mock):
        """Test index page in list mode"""
        mock_di = mock_di_class.return_value
        mock_di.list_files_with_metadata.return_value = [
            {'name': 'file1.txt', 'size': 100, 'size_formatted': '100.0 B',
             'modified': None, 'modified_formatted': '2024-01-01 00:00',
             'crc': 123, 'mime_type': 'text/plain'},
        ]
        mock_di.get_total_storage_size.return_value = 100

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/file_store/?mode=list')

        assert response.status_code == 200
        assert b'List' in response.data

    @patch('web_app.file_store.DataInterface')
    def test_index_grid_mode_shows_all_files(self, mock_di_class, client, auth_mock):
        """Test that grid mode shows all files (not just images)"""
        mock_di = mock_di_class.return_value
        mock_di.list_files_with_metadata.return_value = [
            {'name': 'photo.jpg', 'size': 100, 'size_formatted': '100.0 B',
             'modified': None, 'modified_formatted': '2024-01-01 00:00',
             'crc': 123, 'mime_type': 'image/jpeg'},
            {'name': 'document.txt', 'size': 200, 'size_formatted': '200.0 B',
             'modified': None, 'modified_formatted': '2024-01-01 00:00',
             'crc': 456, 'mime_type': 'text/plain'},
        ]
        mock_di.get_total_storage_size.return_value = 300

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/file_store/?mode=grid')

        assert response.status_code == 200
        # Grid mode should show both files
        assert b'photo.jpg' in response.data
        assert b'document.txt' in response.data

    @patch('web_app.file_store.DataInterface')
    def test_upload_file_success(self, mock_di_class, client, auth_mock):
        """Test successful file upload"""
        mock_di = mock_di_class.return_value
        mock_di.get_total_storage_size.return_value = 0
        mock_di.save_file = Mock(return_value=123)

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        data = {'file': (io.BytesIO(b'test content'), 'test.txt')}
        response = client.post('/file_store/upload', data=data, content_type='multipart/form-data')

        assert response.status_code == 302  # Redirect

    @patch('web_app.file_store.DataInterface')
    def test_download_file(self, mock_di_class, client, auth_mock, tmp_path):
        """Test downloading a file"""
        mock_di = mock_di_class.return_value

        # Create a real temporary file
        test_file = tmp_path / '123'  # CRC-based filename
        test_file.write_text('download test content')
        mock_di.get_file_path.return_value = test_file

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/file_store/download/test.txt')

        assert response.status_code == 200


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
