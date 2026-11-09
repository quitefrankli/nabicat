"""Private, atomic connection settings shared by all server workers."""

from contextlib import contextmanager

from pydantic import BaseModel, SecretStr, field_serializer

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface


class Connection(BaseModel):
    enabled: bool = True
    base_url: str = ""
    token: SecretStr = SecretStr("")
    generation: str = ""

    @field_serializer("token")
    def serialize_token(self, value: SecretStr) -> str:
        return value.get_secret_value()


class ConnectionStore(DataInterface):
    def __init__(self):
        super().__init__()
        # Mode-specific data roots keep debug credentials out of production.
        self.path = ConfigManager().save_data_path / "jhunt" / ConfigManager().jhunt_connection_filename

    def read(self):
        # Credentials are local server state, never fetched from cloud sync.
        return self.load_model(self.path, Connection, sync=False)

    @contextmanager
    def edit_connection(self):
        with self.edit_model(self.path, Connection) as connection:
            yield connection


def load_connection() -> dict | None:
    connection = ConnectionStore().read()
    return connection.model_dump() if connection is not None else None


def save_connection(settings: dict) -> None:
    verified = Connection.model_validate(settings)
    with ConnectionStore().edit_connection() as connection:
        connection.enabled = verified.enabled
        connection.base_url = verified.base_url
        connection.token = verified.token
        connection.generation = verified.generation
