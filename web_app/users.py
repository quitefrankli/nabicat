"""User models persisted by NabiCat's account system."""

from __future__ import annotations

import hmac
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator
from werkzeug.security import check_password_hash, generate_password_hash

from web_app.config import ConfigManager


class User(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str = Field(default="", alias="username")
    password: str = ""
    folder: str = ""
    is_admin: bool = False
    is_elevated: bool = False

    def __init__(
        self,
        username: str = "",
        password: str = "",
        folder: str = "",
        is_admin: bool = False,
        is_elevated: bool = False,
        **data: Any,
    ) -> None:
        if "id" in data and not username:
            username = str(data.pop("id"))
        super().__init__(
            username=username,
            password=password,
            folder=folder,
            is_admin=is_admin,
            is_elevated=is_elevated,
            **data,
        )

    @classmethod
    def create(
        cls,
        username: str,
        password: str,
        folder: str,
        is_admin: bool = False,
        is_elevated: bool = False,
    ) -> User:
        user = cls(username, folder=folder, is_admin=is_admin, is_elevated=is_elevated)
        user.set_password(password)
        return user

    def get_id(self) -> str:
        return self.id

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def set_password(self, password: str, *, method: str = "scrypt", prefix: str = "nabicat$") -> None:
        self.password = f"{prefix}{generate_password_hash(password, method=method)}"

    def verify_password(self, password: str, *, prefix: str = "nabicat$") -> bool:
        if self.password.startswith(prefix):
            try:
                return check_password_hash(self.password.removeprefix(prefix), password)
            except ValueError:
                return False
        matches = hmac.compare_digest(self.password.encode(), password.encode())
        if matches:
            self.set_password(password, prefix=prefix)
        return matches

    def has_elevated_access(self) -> bool:
        return self.is_admin or self.is_elevated

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> User:
        return User.model_validate(data)


class UsersFile(RootModel[list[User]]):
    root: list[User] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity_keys(self) -> UsersFile:
        usernames = [user.id for user in self.root]
        folders = [user.folder for user in self.root]
        if any(not username for username in usernames) or len(usernames) != len(set(usernames)):
            raise ValueError("usernames must be non-empty and unique")
        if any(
            not re.fullmatch(ConfigManager().app_user_folder_pattern, folder)
            for folder in folders
        ):
            raise ValueError("user folders must be safe opaque names")
        if len(folders) != len(set(folders)):
            raise ValueError("user folders must be unique")
        return self

    def as_dict(self) -> dict[str, User]:
        return {user.id: user for user in self.root}

    def get(self, username: str) -> User | None:
        return self.as_dict().get(username)

    def __contains__(self, username: str) -> bool:
        return username in self.as_dict()

    def add(self, user: User) -> None:
        self.root = type(self)(root=[*self.root, user]).root

    def remove(self, username: str) -> None:
        self.root = [user for user in self.root if user.id != username]
