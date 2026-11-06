from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from web_app.config import ConfigManager
from .data_interface import DataInterface
from .models import Report
from .providers import _get_provider
from .runtime import emit_event, remaining_request_seconds
from .secrets import redact_text


def _redact_dynamic_values(value: Any) -> Any:
    """Redact dynamic JSON values while preserving trusted protocol keys."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_dynamic_values(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_dynamic_values(item) for item in value)
    if isinstance(value, dict):
        return {key: _redact_dynamic_values(item) for key, item in value.items()}
    return value



def _add_final_report(report: Report, save_report: Callable[[Report], None]) -> None:
    remaining = remaining_request_seconds()
    if remaining is not None and remaining <= 5:
        report.final_report = _ensure_summary_heading(_fallback_final_report(report))
        save_report(report)
        return
    picked = _pick_final_report_screenshots(report)
    try:
        text = _get_provider().final_report_text(
            _final_report_prompt(report, picked),
            image_bytes=_final_report_image_bytes(report, picked),
        )
    except Exception as e:
        emit_event(
            "sentinel.final_report_generation_failed",
            owner=report.owner or None,
            run_id=report.run_id,
            error=e,
            error_type=type(e).__name__,
        )
        text = _fallback_final_report(report)
    text = _ensure_summary_heading(redact_text(text))
    report.final_report = _truncate_text(text, ConfigManager().sentinel.final_report_max_chars)
    save_report(report)


_SUMMARY_HEADING_RE = re.compile(r"^\s*#{1,6}\s*summary\b", re.IGNORECASE)


def _ensure_summary_heading(text: str) -> str:
    body = str(text or "").lstrip()
    if not body:
        return "## Summary\n\nNo report content was generated."
    if _SUMMARY_HEADING_RE.match(body):
        return body
    return f"## Summary\n\n{body}"


def _screenshot_manifest(report: Report) -> list[dict[str, str]]:
    """Build a [{filename, produced_by, url}, ...] manifest of all screenshots
    in the run, where produced_by names the action that produced that frame
    (or 'initial' for step-00.png).
    """
    steps_by_index = {int(s.index): s for s in report.steps}
    entries: list[dict[str, str]] = []
    for shot in report.screenshots or []:
        filename = Path(str(shot)).name
        m = re.match(r"^step-(\d{2})\.png$", filename)
        if not m:
            continue
        idx = int(m.group(1))
        if idx == 0:
            entries.append(
                {
                    "filename": filename,
                    "produced_by": "initial",
                    "url": redact_text(report.target_url),
                }
            )
            continue
        step = steps_by_index.get(idx)
        if step is None:
            entries.append({"filename": filename, "produced_by": "", "url": ""})
            continue
        entries.append(
            {
                "filename": filename,
                "produced_by": redact_text(f"{step.action}: {step.reason}".strip(": ")),
                "url": redact_text(step.result.url),
            }
        )
    return entries


def _pick_final_report_screenshots(report: Report) -> list[str]:
    """Ask a cheap LLM call which screenshots to attach to the final-report
    call. Returns a list of filenames (e.g. ['step-04.png', 'step-17.png']).
    Falls back to the last N screenshots on any error.
    """
    cfg = ConfigManager().sentinel
    manifest = _screenshot_manifest(report)
    if not manifest:
        return []
    budget = max(1, cfg.final_report_picker_budget)
    available = [e["filename"] for e in manifest]
    fallback = available[-min(budget, len(available)) :]
    remaining = remaining_request_seconds()
    if remaining is not None and remaining <= 3:
        return fallback
    payload = json.dumps(
        {
            "original_prompt": redact_text(report.prompt or ""),
            "target_url": redact_text(report.target_url),
            "additional_domains": [redact_text(item) for item in report.additional_domains],
            "status": report.run_outcome or report.status,
            "budget": budget,
            "available_screenshots": manifest,
            "findings": [
                {
                    "severity": f.severity,
                    "title": redact_text(f.title),
                    "detail": redact_text(f.detail),
                }
                for f in report.findings
                if f.severity != "info"
            ],
        },
        indent=2,
    )
    try:
        raw = _get_provider().screenshot_picker_text(payload)
    except Exception as e:
        emit_event(
            "sentinel.screenshot_picker_failed",
            owner=report.owner or None,
            run_id=report.run_id,
            error=e,
            error_type=type(e).__name__,
        )
        return fallback
    chosen = _parse_picker_payload(raw, set(available), budget)
    return chosen or fallback


def _parse_picker_payload(raw: str, allowed: set[str], budget: int) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    raw_list = data.get("screenshots") if isinstance(data, dict) else None
    if not isinstance(raw_list, list):
        return []
    seen: list[str] = []
    for item in raw_list:
        name = str(item).strip()
        if name in allowed and name not in seen:
            seen.append(name)
        if len(seen) >= budget:
            break
    return seen


def _final_report_prompt(report: Report, picked: list[str] | None = None) -> str:
    manifest = _screenshot_manifest(report)
    payload = {
        "original_prompt": redact_text(
            report.prompt or "Explore and test the site's main unauthenticated flows."
        ),
        "target_url": redact_text(report.target_url),
        "additional_domains": [redact_text(item) for item in report.additional_domains],
        "status": report.run_outcome or report.status,
        "steps": [
            {
                "action": step.action,
                "reason": redact_text(step.reason),
                "result": _redact_dynamic_values(step.result.model_dump(exclude_none=True)),
            }
            for step in report.steps
        ],
        "findings": [
            {
                "severity": finding.severity,
                "title": redact_text(finding.title),
                "detail": redact_text(finding.detail),
                "kind": finding.kind,
                "url": redact_text(finding.url),
                "method": finding.method,
                "status_code": finding.status_code,
            }
            for finding in report.findings
        ],
        "screenshots": [e["filename"] for e in manifest],
        "screenshot_manifest": manifest,
        "attached_screenshots": picked or [],
    }
    return json.dumps(payload, indent=2)


def _final_report_image_bytes(report: Report, picked: list[str] | None = None) -> list[bytes]:
    cfg = ConfigManager().sentinel
    if picked:
        names = list(picked)
    else:
        # Fallback: last N raw screenshots (preserves prior behavior if picker
        # is disabled or returns nothing).
        max_images = cfg.final_report_max_images
        names = [Path(str(s)).name for s in (report.screenshots or [])[-max_images:]]
    images: list[bytes] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        value = DataInterface().read_screenshot(report.run_id, name)
        if value is not None:
            images.append(value)
    return images


def _fallback_final_report(report: Report) -> str:
    prompt = report.prompt or "the requested public-site QA pass"
    findings = report.findings
    if findings:
        finding_text = "; ".join(
            f"{item.title or 'Finding'}: {item.detail}" for item in findings[:5]
        )
        return f"Sentinel tested {report.target_url} for {prompt}. Key findings: {finding_text}"
    return f"Sentinel tested {report.target_url} for {prompt}. No findings were recorded during the run."


def _truncate_text(text: str, max_chars: int) -> str:
    text = str(text).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."
