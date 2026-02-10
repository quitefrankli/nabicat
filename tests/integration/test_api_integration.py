"""Integration tests for API endpoints - server is started automatically

Tests use the new encrypted protocol where all requests are sent in an encrypted
`req` field using gzip + Fernet encryption + base64 encoding.
"""

import pytest
import requests
import json
import base64
import gzip
import os
from io import BytesIO
from cryptography.fernet import Fernet


# Test credentials
USERNAME = "admin"
PASSWORD = "admin"
FILENAME = "testfile.json"
DATA = '{"key": "value"}'

# Encryption key for tests - use the one set by conftest.py
# This ensures client and server use the same key
TEST_ENCRYPTION_KEY = os.environ.get('SYMMETRIC_ENCRYPTION_KEY', '').encode('utf-8')
if not TEST_ENCRYPTION_KEY:
    # Generate and set if not already set
    TEST_ENCRYPTION_KEY = Fernet.generate_key()
    os.environ['SYMMETRIC_ENCRYPTION_KEY'] = TEST_ENCRYPTION_KEY.decode('utf-8')


def encrypt_payload(payload: dict) -> str:
    """Encrypt a payload the same way the client does:
    1. JSON encode
    2. Gzip compress  
    3. Fernet encrypt
    4. Base64 encode
    """
    json_data = json.dumps(payload).encode('utf-8')
    compressed_data = gzip.compress(json_data)
    encrypted_data = Fernet(TEST_ENCRYPTION_KEY).encrypt(compressed_data)
    return base64.b64encode(encrypted_data).decode('utf-8')


def make_encrypted_request(url: str, payload: dict) -> requests.Response:
    """Make an encrypted request to the API"""
    encrypted = encrypt_payload(payload)
    return requests.post(url, json={"req": encrypted})


def get_auth_payload():
    """Get authentication payload with credentials"""
    return {
        "username": USERNAME,
        "password": PASSWORD
    }


@pytest.mark.integration
class TestAPIEncryptedProtocol:
    """Integration tests for the new encrypted API protocol"""

    def test_push_data_encrypted(self, server_url):
        """Test pushing data via encrypted API request"""
        url = f"{server_url}/api/push"
        payload = {
            **get_auth_payload(),
            "name": "encrypted_test_file.json",
            "data": "encrypted test data"
        }
        response = make_encrypted_request(url, payload)
        assert response.status_code == 200
        assert response.json().get("success") is True

    def test_pull_data_encrypted(self, server_url):
        """Test pulling data via encrypted API request"""
        # First push some data
        push_url = f"{server_url}/api/push"
        push_payload = {
            **get_auth_payload(),
            "name": "pull_test_file.txt",
            "data": "test content for pull"
        }
        make_encrypted_request(push_url, push_payload)

        # Now pull it back
        pull_url = f"{server_url}/api/pull"
        pull_payload = {
            **get_auth_payload(),
            "name": "pull_test_file.txt"
        }
        response = make_encrypted_request(pull_url, pull_payload)
        assert response.status_code == 200
        assert response.json().get("success") is True
        assert response.json().get("data") == "test content for pull"

    def test_list_files_encrypted(self, server_url):
        """Test listing files via encrypted API request"""
        url = f"{server_url}/api/list"
        payload = get_auth_payload()
        response = make_encrypted_request(url, payload)
        assert response.status_code == 200
        assert response.json().get("success") is True
        files = response.json().get("files", [])
        assert isinstance(files, list)

    def test_delete_data_encrypted(self, server_url):
        """Test deleting data via encrypted API request"""
        # First push a file to delete
        push_url = f"{server_url}/api/push"
        push_payload = {
            **get_auth_payload(),
            "name": "file_to_delete.txt",
            "data": "temporary data"
        }
        make_encrypted_request(push_url, push_payload)

        # Delete the file
        delete_url = f"{server_url}/api/delete"
        delete_payload = {
            **get_auth_payload(),
            "name": "file_to_delete.txt"
        }
        response = make_encrypted_request(delete_url, delete_payload)
        assert response.status_code == 200
        assert response.json().get("success") is True

        # Confirm deletion by trying to pull
        pull_url = f"{server_url}/api/pull"
        pull_payload = {
            **get_auth_payload(),
            "name": "file_to_delete.txt"
        }
        response = make_encrypted_request(pull_url, pull_payload)
        assert response.status_code == 404

    def test_invalid_credentials_encrypted(self, server_url):
        """Test encrypted request with invalid credentials"""
        url = f"{server_url}/api/list"
        payload = {
            "username": "wrong_user",
            "password": "wrong_pass"
        }
        response = make_encrypted_request(url, payload)
        # API returns 400 for authentication errors (APIError caught)
        assert response.status_code == 400
        assert "Invalid credentials" in response.json().get("error", "")

    def test_missing_required_fields_encrypted(self, server_url):
        """Test encrypted request with missing required fields"""
        url = f"{server_url}/api/push"
        payload = {
            **get_auth_payload()
            # Missing "name" and "data"
        }
        response = make_encrypted_request(url, payload)
        assert response.status_code == 400

    def test_large_payload_encrypted(self, server_url):
        """Test encrypted request with large payload"""
        url = f"{server_url}/api/push"
        large_data = "x" * 100000  # 100KB of data
        payload = {
            **get_auth_payload(),
            "name": "large_file.txt",
            "data": large_data
        }
        response = make_encrypted_request(url, payload)
        assert response.status_code == 200
        assert response.json().get("success") is True

        # Verify we can pull it back
        pull_url = f"{server_url}/api/pull"
        pull_payload = {
            **get_auth_payload(),
            "name": "large_file.txt"
        }
        response = make_encrypted_request(pull_url, pull_payload)
        assert response.status_code == 200
        assert response.json().get("data") == large_data

    def test_nested_json_data_encrypted(self, server_url):
        """Test encrypted request with nested JSON data"""
        url = f"{server_url}/api/push"
        nested_data = {
            "users": [
                {"name": "Alice", "age": 30},
                {"name": "Bob", "age": 25}
            ],
            "metadata": {
                "version": "1.0",
                "count": 2
            }
        }
        payload = {
            **get_auth_payload(),
            "name": "nested_data.json",
            "data": json.dumps(nested_data)
        }
        response = make_encrypted_request(url, payload)
        assert response.status_code == 200

        # Pull it back and verify
        pull_url = f"{server_url}/api/pull"
        pull_payload = {
            **get_auth_payload(),
            "name": "nested_data.json"
        }
        response = make_encrypted_request(pull_url, pull_payload)
        assert response.status_code == 200
        retrieved_data = json.loads(response.json().get("data"))
        assert retrieved_data == nested_data

    def test_pull_nonexistent_file_encrypted(self, server_url):
        """Test pulling a file that doesn't exist via encrypted request"""
        url = f"{server_url}/api/pull"
        payload = {
            **get_auth_payload(),
            "name": "nonexistent_file_xyz123.txt"
        }
        response = make_encrypted_request(url, payload)
        assert response.status_code == 404

    def test_delete_nonexistent_file_encrypted(self, server_url):
        """Test deleting a file that doesn't exist via encrypted request"""
        url = f"{server_url}/api/delete"
        payload = {
            **get_auth_payload(),
            "name": "nonexistent_file_xyz123.txt"
        }
        response = make_encrypted_request(url, payload)
        assert response.status_code == 404


@pytest.mark.integration
class TestAPIBackwardCompatibility:
    """Tests to ensure backward compatibility with unencrypted requests"""

    def test_unencrypted_request_still_works(self, server_url):
        """Test that unencrypted requests still work (backward compatibility)"""
        url = f"{server_url}/api/list"
        payload = get_auth_payload()
        response = requests.post(url, json=payload)
        # Should work with or without encryption
        assert response.status_code == 200

    def test_unencrypted_push_and_pull(self, server_url):
        """Test unencrypted push and pull"""
        # Push
        push_url = f"{server_url}/api/push"
        push_payload = {
            **get_auth_payload(),
            "name": "unencrypted_test.txt",
            "data": "test data"
        }
        response = requests.post(push_url, json=push_payload)
        assert response.status_code == 200

        # Pull
        pull_url = f"{server_url}/api/pull"
        pull_payload = {
            **get_auth_payload(),
            "name": "unencrypted_test.txt"
        }
        response = requests.post(pull_url, json=pull_payload)
        assert response.status_code == 200
        assert response.json().get("data") == "test data"


@pytest.mark.integration
class TestAPICookieUpload:
    """Tests for cookie upload endpoint (admin only)"""

    def test_push_cookie_encrypted_admin(self, server_url):
        """Test uploading cookies via encrypted request as admin"""
        url = f"{server_url}/api/push_cookie"
        cookie_data = "# Netscape HTTP Cookie File\n# Test cookie data"
        payload = {
            **get_auth_payload(),  # admin credentials
            "cookie": cookie_data
        }
        response = make_encrypted_request(url, payload)
        assert response.status_code == 200
        assert response.json().get("success") is True


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'integration'])
