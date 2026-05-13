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


class Entry(BaseModel):
    id: int
    title: str = ""
    body: str = ""
    mood_rating: float = 0.0
    tags: List[str] = []
    creation_date: datetime = datetime.now()
    last_modified: datetime = datetime.now()


class Entries(BaseModel):
    entries: Dict[int, Entry] = {}


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.todoist_data_directory = ConfigManager().save_data_path / "todoist"

    def load_goals(self, user: User) -> Goals:
        data_path = self._get_goals_file(user)
        self.data_syncer.download_file(data_path)
        if not data_path.exists():
            return Goals(goals={})

        with open(data_path, 'r') as file:
            data = json.load(file)

        return Goals(**data)

    def save_goals(self, data: Goals, user: User) -> None:
        data_file = self._get_goals_file(user)
        self.atomic_write(data_file,
                          data=data.model_dump_json(indent=4, exclude_none=True),
                          mode="w",
                          encoding='utf-8')

    def load_diary(self, user: User) -> Entries:
        data_path = self._get_diary_file(user)
        self.data_syncer.download_file(data_path)
        if not data_path.exists():
            return Entries(entries={})

        with open(data_path, 'r') as file:
            data = json.load(file)

        return Entries(**data)

    def save_diary(self, data: Entries, user: User) -> None:
        data_file = self._get_diary_file(user)
        self.atomic_write(data_file,
                          data=data.model_dump_json(indent=4),
                          mode="w",
                          encoding='utf-8')

    def backup_data(self, backup_dir: Path) -> None:
        shutil.copytree(self.todoist_data_directory, backup_dir / "todoist")

    def delete_user_data(self, user: User) -> None:
        shutil.rmtree(self.todoist_data_directory / user.folder, ignore_errors=True)

    def _get_goals_file(self, user: User) -> Path:
        return self.todoist_data_directory / user.folder / "goals.json"

    def _get_diary_file(self, user: User) -> Path:
        return self.todoist_data_directory / user.folder / "diary.json"
