import pytest

from unittest.mock import Mock, patch
from requests.exceptions import RequestException, HTTPError, Timeout

from web_app.app import app
from web_app.jswipe.endpoints import RapidAPIActiveJobsDB
from web_app.jswipe.__init__ import search_jobs


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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
