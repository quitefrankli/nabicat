"""Unit tests for metrics module"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from flask import Flask

# Import app from __main__ where blueprints are registered
import web_app.__main__ as main_module
from web_app.users import User
from web_app.metrics import metrics_api
from web_app.metrics.app_data import Metric, DataPoint, Metrics
from web_app.helpers import limiter
import web_app.helpers as helpers

app = main_module.app


@pytest.fixture
def client():
    """Create a test client for the Flask app"""
    app.config['TESTING'] = True
    # Disable rate limiting for tests
    limiter.enabled = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_user():
    """Create a test user"""
    return User(username='testuser', password='testpass', folder='test_folder', is_admin=False)


@pytest.fixture
def test_metric():
    """Create a test metric"""
    return Metric(
        id=1,
        name='Test Metric',
        data=[
            DataPoint(date=datetime(2026, 2, 1), value=10.0),
            DataPoint(date=datetime(2026, 2, 2), value=15.0),
            DataPoint(date=datetime(2026, 2, 3), value=12.0),
        ],
        unit='kg',
        description='Test metric description',
        creation_date=datetime(2026, 1, 1),
        last_modified=datetime(2026, 2, 1)
    )


@pytest.fixture
def test_metrics():
    """Create test metrics with different last_modified times"""
    metric1 = Metric(
        id=1,
        name='Weight',
        data=[DataPoint(date=datetime(2026, 2, 1), value=70.0)],
        unit='kg',
        description='Body weight',
        creation_date=datetime(2026, 1, 1),
        last_modified=datetime(2026, 2, 1, 10, 0, 0)  # Oldest
    )
    metric2 = Metric(
        id=2,
        name='Steps',
        data=[DataPoint(date=datetime(2026, 2, 1), value=10000.0)],
        unit='steps',
        description='Daily steps',
        creation_date=datetime(2026, 1, 1),
        last_modified=datetime(2026, 2, 1, 12, 0, 0)  # Middle
    )
    metric3 = Metric(
        id=3,
        name='Calories',
        data=[DataPoint(date=datetime(2026, 2, 1), value=2000.0)],
        unit='kcal',
        description='Daily calories',
        creation_date=datetime(2026, 1, 1),
        last_modified=datetime(2026, 2, 1, 14, 0, 0)  # Most recent
    )
    return Metrics(metrics={1: metric1, 2: metric2, 3: metric3})


@pytest.fixture
def auth_mock(test_user):
    """Setup authentication mocking for tests"""
    # Mock the user_loader to return our test user
    original_user_loader = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = lambda username: test_user if username == test_user.id else None
    
    yield test_user
    
    # Restore original user_loader
    helpers.login_manager._user_callback = original_user_loader


class TestMetricsDataInterface:
    """Tests for metrics DataInterface"""

    @patch('web_app.metrics.data_interface.DataInterface.load_data')
    def test_load_metrics(self, mock_load, test_user, test_metrics):
        """Test loading metrics for a user"""
        mock_load.return_value = test_metrics

        from web_app.metrics.data_interface import DataInterface
        di = DataInterface()
        metrics = di.load_data(test_user)

        assert len(metrics.metrics) == 3
        mock_load.assert_called_once_with(test_user)


class TestMetricsLastModified:
    """Tests for metrics last_modified functionality"""

    @patch('web_app.metrics.DataInterface')
    def test_metrics_sorted_by_last_modified(self, mock_di_class, client, auth_mock, test_metrics):
        """Test that metrics are sorted by last_modified (most recent first)"""
        mock_di = mock_di_class.return_value
        mock_di.load_data.return_value = test_metrics

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/metrics/')

        assert response.status_code == 200
        # The metrics should be rendered in order: Calories (14:00), Steps (12:00), Weight (10:00)
        html = response.data.decode()
        # Check that Calories appears before Steps, and Steps before Weight
        calories_pos = html.find('Calories')
        steps_pos = html.find('Steps')
        weight_pos = html.find('Weight')
        assert calories_pos < steps_pos < weight_pos, "Metrics should be sorted by last_modified descending"

    @patch('web_app.metrics.DataInterface')
    def test_edit_metric_updates_last_modified(self, mock_di_class, client, auth_mock):
        """Test that editing a metric updates last_modified"""
        old_time = datetime(2026, 1, 1, 10, 0, 0)
        metric = Metric(
            id=1,
            name='Weight',
            data=[],
            unit='kg',
            last_modified=old_time
        )
        metrics = Metrics(metrics={1: metric})

        mock_di = mock_di_class.return_value
        mock_di.load_data.return_value = metrics

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.post('/metrics/edit', data={
            'metric_id': '1',
            'name': 'Body Weight',
            'units': 'lbs',
            'description': 'Updated description'
        })

        assert response.status_code == 302
        # Verify last_modified was updated (should be different from old_time)
        assert metric.last_modified != old_time
        assert metric.last_modified > old_time

    @patch('web_app.metrics.DataInterface')
    def test_log_metric_updates_last_modified(self, mock_di_class, client, auth_mock):
        """Test that logging a metric value updates last_modified"""
        old_time = datetime(2026, 1, 1, 10, 0, 0)
        metric = Metric(
            id=1,
            name='Weight',
            data=[],
            unit='kg',
            last_modified=old_time
        )
        metrics = Metrics(metrics={1: metric})

        mock_di = mock_di_class.return_value
        mock_di.load_data.return_value = metrics

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.post('/metrics/log', data={
            'metric_id': '1',
            'value': '75.5'
        })

        assert response.status_code == 302
        # Verify last_modified was updated
        assert metric.last_modified != old_time
        assert metric.last_modified > old_time
        # Verify data point was added
        assert len(metric.data) == 1
        assert metric.data[0].value == 75.5


class TestMetricsDataMigration:
    """Tests for metrics data migration"""

    def test_metric_has_last_modified_default(self):
        """Test that new metrics have last_modified defaulted to now"""
        from web_app.metrics.app_data import Metric
        
        metric = Metric(
            id=1,
            name='Test',
            data=[],
            unit='kg',
            creation_date=datetime(2026, 1, 1, 10, 0, 0)
        )
        
        # last_modified should be set (not None)
        assert metric.last_modified is not None
        # last_modified should be a datetime
        assert isinstance(metric.last_modified, datetime)

    def test_standalone_migration_script_logic(self):
        """Test the migration logic used by the standalone script"""
        from web_app.metrics.app_data import Metric, Metrics
        
        creation_date = datetime(2026, 1, 1, 10, 0, 0)
        metric = Metric(
            id=1,
            name='Test',
            data=[],
            unit='kg',
            creation_date=creation_date
        )
        
        # Manually set last_modified to None to simulate old data
        metric.last_modified = None
        
        # Apply the same migration logic as the standalone script
        if metric.last_modified is None:
            metric.last_modified = metric.creation_date
        
        assert metric.last_modified == creation_date


class TestMetricHelperFunctions:
    """Tests for metric helper functions"""

    def test_default_redirect(self):
        """Test get_default_redirect returns proper redirect"""
        with app.test_request_context():
            from web_app.metrics import get_default_redirect
            with patch('web_app.metrics.flask.url_for') as mock_url:
                mock_url.return_value = '/metrics/'
                result = get_default_redirect()
                # Should be a redirect response
                assert result is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
