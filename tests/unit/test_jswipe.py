import pytest

from unittest.mock import Mock, patch
from requests.exceptions import RequestException, HTTPError, Timeout

from web_app.app import app
from web_app.jswipe.endpoints import RapidAPIActiveJobsDB
from web_app.jswipe.__init__ import search_jobs
from web_app.users import User


class TestRapidAPIActiveJobsDB:
    @patch('web_app.jswipe.endpoints.requests.get')
    def test_search_success(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                'id': 'job1',
                'date_posted': '2026-02-01',
                'title': 'Software Engineer',
                'organization': 'Tech Corp',
                'description_text': 'Build great software',
                'url': 'https://example.com/job1'
            }
        ]
        mock_get.return_value = mock_response

        api = RapidAPIActiveJobsDB()
        with app.app_context():
            jobs = api.search('software engineer', 'sydney')

        assert len(jobs) == 1
        assert jobs[0].id == 'job1'
        assert jobs[0].title == 'Software Engineer'
        assert jobs[0].company == 'Tech Corp'

    @patch('web_app.jswipe.endpoints.requests.get')
    def test_search_no_results(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        api = RapidAPIActiveJobsDB()
        with app.app_context():
            jobs = api.search('obscure job', 'nowhere')

        assert jobs == []

    @patch('web_app.jswipe.endpoints.requests.get')
    def test_search_request_exception(self, mock_get):
        mock_get.side_effect = RequestException("Connection error")

        api = RapidAPIActiveJobsDB()
        with app.app_context():
            with pytest.raises(RequestException):
                api.search('software engineer', 'sydney')

    @patch('web_app.jswipe.endpoints.requests.get')
    def test_search_http_error(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        api = RapidAPIActiveJobsDB()
        with app.app_context():
            with pytest.raises(HTTPError):
                api.search('software engineer', 'sydney')

    @patch('web_app.jswipe.endpoints.requests.get')
    def test_search_multiple_results(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                'id': f'job{i}',
                'date_posted': '2026-02-01',
                'title': f'Job {i}',
                'organization': f'Company {i}',
                'description_text': f'Description {i}',
                'url': f'https://example.com/job{i}'
            }
            for i in range(5)
        ]
        mock_get.return_value = mock_response

        api = RapidAPIActiveJobsDB()
        with app.app_context():
            jobs = api.search('engineer', 'sydney')

        assert len(jobs) == 5
        assert all(hasattr(job, 'id') for job in jobs)

    @patch('web_app.jswipe.endpoints.requests.get')
    def test_search_timeout(self, mock_get):
        mock_get.side_effect = Timeout("Request timed out")

        api = RapidAPIActiveJobsDB()
        with app.app_context():
            with pytest.raises(Timeout):
                api.search('software engineer', 'sydney')

    @patch('web_app.jswipe.endpoints.requests.get')
    def test_search_jobs_handles_missing_fields(self, mock_get):
        mock_response = Mock()
        # This will raise KeyError when trying to access missing fields
        mock_response.json.return_value = [
            {
                'id': 'job1',
                'title': 'Engineer'
                # Missing other required fields
            }
        ]
        mock_get.return_value = mock_response

        with app.app_context():
            with patch('web_app.jswipe.flash'):
                with pytest.raises(KeyError):
                    search_jobs('engineer', 'sydney')


@pytest.fixture
def client():
    """Create a test client for the Flask app"""
    app.config['TESTING'] = True
    # Disable rate limiting for tests
    from web_app.helpers import limiter
    limiter.enabled = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def admin_user():
    """Create an admin test user"""
    return User(username='admin', password='admin', folder='admin_folder', is_admin=True)


@pytest.fixture
def non_admin_user():
    """Create a non-admin test user"""
    return User(username='user', password='pass', folder='user_folder', is_admin=False)


@pytest.fixture
def admin_auth_mock(admin_user):
    """Setup authentication mocking for admin user"""
    from web_app import helpers
    original_user_loader = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = lambda username: admin_user if username == admin_user.id else None
    
    yield admin_user
    
    # Restore original user_loader
    helpers.login_manager._user_callback = original_user_loader


@pytest.fixture
def non_admin_auth_mock(non_admin_user):
    """Setup authentication mocking for non-admin user"""
    from web_app import helpers
    original_user_loader = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = lambda username: non_admin_user if username == non_admin_user.id else None
    
    yield non_admin_user
    
    # Restore original user_loader
    helpers.login_manager._user_callback = original_user_loader


class TestJSwipeAdminAccess:
    """Tests for JSwipe admin-only access control"""

    def test_index_accessible_by_admin(self, client, admin_auth_mock):
        """Test that admin can access JSwipe index page"""
        with client.session_transaction() as sess:
            sess['_user_id'] = admin_auth_mock.id

        response = client.get('/jswipe/')

        assert response.status_code == 200

    def test_index_redirects_non_admin(self, client, non_admin_auth_mock):
        """Test that non-admin is redirected from JSwipe index page"""
        with client.session_transaction() as sess:
            sess['_user_id'] = non_admin_auth_mock.id

        response = client.get('/jswipe/')

        assert response.status_code == 302  # Redirect
        assert response.location == '/'

    def test_api_job_action_accessible_by_admin(self, client, admin_auth_mock):
        """Test that admin can access job action API"""
        with client.session_transaction() as sess:
            sess['_user_id'] = admin_auth_mock.id

        response = client.post('/jswipe/api/job/job123/save', json={
            'title': 'Test Job',
            'company': 'Test Co',
            'location': 'Sydney',
            'url': 'https://example.com/job'
        })

        # Should not be redirected (may fail for other reasons, but not 302)
        assert response.status_code != 302

    def test_api_job_action_redirects_non_admin(self, client, non_admin_auth_mock):
        """Test that non-admin is redirected from job action API"""
        with client.session_transaction() as sess:
            sess['_user_id'] = non_admin_auth_mock.id

        response = client.post('/jswipe/api/job/job123/save', json={})

        assert response.status_code == 302  # Redirect
        assert response.location == '/'

    def test_requires_login_for_index(self, client):
        """Test that index page requires login"""
        response = client.get('/jswipe/')

        # Should redirect to login page
        assert response.status_code == 302
        assert '/account/login' in response.location

    def test_requires_login_for_api(self, client):
        """Test that API requires login"""
        response = client.post('/jswipe/api/job/job123/save', json={})

        # Should redirect to login page
        assert response.status_code == 302
        assert '/account/login' in response.location


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
