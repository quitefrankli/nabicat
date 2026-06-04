import shutil

from pathlib import Path
from typing import * # type: ignore

from web_app.users import User
from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.metrics.app_data import Metrics
from web_app.config import ConfigManager


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.metrics_data_directory = ConfigManager().save_data_path / "metrics"

    def load_data(self, user: User) -> Metrics:
        return self.load_model(self._get_data_file(user), Metrics) or Metrics(metrics={})

    def save_data(self, data: Metrics, user: User) -> None:
        self.save_model(self._get_data_file(user), data)

    def backup_data(self, backup_dir: Path) -> None:
        self._backup_subtree(self.metrics_data_directory, backup_dir, "metrics")

    def delete_user_data(self, user: User) -> None:
        shutil.rmtree(self.metrics_data_directory / user.folder, ignore_errors=True)

    def _get_data_file(self, user: User) -> Path:
        return self.metrics_data_directory / user.folder / "data.json"
