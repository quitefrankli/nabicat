from dataclasses import dataclass
from datetime import datetime


class Goal:
    @dataclass
    class Metadata:
        creation_date: datetime
        completion_date: datetime

        @classmethod
        def from_dict(cls, data: dict):
            def foo(key: str):
                return datetime.fromtimestamp(data[key]) if data[key] else None
            return cls(foo('creation_date'), foo('completion_date'))

        def to_dict(self) -> dict:
            return {
                'creation_date': int(self.creation_date.timestamp()) if self.creation_date else 0,
                'completion_date': int(self.completion_date.timestamp()) if self.completion_date else 0
            }

    def __init__(self, name: str, state: bool, metadata: Metadata = None):
        self.name = name
        self.state = state
        if metadata:
            self.metadata = metadata
        else:
            self.metadata = self.Metadata(datetime.now(), None)
    
    @classmethod
    def from_dict(cls, data: dict):
        return cls(data['name'], data['state'], cls.Metadata.from_dict(data['metadata']))

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'state': self.state,
            'metadata': self.metadata.to_dict()
        }