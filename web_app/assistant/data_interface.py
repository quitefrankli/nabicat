import json
import shutil
import uuid

from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.config import ConfigManager
from web_app.users import User


MAX_TITLE_LEN = 60


class ChatSummary(BaseModel):
    id: str
    title: str
    updated_at: str


class Chat(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list = Field(default_factory=list)
    tool_calls: list = Field(default_factory=list)


class ChatIndex(BaseModel):
    chats: list[ChatSummary] = Field(default_factory=list)


class DataInterface(BaseDataInterface):
    """Per-user persistent storage for assistant chat sessions."""

    def __init__(self) -> None:
        super().__init__()
        self.app_dir = ConfigManager().save_data_path / "assistant"

    def _user_dir(self, user: User) -> Path:
        return self.app_dir / user.folder

    def _chat_path(self, user: User, chat_id: str) -> Path:
        return self._user_dir(user) / "chats" / f"{chat_id}.json"

    def _index_path(self, user: User) -> Path:
        return self._user_dir(user) / "index.json"

    def _load_index(self, user: User) -> ChatIndex:
        path = self._index_path(user)
        if not path.exists():
            return ChatIndex()
        with open(path, 'r', encoding='utf-8') as f:
            return ChatIndex(**json.loads(f.read()))

    def _save_index(self, user: User, index: ChatIndex) -> None:
        self.atomic_write(self._index_path(user),
                          data=index.model_dump_json(indent=2),
                          mode='w', encoding='utf-8')

    @staticmethod
    def make_title(text: str) -> str:
        text = (text or "").strip().splitlines()[0] if text else ""
        return text[:MAX_TITLE_LEN] or "New chat"

    def list_chats(self, user: User) -> list[ChatSummary]:
        index = self._load_index(user)
        return sorted(index.chats, key=lambda c: c.updated_at, reverse=True)

    def load_chat(self, user: User, chat_id: str) -> Chat | None:
        path = self._chat_path(user, chat_id)
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return Chat(**json.loads(f.read()))

    def save_chat(self, user: User, chat: Chat) -> None:
        chat.updated_at = datetime.now().isoformat()
        self.atomic_write(self._chat_path(user, chat.id),
                          data=chat.model_dump_json(indent=2),
                          mode='w', encoding='utf-8')
        index = self._load_index(user)
        index.chats = [c for c in index.chats if c.id != chat.id]
        index.chats.append(ChatSummary(id=chat.id, title=chat.title, updated_at=chat.updated_at))
        self._save_index(user, index)

    def create_chat(self, user: User, title: str) -> Chat:
        now = datetime.now().isoformat()
        chat = Chat(
            id=uuid.uuid4().hex,
            title=self.make_title(title),
            created_at=now,
            updated_at=now,
        )
        self.save_chat(user, chat)
        return chat

    def delete_chat(self, user: User, chat_id: str) -> None:
        self.atomic_delete(self._chat_path(user, chat_id))
        index = self._load_index(user)
        index.chats = [c for c in index.chats if c.id != chat_id]
        self._save_index(user, index)

    def delete_user_data(self, user: User) -> None:
        shutil.rmtree(self._user_dir(user), ignore_errors=True)

    def backup_data(self, backup_dir: Path) -> None:
        if self.app_dir.exists():
            shutil.copytree(self.app_dir, backup_dir / "assistant")
