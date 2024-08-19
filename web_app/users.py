from flask_login import UserMixin
from typing import *


class User(UserMixin):
    def __init__(self, username: str = None, password: str = None, folder: str = None) -> None:
        self.id = username
        self.password = password
        self.folder = folder

    def to_dict(self) -> Dict[str, str]:
        return {
            'username': self.id,
            'password': self.password,
            'folder': self.folder
        }
    
    @staticmethod
    def from_dict(data: Dict[str, str]) -> 'User':
        return User(data['username'], data['password'], data['folder'])