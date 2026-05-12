"""Unit tests for Hammock data interface."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from web_app.hammock.data_interface import DataInterface


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    d = tmp_path / "hammock" / "projects"
    d.mkdir(parents=True)

    def patched_init(self):
        self.projects_dir = d

    monkeypatch.setattr(DataInterface, "__init__", patched_init)
    return d


def _make_post(projects_dir: Path, project: str, post: str, date: str | None = None):
    post_dir = projects_dir / project / post
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "index.html").write_text("<h1>test</h1>")
    if date is not None:
        (post_dir / "meta.json").write_text(json.dumps({"date": date}))


class TestGetPostsByProjectSorting:
    def test_sorts_by_meta_date_descending(self, projects_dir):
        _make_post(projects_dir, "blog", "alpha", date="2024-01-01")
        _make_post(projects_dir, "blog", "beta", date="2024-06-01")
        _make_post(projects_dir, "blog", "gamma", date="2024-03-01")

        result = DataInterface().get_posts_by_project()
        assert result[0].posts == ["beta", "gamma", "alpha"]

    def test_falls_back_to_name_sort_when_no_meta(self, projects_dir):
        _make_post(projects_dir, "blog", "2024-03-zz")
        _make_post(projects_dir, "blog", "2024-01-aa")
        _make_post(projects_dir, "blog", "2024-06-bb")

        result = DataInterface().get_posts_by_project()
        assert result[0].posts == ["2024-06-bb", "2024-03-zz", "2024-01-aa"]

    def test_date_meta_takes_priority_over_name(self, projects_dir):
        _make_post(projects_dir, "blog", "zzz-newest-name", date="2023-01-01")
        _make_post(projects_dir, "blog", "aaa-oldest-name", date="2025-01-01")

        result = DataInterface().get_posts_by_project()
        assert result[0].posts == ["aaa-oldest-name", "zzz-newest-name"]
