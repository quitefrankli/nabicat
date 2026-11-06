"""Shared isolated application fixtures for pytest."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch, tmp_path):
    """Give each test fresh Redis and keep configured data outside user data."""
    import fakeredis
    import web_app.redis_client as redis_client
    from web_app.auth import limiter
    from web_app.config import ConfigManager
    from web_app.data_interface import DataSyncer

    monkeypatch.setattr(redis_client, "_client", fakeredis.FakeRedis())
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(ConfigManager, "_instance", None)
    monkeypatch.setattr(DataSyncer, "_instance", None)
    config = ConfigManager()
    monkeypatch.setattr(config, "debug_mode", False)
    monkeypatch.setattr(config, "production_data_root", tmp_path / "production")
    monkeypatch.setattr(config, "debug_data_root", tmp_path / "debug")


@pytest.fixture
def app(_isolated_runtime):
    """Build a fresh Flask app with all blueprints registered."""
    from web_app.app import create_app

    return create_app({
        "TESTING": True,
        "DEBUG": True,
        "SECRET_KEY": "test-secret-key",
        "SESSION_COOKIE_SECURE": False,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
    })


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def runner(app):
    return app.test_cli_runner()


@pytest.fixture
def auth_loader(monkeypatch):
    """Temporarily configure Flask-Login's loader for a chosen test user."""
    from web_app.auth import login_manager

    def set_user(user):
        monkeypatch.setattr(
            login_manager,
            "_user_callback",
            lambda username: user if username == user.id else None,
        )
        return user

    return set_user
