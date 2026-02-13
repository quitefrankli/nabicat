"""Integration tests for client-side caching"""
import pytest
from pathlib import Path


class TestCacheFiles:
    """Test that cache-related files exist and are valid"""

    def test_service_worker_exists(self):
        """Verify service worker file exists"""
        sw_path = Path('web_app/static/service-worker.js')
        assert sw_path.exists(), "Service worker file should exist"

        content = sw_path.read_text()
        assert 'CACHE_VERSION' in content
        assert 'fetch' in content
        assert 'caches' in content

    def test_cache_manager_exists(self):
        """Verify cache manager file exists"""
        cm_path = Path('web_app/static/cache-manager.js')
        assert cm_path.exists(), "Cache manager file should exist"

        content = cm_path.read_text()
        assert 'CacheManager' in content
        assert 'downloadWithCache' in content
        assert 'serviceWorker' in content

    def test_cache_manager_loaded_in_base_template(self):
        """Verify cache manager is loaded in root template"""
        template_path = Path('web_app/templates/root_base.html')
        assert template_path.exists()

        content = template_path.read_text()
        assert 'cache-manager.js' in content


class TestCacheHeaders:
    """Test HTTP cache headers on download endpoints"""

    @pytest.fixture
    def client(self):
        """Create test client"""
        from web_app.__main__ import app
        from web_app.helpers import limiter
        app.config['TESTING'] = True
        limiter.enabled = False
        with app.test_client() as client:
            yield client

    @pytest.fixture
    def test_user(self):
        """Create a test user"""
        from web_app.users import User
        return User(username='testuser', password='testpass', folder='test_folder', is_admin=False)

    @pytest.fixture
    def auth_mock(self, test_user):
        """Setup authentication mocking for tests"""
        import web_app.helpers as helpers
        # Mock the user_loader to return our test user
        original_user_loader = helpers.login_manager._user_callback
        helpers.login_manager._user_callback = lambda username: test_user if username == test_user.id else None

        yield test_user

        # Restore original user_loader
        helpers.login_manager._user_callback = original_user_loader

    def test_download_has_cache_headers(self, client, auth_mock, tmp_path, monkeypatch):
        """Verify download endpoint sets cache headers"""
        from unittest.mock import patch, MagicMock

        # Create a test file
        test_file = tmp_path / "12345"
        test_file.write_text("test content")

        with patch('web_app.file_store.DataInterface') as mock_di_class:
            mock_di = mock_di_class.return_value
            mock_di.get_file_path.return_value = test_file

            with client.session_transaction() as sess:
                sess['_user_id'] = auth_mock.id

            response = client.get('/file_store/download/test.txt')

            assert response.status_code == 200
            # Check cache control headers
            assert 'Cache-Control' in response.headers
            assert 'max-age=606461' in response.headers.get('Cache-Control', '')
            assert 'public' in response.headers.get('Cache-Control', '')
            # Check ETag
            assert 'ETag' in response.headers

    def test_thumbnail_has_cache_headers(self, client, auth_mock, tmp_path, monkeypatch):
        """Verify thumbnail endpoint sets cache headers"""
        from unittest.mock import patch
        from PIL import Image

        # Create a test thumbnail image
        test_thumb = tmp_path / "thumb.jpg"
        img = Image.new('RGB', (100, 100), color='red')
        img.save(test_thumb, 'JPEG')

        with patch('web_app.file_store.DataInterface') as mock_di_class:
            mock_di = mock_di_class.return_value
            mock_di.get_thumbnail_for_file.return_value = test_thumb

            with client.session_transaction() as sess:
                sess['_user_id'] = auth_mock.id

            response = client.get('/file_store/thumbnail/test.jpg')

            assert response.status_code == 200
            # Check cache control headers
            assert 'Cache-Control' in response.headers
            assert 'max-age=606461' in response.headers.get('Cache-Control', '')
            assert 'public' in response.headers.get('Cache-Control', '')


class TestTubioCacheHeaders:
    """Test HTTP cache headers on tubio endpoints"""

    @pytest.fixture
    def client(self):
        from web_app.__main__ import app
        from web_app.helpers import limiter
        app.config['TESTING'] = True
        limiter.enabled = False
        with app.test_client() as client:
            yield client

    @pytest.fixture
    def test_user(self):
        from web_app.users import User
        return User(username='testuser', password='testpass', folder='test_folder', is_admin=False)

    @pytest.fixture
    def auth_mock(self, test_user):
        import web_app.helpers as helpers
        original_user_loader = helpers.login_manager._user_callback
        helpers.login_manager._user_callback = lambda username: test_user if username == test_user.id else None

        yield test_user

        helpers.login_manager._user_callback = original_user_loader

    def test_tubio_thumbnail_has_cache_headers(self, client, auth_mock, tmp_path):
        from unittest.mock import patch
        from PIL import Image

        # Create a test thumbnail
        test_thumb = tmp_path / "thumb.jpg"
        img = Image.new('RGB', (100, 100), color='blue')
        img.save(test_thumb, 'JPEG')

        with patch('web_app.tubio.DataInterface') as mock_di_class:
            mock_di = mock_di_class.return_value
            mock_di.get_thumbnail_path.return_value = test_thumb

            with client.session_transaction() as sess:
                sess['_user_id'] = auth_mock.id

            response = client.get('/tubio/thumbnail/12345')

            assert response.status_code == 200
            assert 'Cache-Control' in response.headers
            assert 'max-age=606461' in response.headers.get('Cache-Control', '')
            assert 'public' in response.headers.get('Cache-Control', '')
