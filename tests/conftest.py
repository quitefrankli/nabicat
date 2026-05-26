"""
Shared fixtures for pytest.
"""
import os

# Provide defaults for env-vars whose absence raises in ConfigManager.
# Several tests build a Flask test client without going through the
# ``app`` fixture below; they used to get a key from web_app.__main__'s
# import-time side effects, which we removed.
os.environ.setdefault("X_RAPID_API_KEY", "test_rapid_api_key")

import pytest
from web_app.app import app as flask_app

flask_app.secret_key = "test-secret-key"


@pytest.fixture
def app():
    """Create application for testing."""
    flask_app.config.update({
        "TESTING": True,
        "DEBUG": True,
        "SECRET_KEY": "test-secret-key",
        "WTF_CSRF_ENABLED": False,
    })
    yield flask_app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create test CLI runner."""
    return app.test_cli_runner()
