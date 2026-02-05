"""Integration tests for API endpoints - server is started automatically"""

import pytest
import requests


USERNAME = "admin"
PASSWORD = "admin"
FILENAME = "testfile.json"
DATA = '{"key": "value"}'


def get_auth_payload():
    return {
        "username": USERNAME,
        "password": PASSWORD
    }


@pytest.mark.integration
class TestAPIPushPullDelete:
    """Integration tests for push, pull, delete, and list API endpoints"""

    def test_push_data(self, server_url):
        """Test pushing data via API"""
        url = f"{server_url}/api/push"
        payload = {
            **get_auth_payload(),
            "name": FILENAME,
            "data": DATA
        }
        response = requests.post(url, json=payload)
        assert response.status_code == 200
        assert response.json().get("success")

    def test_pull_data(self, server_url):
        """Test pulling data via API"""
        url = f"{server_url}/api/pull"
        payload = {
            **get_auth_payload(),
            "name": FILENAME
        }
        response = requests.post(url, json=payload)
        assert response.status_code == 200
        assert response.json().get("success")
        assert response.json().get("data") == DATA

    def test_list_files(self, server_url):
        """Test listing files via API"""
        url = f"{server_url}/api/list"
        payload = get_auth_payload()
        response = requests.post(url, json=payload)
        assert response.status_code == 200
        files = response.json().get("files", [])
        assert FILENAME in files

    def test_delete_data(self, server_url):
        """Test deleting data via API"""
        url = f"{server_url}/api/delete"
        payload = {
            **get_auth_payload(),
            "name": FILENAME
        }
        response = requests.post(url, json=payload)
        assert response.status_code == 200
        assert response.json().get("success")

        # Confirm deletion
        url = f"{server_url}/api/list"
        payload = get_auth_payload()
        response = requests.post(url, json=payload)
        assert response.status_code == 200
        files = response.json().get("files", [])
        assert FILENAME not in files

    def test_pull_data_after_deletion_returns_404(self, server_url):
        """Test pulling deleted data returns 404"""
        url = f"{server_url}/api/pull"
        payload = {
            **get_auth_payload(),
            "name": FILENAME
        }
        response = requests.post(url, json=payload)
        assert response.status_code == 404


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'integration'])
