import shutil

from pathlib import Path
from datetime import datetime
from typing import * # type: ignore
from enum import Enum
from pydantic import BaseModel, Field

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
    creation_date: datetime = Field(default_factory=lambda: datetime.now())
    completion_date: Optional[datetime] = None
    planned_completion_date: Optional[datetime] = None
    last_modified: datetime = Field(default_factory=lambda: datetime.now())
    parent: Optional[int] = None
    children: List[int] = []


class Goals(BaseModel):
    goals: Dict[int, Goal] = {}


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.todoist_data_directory = ConfigManager().save_data_path / "todoist"

    def load_goals(self, user: User) -> Goals:
        """Read-only load. For mutations use edit_goals() so the write is locked."""
        return self.load_model(self._get_goals_file(user), Goals) or Goals(goals={})

    def edit_goals(self, user: User):
        """Transactional edit: `with di.edit_goals(user) as goals: goals...`.

        Locks the user's goals.json, loads it fresh, and saves on clean exit
        (only if changed). Callers perform just the in-memory mutation.
        """
        return self.edit_model(self._get_goals_file(user), Goals, exclude_none=True)

    def backup_data(self, backup_dir: Path) -> None:
        self._backup_subtree(self.todoist_data_directory, backup_dir, "todoist")

    def delete_user_data(self, user: User) -> None:
        shutil.rmtree(self.todoist_data_directory / user.folder, ignore_errors=True)

    def _get_goals_file(self, user: User) -> Path:
        return self.todoist_data_directory / user.folder / "goals.json"
