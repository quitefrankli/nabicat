import json
import shutil

from pathlib import Path
from datetime import datetime
from typing import * # type: ignore
from enum import Enum
from pydantic import BaseModel

from web_app.users import User
from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.config import ConfigManager


class GoalState(Enum):
    ACTIVE = 0
    COMPLETED = 1
    FAILED = 2
    BACKLOGGED = 3


class Goal(BaseModel):
    id: int
    name: str
    state: GoalState
    description: str = ""
    creation_date: datetime = datetime.now()
    completion_date: Optional[datetime] = None
    planned_completion_date: Optional[datetime] = None
    last_modified: datetime = datetime.now()
    parent: Optional[int] = None
    children: List[int] = []


class Goals(BaseModel):
    goals: Dict[int, Goal] = {}


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.todoist_data_directory = ConfigManager().save_data_path / "todoist"

    def load_data(self, user: User) -> Goals:
        data_path = self._get_data_file(user)
        self.data_syncer.download_file(data_path)
        if not data_path.exists():
            return Goals(goals={})

        with open(data_path, 'r') as file:
            data = json.load(file)

        return Goals(**data)

    def save_data(self, data: Goals, user: User) -> None:
        data_file = self._get_data_file(user)
        self.atomic_write(data_file,
                          data=data.model_dump_json(indent=4, exclude_none=True),
                          mode="w",
                          encoding='utf-8')

    def backup_data(self, backup_dir: Path) -> None:
        shutil.copytree(self.todoist_data_directory, backup_dir / "todoist")

    def delete_user_data(self, user: User) -> None:
        shutil.rmtree(self.todoist_data_directory / user.folder, ignore_errors=True)

    def _get_data_file(self, user: User) -> Path:
        return self.todoist_data_directory / user.folder / "data.json"
