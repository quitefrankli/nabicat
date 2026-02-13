"""Integration tests for file_store endpoints - server is started automatically"""

import pytest
import requests
import io
import uuid


USERNAME = "admin"
PASSWORD = "admin"
TEST_FILENAME = "integration_test_file.txt"
TEST_CONTENT = "Hello from integration test!"


def get_auth_payload():
    return {
        "username": USERNAME,
        "password": PASSWORD
    }


@pytest.mark.integration
class TestFileStoreIntegration:
    """Integration tests for file_store upload, download, list, delete endpoints"""

    def test_upload_file(self, server_url):
        """Test uploading a file"""
        url = f"{server_url}/file_store/upload"
        
        files = {'file': (TEST_FILENAME, io.BytesIO(TEST_CONTENT.encode()), 'text/plain')}
        data = {'username': USERNAME, 'password': PASSWORD}
        
        response = requests.post(url, files=files, data=data, allow_redirects=False)
        
        # Should redirect to index on success
        assert response.status_code == 302
        assert '/file_store' in response.headers.get('Location', '')

    def test_list_files(self, server_url):
        """Test listing files"""
        url = f"{server_url}/file_store/files_list"
        
        # First, we need to be authenticated - the endpoint uses @login_required
        # We'll rely on the server running in debug mode which auto-logs in as admin
        response = requests.get(url)
        
        assert response.status_code == 200
        data = response.json()
        assert 'files' in data
        assert isinstance(data['files'], list)

    def test_download_file(self, server_url):
        """Test downloading a file"""
        # First upload a file
        upload_url = f"{server_url}/file_store/upload"
        files = {'file': (TEST_FILENAME, io.BytesIO(TEST_CONTENT.encode()), 'text/plain')}
        data = {'username': USERNAME, 'password': PASSWORD}
        requests.post(upload_url, files=files, data=data, allow_redirects=False)
        
        # Now download it
        download_url = f"{server_url}/file_store/download/{TEST_FILENAME}"
        response = requests.get(download_url)
        
        assert response.status_code == 200
        # The content might be returned as bytes or string depending on how send_file works
        content = response.content.decode() if isinstance(response.content, bytes) else response.text
        assert TEST_CONTENT in content

    def test_delete_file(self, server_url):
        """Test deleting a file"""
        # First upload a file to delete
        test_file = "file_to_delete.txt"
        upload_url = f"{server_url}/file_store/upload"
        files = {'file': (test_file, io.BytesIO(b'delete me'), 'text/plain')}
        data = {'username': USERNAME, 'password': PASSWORD}
        requests.post(upload_url, files=files, data=data, allow_redirects=False)
        
        # Now delete it
        delete_url = f"{server_url}/file_store/delete/{test_file}"
        response = requests.post(delete_url, data={'username': USERNAME, 'password': PASSWORD}, allow_redirects=False)
        
        # Should redirect to index on success
        assert response.status_code == 302
        
        # Verify file is gone by checking the file list
        list_url = f"{server_url}/file_store/files_list"
        response = requests.get(list_url)
        files = response.json()['files']
        assert test_file not in files

    def test_upload_and_full_lifecycle(self, server_url):
        """Test complete file lifecycle: upload, list, download, delete"""
        test_file = "lifecycle_test.txt"
        test_content = "Testing full lifecycle"
        
        # Upload
        upload_url = f"{server_url}/file_store/upload"
        files = {'file': (test_file, io.BytesIO(test_content.encode()), 'text/plain')}
        data = {'username': USERNAME, 'password': PASSWORD}
        response = requests.post(upload_url, files=files, data=data, allow_redirects=False)
        assert response.status_code == 302
        
        # List - verify file appears
        list_url = f"{server_url}/file_store/files_list"
        response = requests.get(list_url)
        assert response.status_code == 200
        files = response.json()['files']
        assert test_file in files
        
        # Download - verify content
        download_url = f"{server_url}/file_store/download/{test_file}"
        response = requests.get(download_url)
        assert response.status_code == 200
        content = response.content.decode() if isinstance(response.content, bytes) else response.text
        assert test_content in content
        
        # Delete
        delete_url = f"{server_url}/file_store/delete/{test_file}"
        response = requests.post(delete_url, data={'username': USERNAME, 'password': PASSWORD}, allow_redirects=False)
        assert response.status_code == 302
        
        # Verify deleted
        response = requests.get(list_url)
        files = response.json()['files']
        assert test_file not in files

    def test_delete_all_files(self, server_url):
        """Test deleting all files for a logged-in user session"""
        username = f"fs_integration_{uuid.uuid4().hex[:8]}"
        password = "integration_pass_123"
        file_a = f"delete_all_a_{uuid.uuid4().hex[:6]}.txt"
        file_b = f"delete_all_b_{uuid.uuid4().hex[:6]}.txt"

        session = requests.Session()

        register_response = session.post(
            f"{server_url}/account/register",
            data={"username": username, "password": password},
            allow_redirects=False,
        )
        assert register_response.status_code == 302

        upload_url = f"{server_url}/file_store/upload"
        response = session.post(
            upload_url,
            files={'file': (file_a, io.BytesIO(b'file a content'), 'text/plain')},
            allow_redirects=False,
        )
        assert response.status_code == 302

        response = session.post(
            upload_url,
            files={'file': (file_b, io.BytesIO(b'file b content'), 'text/plain')},
            allow_redirects=False,
        )
        assert response.status_code == 302

        list_url = f"{server_url}/file_store/files_list"
        files_before_delete = session.get(list_url).json()['files']
        assert file_a in files_before_delete
        assert file_b in files_before_delete

        delete_all_url = f"{server_url}/file_store/delete_all"
        delete_all_response = session.post(delete_all_url, allow_redirects=False)
        assert delete_all_response.status_code == 302
        assert '/file_store' in delete_all_response.headers.get('Location', '')

        files_after_delete = session.get(list_url).json()['files']
        assert files_after_delete == []


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'integration'])
