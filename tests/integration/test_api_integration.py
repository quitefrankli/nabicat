"""Integration tests for API endpoints - server is started automatically

Tests use the new hybrid encrypted protocol where all requests are sent with:
1. Handshake to get ephemeral RSA public key
2. Client generates random AES key
3. Payload encrypted: gzip -> AES-GCM -> base64
4. AES key encrypted: RSA-OAEP -> base64
"""

import pytest
import requests
import json
import base64
import gzip
import os
from io import BytesIO
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# Test credentials
USERNAME = "admin"
PASSWORD = "admin"
FILENAME = "testfile.json"
DATA = '{"key": "value"}'


def do_handshake(server_url: str) -> tuple[str, str]:
    """Perform handshake with server to get ephemeral RSA public key."""
    url = f"{server_url}/api/handshake"
    response = requests.post(url)
    
    if response.status_code != 200:
        raise ConnectionError(f"Handshake failed: {response.status_code} - {response.text}")
    
    data = response.json()
    if not data.get("success"):
        raise ConnectionError(f"Handshake failed: {data.get('error', 'Unknown error')}")
    
    return data["session_id"], data["public_key"]


def hybrid_encrypt(data: bytes, public_key_pem: str, session_id: str) -> dict:
    """
    Encrypt data using hybrid encryption (RSA + AES-GCM).
    
    1. Generate random 256-bit AES key
    2. Compress and encrypt data with AES-GCM
    3. Encrypt AES key with RSA public key
    4. Return payload dict for server
    """
    # Generate random 256-bit AES key
    aes_key = AESGCM.generate_key(bit_length=256)
    aesgcm = AESGCM(aes_key)
    
    # Compress data
    compressed_data = gzip.compress(data)
    
    # Encrypt with AES-GCM
    nonce = os.urandom(12)  # 96-bit nonce for GCM
    encrypted_data = aesgcm.encrypt(nonce, compressed_data, None)
    
    # Load RSA public key
    public_key = serialization.load_pem_public_key(public_key_pem.encode('utf-8'))
    
    # Encrypt AES key with RSA-OAEP
    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    
    return {
        "session_id": session_id,
        "encrypted_key": base64.b64encode(encrypted_key).decode('utf-8'),
        "encrypted_data": base64.b64encode(encrypted_data).decode('utf-8'),
        "nonce": base64.b64encode(nonce).decode('utf-8')
    }


def encrypt_payload(server_url: str, payload: dict) -> dict:
    """
    Encrypt a payload using hybrid encryption:
    1. Do handshake to get ephemeral public key
    2. JSON encode payload
    3. Gzip compress
    4. Encrypt with AES-GCM
    5. Encrypt AES key with RSA-OAEP
    6. Return encrypted payload dict
    """
    session_id, public_key = do_handshake(server_url)
    json_data = json.dumps(payload).encode('utf-8')
    return hybrid_encrypt(json_data, public_key, session_id)


def make_encrypted_request(server_url: str, endpoint: str, payload: dict) -> requests.Response:
    """Make an encrypted request to the API"""
    url = f"{server_url}/{endpoint}"
    encrypted = encrypt_payload(server_url, payload)
    return requests.post(url, json={"req": encrypted})


def get_auth_payload():
    """Get authentication payload with credentials"""
    return {
        "username": USERNAME,
        "password": PASSWORD
    }


def encode_data_for_upload(data: str) -> str:
    """Encode data as gzip+base64 for upload (client-side encoding)"""
    compressed = gzip.compress(data.encode('utf-8'))
    return base64.b64encode(compressed).decode('utf-8')


def decode_data_from_download(encoded_data: str) -> str:
    """Decode data from gzip+base64 (client-side decoding)"""
    decoded = base64.b64decode(encoded_data)
    return gzip.decompress(decoded).decode('utf-8')


@pytest.mark.integration
class TestAPIEncryptedProtocol:
    """Integration tests for the new encrypted API protocol"""

    def test_push_data_encrypted(self, server_url):
        """Test pushing data via encrypted API request"""
        payload = {
            **get_auth_payload(),
            "name": "encrypted_test_file.json",
            "data": encode_data_for_upload("encrypted test data")
        }
        response = make_encrypted_request(server_url, "api/push", payload)
        assert response.status_code == 200
        assert response.json().get("success") is True

    def test_pull_data_encrypted(self, server_url):
        """Test pulling data via encrypted API request"""
        # First push some data
        push_payload = {
            **get_auth_payload(),
            "name": "pull_test_file.txt",
            "data": encode_data_for_upload("test content for pull")
        }
        make_encrypted_request(server_url, "api/push", push_payload)

        # Now pull it back
        pull_payload = {
            **get_auth_payload(),
            "name": "pull_test_file.txt"
        }
        response = make_encrypted_request(server_url, "api/pull", pull_payload)
        assert response.status_code == 200
        assert response.json().get("success") is True
        assert decode_data_from_download(response.json().get("data")) == "test content for pull"

    def test_list_files_encrypted(self, server_url):
        """Test listing files via encrypted API request"""
        payload = get_auth_payload()
        response = make_encrypted_request(server_url, "api/list", payload)
        assert response.status_code == 200
        assert response.json().get("success") is True
        files = response.json().get("files", [])
        assert isinstance(files, list)

    def test_delete_data_encrypted(self, server_url):
        """Test deleting data via encrypted API request"""
        # First push a file to delete
        push_payload = {
            **get_auth_payload(),
            "name": "file_to_delete.txt",
            "data": encode_data_for_upload("temporary data")
        }
        make_encrypted_request(server_url, "api/push", push_payload)

        # Delete the file
        delete_payload = {
            **get_auth_payload(),
            "name": "file_to_delete.txt"
        }
        response = make_encrypted_request(server_url, "api/delete", delete_payload)
        assert response.status_code == 200
        assert response.json().get("success") is True

        # Confirm deletion by trying to pull
        pull_payload = {
            **get_auth_payload(),
            "name": "file_to_delete.txt"
        }
        response = make_encrypted_request(server_url, "api/pull", pull_payload)
        assert response.status_code == 404

    def test_invalid_credentials_encrypted(self, server_url):
        """Test encrypted request with invalid credentials"""
        payload = {
            "username": "wrong_user",
            "password": "wrong_pass"
        }
        response = make_encrypted_request(server_url, "api/list", payload)
        # API returns 400 for authentication errors (APIError caught)
        assert response.status_code == 400
        assert "Invalid credentials" in response.json().get("error", "")

    def test_missing_required_fields_encrypted(self, server_url):
        """Test encrypted request with missing required fields"""
        payload = {
            **get_auth_payload()
            # Missing "name" and "data"
        }
        response = make_encrypted_request(server_url, "api/push", payload)
        assert response.status_code == 400

    def test_large_payload_encrypted(self, server_url):
        """Test encrypted request with large payload"""
        large_data = "x" * 100000  # 100KB of data
        payload = {
            **get_auth_payload(),
            "name": "large_file.txt",
            "data": encode_data_for_upload(large_data)
        }
        response = make_encrypted_request(server_url, "api/push", payload)
        assert response.status_code == 200
        assert response.json().get("success") is True

        # Verify we can pull it back
        pull_payload = {
            **get_auth_payload(),
            "name": "large_file.txt"
        }
        response = make_encrypted_request(server_url, "api/pull", pull_payload)
        assert response.status_code == 200
        assert decode_data_from_download(response.json().get("data")) == large_data

    def test_nested_json_data_encrypted(self, server_url):
        """Test encrypted request with nested JSON data"""
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
            "data": encode_data_for_upload(json.dumps(nested_data))
        }
        response = make_encrypted_request(server_url, "api/push", payload)
        assert response.status_code == 200

        # Pull it back and verify
        pull_payload = {
            **get_auth_payload(),
            "name": "nested_data.json"
        }
        response = make_encrypted_request(server_url, "api/pull", pull_payload)
        assert response.status_code == 200
        retrieved_data = json.loads(decode_data_from_download(response.json().get("data")))
        assert retrieved_data == nested_data

    def test_pull_nonexistent_file_encrypted(self, server_url):
        """Test pulling a file that doesn't exist via encrypted request"""
        payload = {
            **get_auth_payload(),
            "name": "nonexistent_file_xyz123.txt"
        }
        response = make_encrypted_request(server_url, "api/pull", payload)
        assert response.status_code == 404

    def test_delete_nonexistent_file_encrypted(self, server_url):
        """Test deleting a file that doesn't exist via encrypted request"""
        payload = {
            **get_auth_payload(),
            "name": "nonexistent_file_xyz123.txt"
        }
        response = make_encrypted_request(server_url, "api/delete", payload)
        assert response.status_code == 404

    def test_expired_session_id(self, server_url):
        """Test that using an expired/invalid session ID fails"""
        # Create a payload with fake session ID
        fake_payload = {
            "session_id": "invalid-session-id-12345",
            "encrypted_key": base64.b64encode(b"fake_key").decode('utf-8'),
            "encrypted_data": base64.b64encode(b"fake_data").decode('utf-8'),
            "nonce": base64.b64encode(os.urandom(12)).decode('utf-8')
        }
        
        url = f"{server_url}/api/list"
        response = requests.post(url, json={"req": fake_payload})
        assert response.status_code == 400
        assert "Invalid or expired session" in response.json().get("error", "")


@pytest.mark.integration
class TestAPIPlainRequests:
    """Tests for plain (unencrypted) requests - still supported for backward compatibility"""

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
            "data": encode_data_for_upload("test data")
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
        assert decode_data_from_download(response.json().get("data")) == "test data"


@pytest.mark.integration
class TestAPICookieUpload:
    """Tests for cookie upload endpoint (admin only)"""

    def test_push_cookie_encrypted_admin(self, server_url):
        """Test uploading cookies via encrypted request as admin"""
        cookie_data = "# Netscape HTTP Cookie File\n# Test cookie data"
        payload = {
            **get_auth_payload(),  # admin credentials
            "cookie": cookie_data
        }
        response = make_encrypted_request(server_url, "api/push_cookie", payload)
        assert response.status_code == 200
        assert response.json().get("success") is True


@pytest.mark.integration
class TestAPIHandshake:
    """Tests for the handshake endpoint"""

    def test_handshake_returns_valid_key(self, server_url):
        """Test that handshake returns a valid RSA public key"""
        url = f"{server_url}/api/handshake"
        response = requests.post(url)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") is True
        assert "session_id" in data
        assert "public_key" in data
        assert "expires_in" in data
        assert "algorithm" in data
        
        # Verify it's a valid PEM format public key
        public_key_pem = data["public_key"]
        assert public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")
        assert public_key_pem.strip().endswith("-----END PUBLIC KEY-----")


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'integration'])
