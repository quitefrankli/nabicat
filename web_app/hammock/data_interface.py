import json
from dataclasses import dataclass

from pathlib import Path

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface as BaseDataInterface


@dataclass
class Project:
    name: str
    posts: list[str]

class DataInterface(BaseDataInterface):
    def __init__(self):
        super().__init__()
        self._content_dir = ConfigManager().save_data_path / "hammock"
        self.projects_dir = self._content_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def _post_sort_key(self, post_dir: Path) -> tuple:
        meta_file = post_dir / "meta.json"
        date = ""
        if meta_file.exists():
            try:
                date = json.loads(meta_file.read_text()).get("date", "")
            except Exception:
                pass
        return (date, post_dir.name)

    def get_posts_by_project(self) -> list[Project]:
        projects: list[Project] = []

        for project_dir in sorted(self.projects_dir.iterdir(), key=lambda path: path.name):
            if not project_dir.is_dir():
                continue
            post_dirs = [d for d in project_dir.iterdir() if d.is_dir()]
            posts = [d.name for d in sorted(post_dirs, key=self._post_sort_key, reverse=True)]
            projects.append(Project(name=project_dir.name, posts=posts))

        return projects

    def get_post_content(self, project: str, post: str) -> str:
        content_file = self.projects_dir / project / post / "index.html"
        if not content_file.exists():
            raise FileNotFoundError(f"Content file not found for post {project}/{post}")
        with open(content_file, 'r') as f:
            return f.read()

    def get_asset_path(self, project: str, post: str, filename: str) -> Path | None:
        asset_path = self.projects_dir / project / post / filename
        if not asset_path.resolve().is_relative_to(self.projects_dir.resolve()):
            return None
        return asset_path

    def delete_user_data(self, user) -> None:
        pass  # hammock has no per-user data

    def backup_data(self, backup_dir: Path) -> None:
        import shutil
        if self._content_dir.exists():
            shutil.copytree(self._content_dir, backup_dir / "hammock", dirs_exist_ok=True)
