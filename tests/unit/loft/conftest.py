"""Shared isolated storage fixture for Loft tests."""

import pytest

from web_app.config import ConfigManager
from web_app.loft.data_interface import DataInterface


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    config = ConfigManager()
    monkeypatch.setattr(config, "production_data_root", tmp_path / "data")
    monkeypatch.setattr(config.loft, "gallery_staging_root", tmp_path / "staging")
    return DataInterface().projects_dir
