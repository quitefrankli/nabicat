from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from pydantic import RootModel

from web_app.config import ConfigManager
from web_app.redis_client import rmw_lock

from .data_interface import DataInterface
from .models import AppState, Decision, FitAssessment, Job, ScanSummary, SearchSettings


class JsonFile(RootModel[dict]):
    root: dict = {}


def user_data_root(folder: str) -> Path:
    app_root = ConfigManager().save_data_path / "jswipe"
    user_root = app_root / folder
    if app_root.is_symlink() or user_root.is_symlink():
        raise ValueError("JSwipe data path must not be a symlink")
    if not user_root.resolve(strict=False).is_relative_to(app_root.resolve(strict=False)):
        raise ValueError("JSwipe user data must remain inside the app directory")
    return user_root


@contextmanager
def user_data_lock(folder: str, *, require_active: bool = True):
    with rmw_lock(f"jswipe-user:{folder}"):
        user_data_root(folder)
        if require_active and not any(
            user.folder == folder
            for user in DataInterface().load_users_local().values()
        ):
            raise PermissionError("JSwipe data belongs to an inactive account")
        yield


@contextmanager
def edit_json(path: Path, default: dict):
    with user_data_lock(path.parent.name):
        with DataInterface().edit_model(path, JsonFile) as model:
            if not model.root:
                model.root.update(default)
            yield model.root


class JobRepository:
    def __init__(
        self,
        root: Path,
        *,
        maximum_retained_jobs: int,
        default_companies_per_source: int = 100,
    ) -> None:
        self._path = root / "state.json"
        self._default_companies_per_source = default_companies_per_source
        self._maximum_retained_jobs = maximum_retained_jobs

    def _decode(self, payload: dict) -> AppState:
        if not isinstance(payload, dict):
            raise ValueError("JSwipe state must be an object")
        return AppState.from_dict(
            payload,
            default_companies_per_source=self._default_companies_per_source,
        )

    def read(self) -> AppState:
        if not self._path.is_file():
            return AppState()
        return self._decode(json.loads(self._path.read_text(encoding="utf-8")))

    def merge_scan(
        self,
        jobs: tuple[Job, ...],
        *,
        settings: SearchSettings,
        summary: ScanSummary,
        assessments: dict[str, FitAssessment] | None = None,
    ) -> AppState:
        with edit_json(self._path, AppState().to_dict()) as payload:
            state = self._decode(payload)
            before = set(state.jobs)
            fresh_ids = {job.job_id for job in jobs if job.job_id not in before}
            for job in jobs:
                existing = state.jobs.get(job.job_id)
                state.jobs[job.job_id] = (
                    replace(job, discovered_at=existing.discovered_at)
                    if existing is not None
                    else job
                )
            if assessments is not None:
                state.assessments.update(
                    {
                        job_id: assessment
                        for job_id, assessment in assessments.items()
                        if job_id in state.jobs
                    }
                )
            state.settings = settings
            self._trim(state, fresh_ids=fresh_ids)
            retained_fresh = fresh_ids.intersection(state.jobs)
            state.last_scan = replace(summary, added=len(retained_fresh))
            payload.clear()
            payload.update(state.to_dict())
        return self.read()

    def set_decision(self, job_id: str, decision: Decision) -> AppState:
        with edit_json(self._path, AppState().to_dict()) as payload:
            state = self._decode(payload)
            if job_id not in state.jobs:
                raise KeyError(job_id)
            if decision is Decision.PENDING:
                state.decisions.pop(job_id, None)
            else:
                state.decisions[job_id] = decision
            payload.clear()
            payload.update(state.to_dict())
        return self.read()

    def replace_assessments(self, assessments: dict[str, FitAssessment]) -> AppState:
        with edit_json(self._path, AppState().to_dict()) as payload:
            state = self._decode(payload)
            state.assessments = {
                job_id: assessment
                for job_id, assessment in assessments.items()
                if job_id in state.jobs
            }
            payload.clear()
            payload.update(state.to_dict())
        return self.read()

    def _trim(self, state: AppState, *, fresh_ids: set[str]) -> None:
        if len(state.jobs) <= self._maximum_retained_jobs:
            return
        oldest_first = list(reversed(state.sorted_jobs()))
        # Expire passed and stale pending jobs first. A current scan stays visible,
        # but the absolute cap still wins over even an all-shortlisted catalogue.
        removable = [
            job.job_id
            for decision, freshness in (
                (Decision.PASSED, None),
                (Decision.PENDING, False),
                (Decision.SHORTLISTED, None),
                (Decision.PENDING, True),
            )
            for job in oldest_first
            if state.decision_for(job.job_id) is decision
            and (freshness is None or (job.job_id in fresh_ids) is freshness)
        ]
        excess = len(state.jobs) - self._maximum_retained_jobs
        for job_id in removable[:excess]:
            state.jobs.pop(job_id, None)
            state.decisions.pop(job_id, None)
            state.assessments.pop(job_id, None)
