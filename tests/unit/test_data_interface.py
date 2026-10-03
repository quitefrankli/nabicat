"""Unit tests for data_interface module"""

import json
import shutil
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from web_app.data_interface import DataInterface
from web_app.users import User


def test_users_file_rejects_duplicate_identity_and_folder(mock_config) -> None:
    from pydantic import ValidationError

    from web_app.users import UsersFile

    first = User(username="alice", folder="alice-folder")
    with pytest.raises(ValidationError):
        UsersFile(root=[first, first.model_copy()])
    with pytest.raises(ValidationError):
        UsersFile(root=[first, User(username="bob", folder="alice-folder")])


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing"""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def mock_config(temp_dir):
    """Mock ConfigManager for testing"""
    with (
        patch('web_app.data_interface.ConfigManager') as mock_cfg,
        patch('web_app.users.ConfigManager') as mock_user_config,
    ):
        config_instance = Mock()
        config_instance.save_data_path = temp_dir / "data"
        config_instance.tubio.cookie_path = temp_dir / "data" / "cookies.txt"
        config_instance.temp_dir = temp_dir / "temp"
        config_instance.use_offline_syncer = True
        config_instance.random_string_length = 10
        config_instance.random_generation_attempts = 100
        config_instance.atomic_write_file_mode = 0o644
        config_instance.atomic_write_chunk_size = 1024 * 1024
        config_instance.app_user_folder_pattern = r"[a-z0-9][a-z0-9._-]*"
        mock_cfg.return_value = config_instance
        mock_user_config.return_value = config_instance
        yield config_instance


@pytest.fixture
def mock_data_syncer():
    """Mock DataSyncer for testing"""
    with patch('web_app.data_interface.DataSyncer.instance') as mock_syncer:
        syncer_instance = Mock()
        mock_syncer.return_value = syncer_instance
        yield syncer_instance


class TestDataInterface:
    """Tests for DataInterface"""

    def test_load_users_empty(self, mock_config, mock_data_syncer, temp_dir):
        """Test load_users when no users file exists"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()
        users = interface.load_users()
        assert users == {}

    def test_load_users_with_data(self, mock_config, mock_data_syncer, temp_dir):
        """Test load_users with existing users file"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()

        # Create users file
        interface.users_file.parent.mkdir(parents=True, exist_ok=True)
        users_data = [
            {'username': 'user1', 'password': 'pass1', 'folder': 'folder1', 'is_admin': True},
            {'username': 'user2', 'password': 'pass2', 'folder': 'folder2', 'is_admin': False},
        ]
        with open(interface.users_file, 'w') as f:
            json.dump(users_data, f)

        users = interface.load_users()
        assert len(users) == 2
        assert 'user1' in users
        assert 'user2' in users
        assert users['user1'].is_admin is True
        assert users['user2'].is_admin is False

    def test_save_users(self, mock_config, mock_data_syncer, temp_dir):
        """Test save_users method"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()

        users = [
            User(username='user1', password='pass1', folder='folder1', is_admin=True),
            User(username='user2', password='pass2', folder='folder2', is_admin=False),
        ]
        interface._save_users(users)

        # Verify file was created
        assert interface.users_file.exists()

        # Verify content
        with open(interface.users_file, 'r') as f:
            loaded_data = json.load(f)
        assert len(loaded_data) == 2
        assert loaded_data[0]['username'] == 'user1'
        assert loaded_data[1]['username'] == 'user2'

    def test_generate_random_string_default_length(self):
        """Test generate_random_string with default length"""
        result = DataInterface.generate_random_string()
        assert len(result) == 10
        assert result.isalpha()
        assert result.islower()

    def test_generate_random_string_custom_length(self):
        """Test generate_random_string with custom length"""
        result = DataInterface.generate_random_string(length=20)
        assert len(result) == 20
        assert result.isalpha()
        assert result.islower()

    @patch('web_app.data_interface.Repo')
    def test_generate_new_user_unique_folder(self, mock_repo, mock_config, mock_data_syncer, temp_dir):
        """Test generate_new_user creates user with unique folder"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()

        # Create initial user file
        interface.users_file.parent.mkdir(parents=True, exist_ok=True)
        initial_users = [
            User(username='existing', password='pass', folder='existing_folder', is_admin=False)
        ]
        interface._save_users(initial_users)

        new_user = interface.generate_new_user('newuser', 'newpass')
        assert new_user.id == 'newuser'
        assert new_user.password != 'newpass'
        assert new_user.verify_password('newpass')
        assert new_user.folder != 'existing_folder'
        assert len(new_user.folder) == 10

    @patch('web_app.data_interface.Repo')
    def test_generate_new_user_duplicate_prevention(self, mock_repo, mock_config, mock_data_syncer, temp_dir):
        """Test generate_new_user retries when folder conflicts"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()

        # Mock generate_random_string to return duplicates then unique
        with patch.object(interface, 'generate_random_string') as mock_generate:
            mock_generate.side_effect = ['existing_folder', 'existing_folder', 'unique_folder']

            # Create initial user file
            interface.users_file.parent.mkdir(parents=True, exist_ok=True)
            initial_users = [
                User(username='existing', password='pass', folder='existing_folder', is_admin=False)
            ]
            interface._save_users(initial_users)

            new_user = interface.generate_new_user('newuser', 'newpass')
            assert new_user.folder == 'unique_folder'

    @patch('web_app.data_interface.Repo')
    def test_generate_metadata_file(self, mock_repo, mock_config, mock_data_syncer, temp_dir):
        """Test generate_metadata_file creates metadata"""
        mock_config.save_data_path = temp_dir / "data"
        mock_repo.return_value.head.commit.hexsha = "abc123def456"

        interface = DataInterface()
        backup_dir = temp_dir / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)

        interface.generate_metadata_file(backup_dir)

        metadata_file = backup_dir / "metadata.json"
        assert metadata_file.exists()

        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
        assert metadata['commit_hash'] == 'abc123def456'

    def test_generate_backup_dir(self, mock_config, mock_data_syncer, temp_dir):
        """Test generate_backup_dir creates timestamped directory"""
        mock_config.save_data_path = temp_dir / "data"
        mock_config.backup_max_count = 8
        interface = DataInterface()

        backup_dir = interface.generate_backup_dir()
        assert backup_dir.exists()
        assert backup_dir.parent == interface.backups_directory

    @patch('web_app.data_interface.Repo')
    def test_backup_data_does_not_copy_logs(
        self,
        mock_repo,
        mock_config,
        mock_data_syncer,
        temp_dir,
    ):
        mock_repo.return_value.head.commit.hexsha = "abc123"
        data_root = mock_config.save_data_path
        data_root.mkdir(parents=True)
        (data_root / "users.json").write_text("[]")
        logs_dir = data_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "web_app.log").write_text("request log")

        backup_dir = temp_dir / "backup"
        backup_dir.mkdir()
        DataInterface().backup_data(backup_dir)

        assert (backup_dir / "users.json").read_text() == "[]"
        assert json.loads((backup_dir / "metadata.json").read_text()) == {
            "commit_hash": "abc123"
        }
        assert not (backup_dir / "logs").exists()

    def test_atomic_write_with_data(self, mock_config, mock_data_syncer, temp_dir):
        """Test atomic_write with data string"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()

        file_path = temp_dir / "test_file.txt"
        interface.atomic_write(file_path, data="test content", mode='w')

        assert file_path.exists()
        with open(file_path, 'r') as f:
            content = f.read()
        assert content == "test content"

    def test_atomic_write_with_stream(self, mock_config, mock_data_syncer, temp_dir):
        """Test atomic_write with stream"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()

        file_path = temp_dir / "test_file.bin"
        stream = BytesIO(b"test content")
        interface.atomic_write(file_path, stream=stream, mode='wb')

        assert file_path.exists()
        with open(file_path, 'rb') as f:
            content = f.read()
        assert content == b"test content"

    def test_atomic_write_creates_parent_dirs(self, mock_config, mock_data_syncer, temp_dir):
        """Test atomic_write creates parent directories"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()

        file_path = temp_dir / "deep" / "nested" / "path" / "file.txt"
        interface.atomic_write(file_path, data="content", mode='w')

        assert file_path.exists()
        assert file_path.parent.exists()

    def test_atomic_write_no_data_or_stream(self, mock_config, mock_data_syncer, temp_dir):
        """Test atomic_write raises error when neither data nor stream provided"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()

        file_path = temp_dir / "test_file.txt"
        with pytest.raises(ValueError):
            interface.atomic_write(file_path)

    def test_atomic_delete_existing_file(self, mock_config, mock_data_syncer, temp_dir):
        """Test atomic_delete removes existing file"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()

        file_path = temp_dir / "test_file.txt"
        file_path.write_text("content")
        assert file_path.exists()

        interface.atomic_delete(file_path)
        assert not file_path.exists()

    def test_atomic_delete_nonexistent_file(self, mock_config, mock_data_syncer, temp_dir):
        """Test atomic_delete handles nonexistent file gracefully"""
        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()

        file_path = temp_dir / "nonexistent.txt"
        # Should not raise exception
        interface.atomic_delete(file_path)

    def test_find_avail_temp_file_path(self, mock_config, mock_data_syncer, temp_dir):
        """Test find_avail_temp_file_path returns valid path"""
        mock_config.save_data_path = temp_dir / "data"
        mock_config.temp_dir = temp_dir / "temp"
        interface = DataInterface()

        temp_path = interface.find_avail_temp_file_path(ext='.txt')
        assert temp_path.suffix == '.txt'
        assert not temp_path.exists()

    def test_temp_file_ctx_creates_and_cleans_up(self, mock_config, mock_data_syncer, temp_dir):
        """Test temp_file_ctx context manager"""
        mock_config.save_data_path = temp_dir / "data"
        mock_config.temp_dir = temp_dir / "temp"
        interface = DataInterface()

        temp_path = None
        with interface.temp_file_ctx(ext='txt') as tp:
            temp_path = tp
            assert temp_path.exists()
            assert temp_path.suffix == '.txt'

        # File should be cleaned up after context
        assert not temp_path.exists()

    def test_temp_file_ctx_cleans_up_on_exception(self, mock_config, mock_data_syncer, temp_dir):
        """Test temp_file_ctx cleans up even on exception"""
        mock_config.save_data_path = temp_dir / "data"
        mock_config.temp_dir = temp_dir / "temp"
        interface = DataInterface()

        temp_path = None
        try:
            with interface.temp_file_ctx(ext='txt') as tp:
                temp_path = tp
                assert temp_path.exists()
                raise ValueError("Test exception")
        except ValueError:
            pass

        # File should be cleaned up even after exception
        assert not temp_path.exists()

    def test_save_model_load_model_round_trip(self, mock_config, mock_data_syncer, temp_dir):
        """save_model then load_model returns an equal model; missing path → None."""
        from pydantic import BaseModel

        class Sample(BaseModel):
            name: str
            count: int = 0
            tags: list[str] = []

        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()
        path = temp_dir / "sample.json"

        # Missing file returns None (sync=False so we don't hit the syncer).
        assert interface.load_model(path, Sample, sync=False) is None

        original = Sample(name="widget", count=3, tags=["a", "b"])
        interface._save_model(path, original)
        loaded = interface.load_model(path, Sample, sync=False)
        assert loaded == original

    def test_edit_model_saves_changed_model_under_lock(self, mock_config, mock_data_syncer, temp_dir):
        from pydantic import BaseModel

        class Sample(BaseModel):
            count: int = 0

        lock = MagicMock()
        mock_config.save_data_path = temp_dir / "data"
        with patch("web_app.redis_client.rmw_lock", return_value=lock) as lock_factory:
            interface = DataInterface()
            path = interface.app_path("sample.json")
            with interface.edit_model(path, Sample) as sample:
                sample.count = 1

        lock_factory.assert_called_once_with(f"model:{path.relative_to(temp_dir)}")
        lock.__enter__.assert_called_once()
        lock.__exit__.assert_called_once()
        assert interface.load_model(path, Sample, sync=False) == Sample(count=1)

    def test_edit_model_discards_changes_when_edit_fails(self, mock_config, mock_data_syncer, temp_dir):
        from pydantic import BaseModel

        class Sample(BaseModel):
            count: int = 0

        mock_config.save_data_path = temp_dir / "data"
        interface = DataInterface()
        path = interface.app_path("sample.json")
        interface._save_model(path, Sample(count=1))

        with pytest.raises(RuntimeError):
            with interface.edit_model(path, Sample) as sample:
                sample.count = 2
                raise RuntimeError("abort edit")

        assert interface.load_model(path, Sample, sync=False) == Sample(count=1)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
