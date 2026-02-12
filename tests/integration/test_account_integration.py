"""Integration tests for account deletion behavior."""

import json
import uuid
from pathlib import Path

import pytest
import requests


DEBUG_SAVE_DATA_PATH = Path.home() / ".nabicat_debug" / "data"
USERS_FILE = DEBUG_SAVE_DATA_PATH / "users.json"


def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users_data = json.load(f)
    return {user["username"]: user for user in users_data}


@pytest.mark.integration
class TestAccountDeletionIntegration:
    def test_delete_account_removes_sub_app_data(self, server_url):
        username = f"integration_user_{uuid.uuid4().hex[:8]}"
        password = "integration_pass_123"

        session = requests.Session()

        register_response = session.post(
            f"{server_url}/account/register",
            data={"username": username, "password": password},
            allow_redirects=False,
        )
        assert register_response.status_code == 302

        users = _load_users()
        assert username in users
        user_folder = users[username]["folder"]

        # Seed data in sub-app directories that implement delete_user_data
        api_user_dir = DEBUG_SAVE_DATA_PATH / "api_data" / user_folder
        todoist2_user_dir = DEBUG_SAVE_DATA_PATH / "todoist2" / user_folder
        metrics_user_dir = DEBUG_SAVE_DATA_PATH / "metrics" / user_folder
        jswipe_user_dir = DEBUG_SAVE_DATA_PATH / "jswipe" / user_folder

        for directory, filename, content in [
            (api_user_dir, "payload.txt", "hello"),
            (todoist2_user_dir, "data.json", "{}"),
            (metrics_user_dir, "data.json", "{}"),
            (jswipe_user_dir, "jobs.json", '{"jobs": {}}'),
        ]:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / filename).write_text(content, encoding="utf-8")

        # Seed Tubio metadata + media files for this user
        tubio_dir = DEBUG_SAVE_DATA_PATH / "tubio"
        tubio_audio_dir = tubio_dir / "audio"
        tubio_thumb_dir = tubio_dir / "thumbnails"
        tubio_metadata_file = tubio_dir / "metadata.json"
        tubio_audio_crc = 90000123

        tubio_audio_dir.mkdir(parents=True, exist_ok=True)
        tubio_thumb_dir.mkdir(parents=True, exist_ok=True)
        (tubio_audio_dir / f"{tubio_audio_crc}.m4a").write_bytes(b"audio")
        (tubio_thumb_dir / f"{tubio_audio_crc}.jpg").write_bytes(b"thumb")

        tubio_metadata = {
            "users": {
                username: {
                    "user_id": username,
                    "playlists": {
                        "Favourites": {
                            "name": "Favourites",
                            "audio_crcs": [tubio_audio_crc],
                        }
                    },
                }
            },
            "audios": {
                str(tubio_audio_crc): {
                    "crc": tubio_audio_crc,
                    "title": "integration track",
                    "yt_video_id": "",
                    "is_cached": True,
                    "source_url": "",
                }
            },
        }
        tubio_metadata_file.write_text(json.dumps(tubio_metadata, indent=2), encoding="utf-8")

        # Seed FileStore metadata + blobs for this user
        file_store_dir = DEBUG_SAVE_DATA_PATH / "file_store"
        file_store_files_dir = file_store_dir / "files"
        file_store_thumb_dir = file_store_dir / "thumbnails"
        file_store_metadata_file = file_store_dir / "metadata.json"
        file_store_crc = 70000123

        file_store_files_dir.mkdir(parents=True, exist_ok=True)
        file_store_thumb_dir.mkdir(parents=True, exist_ok=True)
        (file_store_files_dir / str(file_store_crc)).write_bytes(b"blob")
        (file_store_thumb_dir / f"{file_store_crc}.jpg").write_bytes(b"thumb")

        file_store_metadata = {
            "users": {
                username: {
                    "user_id": username,
                    "files": [
                        {
                            "crc": file_store_crc,
                            "original_name": "integration_file.txt",
                        }
                    ],
                }
            },
            "files": {
                str(file_store_crc): {
                    "crc": file_store_crc,
                    "original_name": "integration_file.txt",
                    "size": 4,
                    "upload_date": "2026-01-01T00:00:00",
                    "mime_type": "text/plain",
                }
            },
        }
        file_store_metadata_file.write_text(json.dumps(file_store_metadata, indent=2), encoding="utf-8")

        delete_response = session.post(
            f"{server_url}/account/delete",
            data={"password": password},
            allow_redirects=False,
        )
        assert delete_response.status_code == 302

        users_after_delete = _load_users()
        assert username not in users_after_delete

        # Sub-app directory cleanup checks
        assert not api_user_dir.exists()
        assert not todoist2_user_dir.exists()
        assert not metrics_user_dir.exists()
        assert not jswipe_user_dir.exists()

        # Tubio cleanup checks
        with open(tubio_metadata_file, "r", encoding="utf-8") as f:
            tubio_metadata_after = json.load(f)
        assert username not in tubio_metadata_after.get("users", {})
        assert str(tubio_audio_crc) not in tubio_metadata_after.get("audios", {})
        assert not (tubio_audio_dir / f"{tubio_audio_crc}.m4a").exists()
        assert not (tubio_thumb_dir / f"{tubio_audio_crc}.jpg").exists()

        # FileStore cleanup checks
        with open(file_store_metadata_file, "r", encoding="utf-8") as f:
            file_store_metadata_after = json.load(f)
        assert username not in file_store_metadata_after.get("users", {})
        assert str(file_store_crc) not in file_store_metadata_after.get("files", {})
        assert not (file_store_files_dir / str(file_store_crc)).exists()
        assert not (file_store_thumb_dir / f"{file_store_crc}.jpg").exists()

    def test_delete_last_admin_account_is_blocked(self, server_url):
        username = f"integration_admin_{uuid.uuid4().hex[:8]}"
        password = "integration_admin_pass_123"
        folder = f"admin_folder_{uuid.uuid4().hex[:8]}"

        original_exists = USERS_FILE.exists()
        original_contents = USERS_FILE.read_text(encoding="utf-8") if original_exists else ""

        try:
            USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            single_admin_users = [
                {
                    "username": username,
                    "password": password,
                    "folder": folder,
                    "is_admin": True,
                }
            ]
            USERS_FILE.write_text(json.dumps(single_admin_users, indent=2), encoding="utf-8")

            session = requests.Session()
            login_response = session.post(
                f"{server_url}/account/login",
                data={"username": username, "password": password},
                allow_redirects=False,
            )
            assert login_response.status_code == 302

            delete_response = session.post(
                f"{server_url}/account/delete",
                data={"password": password},
                allow_redirects=False,
            )
            assert delete_response.status_code == 302
            assert delete_response.headers.get("Location", "").endswith("/account/delete")

            users_after_attempt = _load_users()
            assert username in users_after_attempt
            assert users_after_attempt[username]["is_admin"] is True
        finally:
            if original_exists:
                USERS_FILE.write_text(original_contents, encoding="utf-8")
            elif USERS_FILE.exists():
                USERS_FILE.unlink()
