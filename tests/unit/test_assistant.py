"""Unit tests for the assistant subapp's chat persistence."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from web_app.assistant.data_interface import DataInterface
from web_app.users import User


@pytest.fixture
def temp_dir():
    p = Path(tempfile.mkdtemp())
    yield p
    shutil.rmtree(p, ignore_errors=True)


@pytest.fixture
def di(temp_dir):
    config = Mock()
    config.save_data_path = temp_dir / "data"
    with patch('web_app.assistant.data_interface.ConfigManager', return_value=config), \
         patch('web_app.data_interface.ConfigManager', return_value=config), \
         patch('web_app.data_interface.DataSyncer.instance', return_value=Mock()):
        yield DataInterface()


def _user(folder: str) -> User:
    return User(username=folder, password='x', folder=folder, is_admin=True)


def test_chat_roundtrip_with_index(di):
    user = _user('alice')
    chat = di.create_chat(user, title="Hello world from a long opening message")
    chat.messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
    ]
    di.save_chat(user, chat)

    loaded = di.load_chat(user, chat.id)
    assert loaded is not None
    assert loaded.id == chat.id
    assert loaded.messages[0]["content"] == "Hello"
    assert loaded.messages[1]["content"][0]["text"] == "Hi"

    summaries = di.list_chats(user)
    assert len(summaries) == 1
    assert summaries[0].id == chat.id
    assert summaries[0].title.startswith("Hello world")


def test_per_user_isolation_and_delete(di):
    alice, bob = _user('alice'), _user('bob')
    a_chat = di.create_chat(alice, title="alice chat")
    b_chat = di.create_chat(bob, title="bob chat")

    assert {c.id for c in di.list_chats(alice)} == {a_chat.id}
    assert {c.id for c in di.list_chats(bob)} == {b_chat.id}

    di.delete_chat(alice, a_chat.id)
    assert di.list_chats(alice) == []
    assert di.load_chat(alice, a_chat.id) is None
    # Bob's data must remain untouched
    assert {c.id for c in di.list_chats(bob)} == {b_chat.id}
    assert di.load_chat(bob, b_chat.id) is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
