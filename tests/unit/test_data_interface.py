"""Unit tests for data_interface module"""

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface, DataSyncer
from web_app.users import User


def test_users_file_rejects_duplicate_identity_and_folder(config) -> None:
    from pydantic import ValidationError

    from web_app.users import UsersFile

    first = User(username="alice", folder="alice-folder")
    with pytest.raises(ValidationError):
        UsersFile(root=[first, first.model_copy()])
    with pytest.raises(ValidationError):
        UsersFile(root=[first, User(username="bob", folder="alice-folder")])


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Use real configuration with isolated storage and no external sync."""
    config = ConfigManager()
    monkeypatch.setattr(config, "debug_mode", False)
    monkeypatch.setattr(config, "production_data_root", tmp_path / "data")
    monkeypatch.setattr(config, "debug_data_root", tmp_path / "debug-data")
    monkeypatch.setattr(config, "use_offline_syncer", True)
    monkeypatch.setattr(DataSyncer, "_instance", None)
    return config


class TestDataInterface:
    """Tests for DataInterface"""

    def test_load_users_empty(self, config, tmp_path):
        """Test load_users when no users file exists"""
        interface = DataInterface()
        users = interface.load_users()
        assert users == {}

    def test_load_users_with_data(self, config, tmp_path):
        """Test load_users with existing users file"""
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

    def test_edit_users(self, config, tmp_path):
        interface = DataInterface()

        users = [
            User(username='user1', password='pass1', folder='folder1', is_admin=True),
            User(username='user2', password='pass2', folder='folder2', is_admin=False),
        ]
        with interface.edit_users() as users_file:
            for user in users:
                users_file.add(user)

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

    def test_generate_new_user_unique_folder(self, config, tmp_path):
        """Test generate_new_user creates user with unique folder"""
        interface = DataInterface()

        # Create initial user file
        interface.users_file.parent.mkdir(parents=True, exist_ok=True)
        initial_users = [
            User(username='existing', password='pass', folder='existing_folder', is_admin=False)
        ]
        with interface.edit_users() as users_file:
            for user in initial_users:
                users_file.add(user)

        new_user = interface.generate_new_user('newuser', 'newpass')
        assert new_user.id == 'newuser'
        assert new_user.password != 'newpass'
        assert new_user.verify_password('newpass')
        assert new_user.folder != 'existing_folder'
        assert len(new_user.folder) == 10

    def test_generate_new_user_duplicate_prevention(self, config, tmp_path):
        """Test generate_new_user retries when folder conflicts"""
        interface = DataInterface()

        # Mock generate_random_string to return duplicates then unique
        with patch.object(interface, 'generate_random_string') as mock_generate:
            mock_generate.side_effect = ['existing_folder', 'existing_folder', 'unique_folder']

            # Create initial user file
            interface.users_file.parent.mkdir(parents=True, exist_ok=True)
            initial_users = [
                User(username='existing', password='pass', folder='existing_folder', is_admin=False)
            ]
            with interface.edit_users() as users_file:
                for user in initial_users:
                    users_file.add(user)

            new_user = interface.generate_new_user('newuser', 'newpass')
            assert new_user.folder == 'unique_folder'

    @patch('web_app.data_interface.Repo')
    def test_generate_metadata_file(self, mock_repo, config, tmp_path):
        """Test generate_metadata_file creates metadata"""
        mock_repo.return_value.head.commit.hexsha = "abc123def456"

        interface = DataInterface()
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)

        interface.generate_metadata_file(backup_dir)

        metadata_file = backup_dir / "metadata.json"
        assert metadata_file.exists()

        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
        assert metadata['commit_hash'] == 'abc123def456'

    def test_generate_backup_dir(self, config, tmp_path):
        """Test generate_backup_dir creates timestamped directory"""
        config.backup_max_count = 8
        interface = DataInterface()

        backup_dir = interface.generate_backup_dir()
        assert backup_dir.exists()
        assert backup_dir.parent == interface.backups_directory

    @patch('web_app.data_interface.Repo')
    def test_backup_data_does_not_copy_logs(
        self,
        mock_repo,
        config,
        tmp_path,
    ):
        mock_repo.return_value.head.commit.hexsha = "abc123"
        data_root = config.save_data_path
        data_root.mkdir(parents=True)
        (data_root / "users.json").write_text("[]")
        logs_dir = data_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "web_app.log").write_text("request log")

        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        DataInterface().backup_data(backup_dir)

        assert (backup_dir / "users.json").read_text() == "[]"
        assert json.loads((backup_dir / "metadata.json").read_text()) == {
            "commit_hash": "abc123"
        }
        assert not (backup_dir / "logs").exists()

    def test_atomic_write_with_data(self, config, tmp_path):
        """Test atomic_write with data string"""
        interface = DataInterface()

        file_path = tmp_path / "test_file.txt"
        interface.atomic_write(file_path, data="test content")

        assert file_path.exists()
        with open(file_path, 'r') as f:
            content = f.read()
        assert content == "test content"

    def test_atomic_write_with_stream(self, config, tmp_path):
        """Test atomic_write with stream"""
        interface = DataInterface()

        file_path = tmp_path / "test_file.bin"
        stream = BytesIO(b"test content")
        interface.atomic_write(file_path, stream=stream)

        assert file_path.exists()
        with open(file_path, 'rb') as f:
            content = f.read()
        assert content == b"test content"

    def test_atomic_write_creates_parent_dirs(self, config, tmp_path):
        """Test atomic_write creates parent directories"""
        interface = DataInterface()

        file_path = tmp_path / "deep" / "nested" / "path" / "file.txt"
        interface.atomic_write(file_path, data="content")

        assert file_path.exists()
        assert file_path.parent.exists()

    def test_atomic_write_no_data_or_stream(self, config, tmp_path):
        """Test atomic_write raises error when neither data nor stream provided"""
        interface = DataInterface()

        file_path = tmp_path / "test_file.txt"
        with pytest.raises(ValueError):
            interface.atomic_write(file_path)

    def test_atomic_delete_existing_file(self, config, tmp_path):
        """Test atomic_delete removes existing file"""
        interface = DataInterface()

        file_path = tmp_path / "test_file.txt"
        file_path.write_text("content")
        assert file_path.exists()

        interface.atomic_delete(file_path)
        assert not file_path.exists()

    def test_atomic_delete_nonexistent_file(self, config, tmp_path):
        """Test atomic_delete handles nonexistent file gracefully"""
        interface = DataInterface()

        file_path = tmp_path / "nonexistent.txt"
        # Should not raise exception
        interface.atomic_delete(file_path)

    def test_find_avail_temp_file_path(self, config, tmp_path):
        """Test find_avail_temp_file_path returns valid path"""
        interface = DataInterface()

        temp_path = interface.find_avail_temp_file_path(ext='.txt')
        assert temp_path.suffix == '.txt'
        assert not temp_path.exists()

    def test_save_model_load_model_round_trip(self, config, tmp_path):
        """save_model then load_model returns an equal model; missing path → None."""
        from pydantic import BaseModel

        class Sample(BaseModel):
            name: str
            count: int = 0
            tags: list[str] = []

        interface = DataInterface()
        path = tmp_path / "sample.json"

        # Missing file returns None (sync=False so we don't hit the syncer).
        assert interface.load_model(path, Sample, sync=False) is None

        original = Sample(name="widget", count=3, tags=["a", "b"])
        interface._save_model(path, original)
        loaded = interface.load_model(path, Sample, sync=False)
        assert loaded == original

    def test_edit_model_saves_changed_model_under_lock(self, config, tmp_path):
        from pydantic import BaseModel

        class Sample(BaseModel):
            count: int = 0

        lock = MagicMock()
        with patch("web_app.redis_client.rmw_lock", return_value=lock) as lock_factory:
            interface = DataInterface()
            path = interface.app_path("sample.json")
            with interface.edit_model(path, Sample) as sample:
                sample.count = 1

        lock_factory.assert_called_once_with(f"model:{path.relative_to(tmp_path)}")
        lock.__enter__.assert_called_once()
        lock.__exit__.assert_called_once()
        assert interface.load_model(path, Sample, sync=False) == Sample(count=1)

    def test_edit_model_discards_changes_when_edit_fails(self, config, tmp_path):
        from pydantic import BaseModel

        class Sample(BaseModel):
            count: int = 0

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


def test_private_writes_and_backups_ignore_permissive_source_modes(config, tmp_path):
    import os
    import stat

    interface = DataInterface()
    previous_umask = os.umask(0)
    try:
        source = config.save_data_path / "nested" / "record.json"
        interface.atomic_write(source, data="{}")
        assert stat.S_IMODE(source.stat().st_mode) == config.atomic_write_file_mode
        assert all(
            stat.S_IMODE(path.stat().st_mode) == config.app_data_dir_mode
            for path in (config.save_data_path, source.parent)
        )
        # Backups must not propagate legacy readable source permissions.
        source.chmod(0o644)
        source.parent.chmod(0o755)
        interface.atomic_write(interface.users_file, data="[]")
        interface.users_file.chmod(0o644)
        backup = interface.generate_backup_dir()
        with patch.object(interface, "generate_metadata_file"):
            interface.backup_data(backup)
        interface._backup_subtree(source.parent, backup, "records")
        assert (backup / "records" / source.name).read_text() == "{}"
        assert (backup / "users.json").read_text() == "[]"
        for path in (backup.parent, backup, *backup.rglob("*")):
            expected = config.app_data_dir_mode if path.is_dir() else config.app_data_file_mode
            assert stat.S_IMODE(path.stat().st_mode) == expected
        # Replacing a legacy file also restores the private mode.
        interface.atomic_write(source, data="{\"updated\": true}")
        assert stat.S_IMODE(source.stat().st_mode) == config.atomic_write_file_mode
    finally:
        os.umask(previous_umask)
