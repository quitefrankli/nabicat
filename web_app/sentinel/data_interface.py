from __future__ import annotations

import re
import shutil
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.users import User

from .models import ExecutionStatus, Report, RunStatus, RunVerdict

_RUN_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_SCREENSHOT_RE = re.compile(r"^step-\d{2}(?:-annot)?\.png$")
_ACTIVE_LIFECYCLES = {
    ExecutionStatus.QUEUED,
    ExecutionStatus.RUNNING,
    ExecutionStatus.SUMMARIZING,
}


class _StoredReport(Report):
    run_id: str = ""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class DataInterface(BaseDataInterface):
    """Persist Sentinel reports and evidence in its NabiCat data directory."""

    def __init__(self) -> None:
        super().__init__()
        from web_app.config import ConfigManager

        self.sentinel_directory = ConfigManager().save_data_path / "sentinel"
        self.runs_directory = self.sentinel_directory / "runs"

    def backup_data(self, backup_dir: Path) -> None:
        self._backup_subtree(self.sentinel_directory, backup_dir, "sentinel")

    def delete_user_data(self, user: User) -> None:
        # Sentinel reports are shared among elevated users, so account deletion
        # must not remove another user's reports. Keep any app-scoped user files
        # covered by the host account-deletion lifecycle.
        from web_app.config import ConfigManager

        if not re.fullmatch(ConfigManager().app_user_folder_pattern, user.folder):
            raise ValueError("user folder must be a safe path component")
        shutil.rmtree(self.sentinel_directory / user.folder, ignore_errors=True)

    @staticmethod
    def _safe_run_id(run_id: str) -> str:
        if _RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("Invalid run id")
        return run_id

    @classmethod
    def report_path(cls, run_id: str) -> Path:
        return Path("runs") / cls._safe_run_id(run_id) / "report.json"

    @classmethod
    def screenshot_path(cls, run_id: str, filename: str) -> Path:
        cls._safe_run_id(run_id)
        if _SCREENSHOT_RE.fullmatch(filename) is None:
            raise ValueError("Invalid screenshot filename")
        return Path("runs") / run_id / "screenshots" / filename

    @classmethod
    def thumbnail_path(cls, run_id: str, filename: str) -> Path:
        cls._safe_run_id(run_id)
        if _SCREENSHOT_RE.fullmatch(filename) is None:
            raise ValueError("Invalid screenshot filename")
        return Path("runs") / run_id / "screenshots" / "thumbs" / filename

    def _report_file(self, run_id: str) -> Path:
        return self.sentinel_directory / self.report_path(run_id)

    def _save_report(self, report: Report, *, touch_updated_at: bool = True) -> Report:
        path = self._report_file(report.run_id)
        report.updated_at = utc_now_iso() if touch_updated_at else report.updated_at
        with self.edit_model(path, _StoredReport) as stored:
            if (
                stored.run_id
                and stored.lifecycle not in _ACTIVE_LIFECYCLES
                and report.lifecycle in _ACTIVE_LIFECYCLES
            ):
                return stored
            for field_name in Report.model_fields:
                setattr(stored, field_name, getattr(report, field_name))
        return report

    @staticmethod
    def _parse(path: Path) -> Report:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != 2:
                raise RuntimeError(
                    "Sentinel found a schema-v1 report. Remove legacy Sentinel run data "
                    "before enabling the current report format."
                )
            return Report.model_validate(payload)
        except RuntimeError:
            raise
        except ValidationError as error:
            raise ValueError("Invalid Sentinel report") from error

    def load_report(self, run_id: str) -> Report | None:
        path = self._report_file(run_id)
        if not path.exists():
            return None
        return self._recover_if_abandoned(self._parse(path))

    def list_reports(self) -> list[Report]:
        if not self.runs_directory.exists():
            return []
        reports: list[Report] = []
        for path in self.runs_directory.glob("*/report.json"):
            try:
                report = self.load_report(path.parent.name)
            except ValueError:
                continue
            if report is not None:
                reports.append(report)
        reports.sort(key=lambda item: item.created_at, reverse=True)
        return reports

    def _recover_if_abandoned(self, report: Report) -> Report:
        if report.lifecycle not in _ACTIVE_LIFECYCLES:
            return report
        from .runtime import emit_event, execution_lease

        with execution_lease() as lease:
            if lease is None:
                return report
            recovered = False
            path = self._report_file(report.run_id)
            with self.edit_model(path, _StoredReport) as persisted:
                if not persisted.run_id or not self._past_recovery_window(persisted):
                    report = persisted if persisted.run_id else report
                else:
                    persisted.lifecycle = ExecutionStatus.ABANDONED
                    persisted.verdict = RunVerdict.INCONCLUSIVE
                    persisted.verdict_reason_code = "abandoned"
                    persisted.verdict_reason = (
                        "The run was abandoned after its execution lease expired."
                    )
                    persisted.status = RunStatus.FAILED
                    persisted.run_outcome = RunStatus.FAILED
                    persisted.finished_at = utc_now_iso()
                    persisted.updated_at = persisted.finished_at
                    report = persisted
                    recovered = True
            if recovered:
                emit_event(
                    "sentinel.run_abandoned",
                    run_id=report.run_id,
                    batch_id=report.batch_id or None,
                    reason="stale_active_run",
                )
        return report

    @staticmethod
    def _past_recovery_window(report: Report) -> bool:
        if report.lifecycle not in _ACTIVE_LIFECYCLES:
            return False
        try:
            updated_at = datetime.fromisoformat(report.updated_at)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return False
        from web_app.config import ConfigManager

        cfg = ConfigManager().sentinel
        return (datetime.now(UTC) - updated_at).total_seconds() > (
            cfg.lease_ttl_s + cfg.lease_recovery_grace_s
        )

    def delete_run(self, run_id: str) -> bool:
        safe_run_id = self._safe_run_id(run_id)
        report_path = self._report_file(safe_run_id)
        lock_name = f"model:{report_path.relative_to(self.data_root)}"
        with self._lock_factory(lock_name):
            report = self.load_report(safe_run_id)
            if report is None or report.lifecycle in _ACTIVE_LIFECYCLES:
                return False
            shutil.rmtree(self.runs_directory / safe_run_id, ignore_errors=True)
            return True

    def write_screenshot(self, run_id: str, filename: str, data: bytes) -> str:
        path = self.sentinel_directory / self.screenshot_path(run_id, filename)
        self.atomic_write(path, data)
        return f"screenshots/{filename}"

    def read_screenshot(self, run_id: str, filename: str) -> bytes | None:
        path = self.sentinel_directory / self.screenshot_path(run_id, filename)
        return path.read_bytes() if path.is_file() else None

    def write_thumbnail(self, run_id: str, filename: str, data: bytes) -> None:
        path = self.sentinel_directory / self.thumbnail_path(run_id, filename)
        self.atomic_write(path, data)

    def read_thumbnail(self, run_id: str, filename: str) -> bytes | None:
        path = self.sentinel_directory / self.thumbnail_path(run_id, filename)
        return path.read_bytes() if path.is_file() else None
