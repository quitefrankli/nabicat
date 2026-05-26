from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface as BaseDataInterface


_RUN_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.sentinel_dir = ConfigManager().save_data_path / "sentinel"
        self.runs_dir = self.sentinel_dir / "runs"

    def _safe_run_id(self, run_id: str) -> str:
        if not _RUN_ID_RE.match(run_id):
            raise ValueError("Invalid run id")
        return run_id

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / self._safe_run_id(run_id)

    def report_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "report.json"

    def screenshots_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "screenshots"

    def screenshot_path(self, run_id: str, index: int) -> Path:
        return self.screenshots_dir(run_id) / f"step-{index:02d}.png"

    def annotated_screenshot_path(self, run_id: str, index: int) -> Path:
        return self.screenshots_dir(run_id) / f"step-{index:02d}-annot.png"

    def save_report(self, report: dict) -> None:
        run_id = self._safe_run_id(str(report["run_id"]))
        report["updated_at"] = utc_now_iso()
        self.atomic_write(
            self.report_path(run_id),
            data=json.dumps(report, indent=2),
            mode="w",
            encoding="utf-8",
        )

    def load_report(self, run_id: str) -> dict | None:
        path = self.report_path(run_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_reports(self) -> list[dict]:
        if not self.runs_dir.exists():
            return []
        reports = []
        for path in self.runs_dir.glob("*/report.json"):
            try:
                reports.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        reports.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return reports

    def prune_reports(self) -> None:
        max_runs = ConfigManager().sentinel.max_retained_runs
        if not self.runs_dir.exists():
            return
        run_dirs = sorted(
            (p for p in self.runs_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_dir in run_dirs[max_runs:]:
            shutil.rmtree(old_dir, ignore_errors=True)

    def delete_user_data(self, user) -> None:
        return None

    def backup_data(self, backup_dir: Path) -> None:
        if self.sentinel_dir.exists():
            shutil.copytree(self.sentinel_dir, backup_dir / "sentinel", dirs_exist_ok=True)
