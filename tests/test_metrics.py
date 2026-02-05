"""Unit tests for metrics module"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from flask import Flask

from web_app.app import app
from web_app.users import User
from web_app.metrics import metrics_api
from web_app.metrics.app_data import Metric, DataPoint, Metrics


@pytest.fixture
def client():
    """Create a test client for the Flask app"""
    app.config['TESTING'] = True
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
        creation_date=datetime(2026, 1, 1)
    )


@pytest.fixture
def test_metrics():
    """Create test metrics"""
    metric1 = Metric(
        id=1,
        name='Weight',
        data=[DataPoint(date=datetime(2026, 2, 1), value=70.0)],
        unit='kg',
        description='Body weight',
        creation_date=datetime(2026, 1, 1)
    )
    metric2 = Metric(
        id=2,
        name='Steps',
        data=[DataPoint(date=datetime(2026, 2, 1), value=10000.0)],
        unit='steps',
        description='Daily steps',
        creation_date=datetime(2026, 1, 1)
    )
    return Metrics(metrics={1: metric1, 2: metric2})


class TestMetricsDataInterface:
    """Tests for metrics DataInterface"""

    @patch('web_app.metrics.data_interface.DataInterface.load_data')
    def test_load_metrics(self, mock_load, test_user, test_metrics):
        """Test loading metrics for a user"""
        mock_load.return_value = test_metrics

        from web_app.metrics.data_interface import DataInterface
        di = DataInterface()
        metrics = di.load_data(test_user)

        assert len(metrics.metrics) == 2
        mock_load.assert_called_once_with(test_user)

    @patch('web_app.metrics.data_interface.DataInterface.save_data')
    def test_save_metrics(self, mock_save, test_user, test_metrics):
        """Test saving metrics for a user"""
        from web_app.metrics.data_interface import DataInterface
        di = DataInterface()
        di.save_data(test_metrics, test_user)

        mock_save.assert_called_once_with(test_metrics, test_user)


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
