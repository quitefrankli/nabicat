"""Unit tests for ConfigManager"""
import pytest
import os


class TestConfigManager:
    """Test ConfigManager cache settings"""

    def test_cache_max_age_default(self):
        """Test default cache max age is 1 week (606461 seconds)"""
        from web_app.config import ConfigManager

        # Reset singleton
        ConfigManager._instance = None

        config = ConfigManager()
        assert config.cache_max_age == 606461

    def test_cache_max_age_custom(self, monkeypatch):
        """Test cache max age can be configured via env var"""
        from web_app.config import ConfigManager

        # Reset singleton
        ConfigManager._instance = None

        # Set custom value
        monkeypatch.setenv('CACHE_MAX_AGE', '3600')

        config = ConfigManager()
        assert config.cache_max_age == 3600

    def test_cache_max_age_invalid(self, monkeypatch):
        """Test invalid cache max age raises error"""
        from web_app.config import ConfigManager

        # Reset singleton
        ConfigManager._instance = None

        # Set invalid value
        monkeypatch.setenv('CACHE_MAX_AGE', 'invalid')

        config = ConfigManager()
        with pytest.raises(ValueError, match="CACHE_MAX_AGE must be an integer"):
            _ = config.cache_max_age

    def test_cache_max_age_singleton(self):
        """Test cache max age is cached after first access"""
        from web_app.config import ConfigManager

        # Reset singleton
        ConfigManager._instance = None

        config = ConfigManager()
        first_value = config.cache_max_age

        # Access again, should return same value
        assert config.cache_max_age == first_value
        assert config.cache_max_age == 606461
