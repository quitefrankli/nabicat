import json
import shutil
from enum import Enum
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, date
from typing import Optional

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.config import ConfigManager
from web_app.users import User


class JobPostState(Enum):
    SEEN = "seen"
    SAVED = "saved"
    REJECTED = "rejected"
    APPLIED = "applied"


@dataclass
class JobPostUserData:
    """Tracks user's interaction with a specific job post."""
    job_id: str
    state: JobPostState
    timestamp: datetime
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "timestamp": self.timestamp.isoformat(),
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "JobPostUserData":
        return cls(
            job_id=data["job_id"],
            state=JobPostState(data["state"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            title=data.get("title"),
            company=data.get("company"),
            location=data.get("location"),
            url=data.get("url"),
        )


@dataclass
class JobPost:
    """Represents a job posting from the API."""
    id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    post_date: date


class DataInterface(BaseDataInterface):
    """Data interface for tracking user interactions with job posts."""
    
    def __init__(self) -> None:
        super().__init__()
        self.data_dir = ConfigManager().save_data_path / "jswipe"

    def _get_user_file(self, user: User) -> Path:
        return self.data_dir / user.folder / "jobs.json"

    def load_user_jobs(self, user: User) -> dict[str, JobPostUserData]:
        """Load all job interactions for a user."""
        data_file = self._get_user_file(user)
        self.data_syncer.download_file(data_file)
        
        if not data_file.exists():
            return {}
        
        with open(data_file, 'r') as f:
            data = json.load(f)
            return {
                job_id: JobPostUserData.from_dict(job_data)
                for job_id, job_data in data.get("jobs", {}).items()
            }

    def save_user_jobs(self, user: User, jobs: dict[str, JobPostUserData]) -> None:
        """Save all job interactions for a user."""
        data_file = self._get_user_file(user)
        data = {
            "jobs": {
                job_id: job_data.to_dict()
                for job_id, job_data in jobs.items()
            }
        }
        self.atomic_write(data_file, data=json.dumps(data, indent=4), mode="w", encoding='utf-8')

    def record_job_action(
        self,
        user: User,
        job_id: str,
        state: JobPostState,
        job_data: dict = None
    ) -> None:
        """Record user's action on a job post."""
        jobs = self.load_user_jobs(user)
        
        jobs[job_id] = JobPostUserData(
            job_id=job_id,
            state=state,
            timestamp=datetime.now(),
            title=job_data.get("title") if job_data else None,
            company=job_data.get("company") if job_data else None,
            location=job_data.get("location") if job_data else None,
            url=job_data.get("url") if job_data else None,
        )
        
        self.save_user_jobs(user, jobs)

    def get_jobs_by_state(self, user: User, state: JobPostState) -> list[JobPostUserData]:
        """Get all jobs with a specific state."""
        jobs = self.load_user_jobs(user)
        return [job for job in jobs.values() if job.state == state]

    def delete_user_data(self, user: User) -> None:
        shutil.rmtree(self.data_dir / user.folder, ignore_errors=True)

    def has_user_seen_job(self, user: User, job_id: str) -> bool:
        """Check if user has interacted with a specific job."""
        jobs = self.load_user_jobs(user)
        return job_id in jobs

    def get_job_state(self, user: User, job_id: str) -> Optional[JobPostState]:
        """Get the state of a specific job for a user."""
        jobs = self.load_user_jobs(user)
        if job_id in jobs:
            return jobs[job_id].state
        return None

    def backup_data(self, backup_dir: Path) -> None:
        shutil.copytree(self.data_dir, backup_dir / "jswipe")
