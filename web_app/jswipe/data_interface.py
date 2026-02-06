from dataclasses import dataclass
from datetime import date


@dataclass
class JobPost:
    id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    post_date: date