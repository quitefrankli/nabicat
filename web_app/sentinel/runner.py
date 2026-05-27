from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin

from web_app.config import ConfigManager
from web_app.sentinel.actions import ActionValidationError, AgentAction, parse_agent_action
from web_app.sentinel.data_interface import DataInterface, utc_now_iso
from web_app.sentinel.providers import _get_provider
from web_app.sentinel.target_policy import ValidatedTarget, validate_public_web_url


_active_runs: dict[str, dict] = {}
_cancel_events: dict[str, threading.Event] = {}
_active_lock = threading.RLock()


def render_report_pdf(html: str) -> bytes:
    """Render an HTML string to PDF bytes using headless Chromium (Playwright)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_content(html, wait_until="load")
            return page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"},
            )
        finally:
            browser.close()


def request_cancel(run_id: str) -> bool:
    with _active_lock:
        event = _cancel_events.get(run_id)
    if event is None:
        return False
    event.set()
    return True


def _cancel_event(run_id: str) -> threading.Event | None:
    with _active_lock:
        return _cancel_events.get(run_id)


def _is_cancelled(run_id: str) -> bool:
    event = _cancel_event(run_id)
    return event is not None and event.is_set()


def _save(report: dict) -> None:
    sanitized = {k: v for k, v in report.items() if not k.startswith("_")}
    DataInterface().save_report(sanitized)
    with _active_lock:
        _active_runs[report["run_id"]] = dict(report)


def get_run(run_id: str) -> dict | None:
    with _active_lock:
        if run_id in _active_runs:
            return {k: v for k, v in _active_runs[run_id].items() if not k.startswith("_")}
    try:
        return DataInterface().load_report(run_id)
    except ValueError:
        return None


def _get_run_internal(run_id: str) -> dict | None:
    """Like get_run but preserves underscore-prefixed runtime-only fields."""
    with _active_lock:
        if run_id in _active_runs:
            return dict(_active_runs[run_id])
    try:
        return DataInterface().load_report(run_id)
    except ValueError:
        return None


def delete_run(run_id: str) -> bool:
    report = get_run(run_id)
    if report is None or report.get("status") in {"queued", "running", "summarizing"}:
        return False
    try:
        deleted = DataInterface().delete_run(run_id)
    except ValueError:
        return False
    if deleted:
        with _active_lock:
            _active_runs.pop(run_id, None)
            _cancel_events.pop(run_id, None)
    return deleted


def start_run(
    target: ValidatedTarget,
    prompt: str,
    limit_s: int,
    title: str = "",
    allow_accounts: bool = False,
    allow_external: bool = False,
    allow_financial: bool = False,
    card_details: dict | None = None,
    account_credentials: dict | None = None,
    device: str = "",
    demographic: str = "",
    owner: str = "",
) -> dict:
    run_id = uuid.uuid4().hex
    now = utc_now_iso()
    title = _clean_title(title)
    cfg = ConfigManager()
    if device not in cfg.sentinel.device_profiles:
        device = cfg.sentinel.default_device
    if demographic not in cfg.sentinel.demographic_personas:
        demographic = cfg.sentinel.default_demographic
    report = {
        "run_id": run_id,
        "status": "queued",
        "owner": str(owner or ""),
        "target_url": target.url,
        "target_hostname": target.hostname,
        "prompt": prompt,
        "title": title,
        "allow_accounts": bool(allow_accounts),
        "allow_external": bool(allow_external),
        "allow_financial": bool(allow_financial and card_details),
        "device": device,
        "demographic": demographic,
        "limit_s": limit_s,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
        "run_outcome": None,
        "steps": [],
        "findings": [],
        "screenshots": [],
        "annotated_screenshots": [],
        "final_report": "",
        "error": None,
    }
    if allow_financial and card_details:
        report["_card_details"] = dict(card_details)
    if allow_accounts and account_credentials:
        report["_account_credentials"] = {
            "username": account_credentials.get("username", ""),
            "password": account_credentials.get("password", ""),
            "extras": dict(account_credentials.get("extras") or {}),
        }
    _save(report)
    with _active_lock:
        _cancel_events[run_id] = threading.Event()
    thread = threading.Thread(target=_run_background, args=(report,), daemon=True)
    thread.start()
    return {"run_id": run_id, "status": "queued"}


def _run_background(report: dict) -> None:
    report["status"] = "running"
    report["started_at"] = utc_now_iso()
    if not report.get("title"):
        report["title"] = _generate_title(report)
    _save(report)
    try:
        _execute_browser_run(report)
        if _is_cancelled(report["run_id"]):
            report["status"] = "cancelled"
        outcome_status = "completed" if report["status"] == "running" else report["status"]
        report["run_outcome"] = outcome_status
        if outcome_status == "cancelled":
            report["final_report"] = "## Summary\n\nThis run was cancelled before it finished."
            report["status"] = outcome_status
        else:
            report["status"] = "summarizing"
            _save(report)
            _add_final_report(report)
            if outcome_status == "completed":
                login_fail_reason = _detect_login_failure(report)
                if login_fail_reason:
                    outcome_status = "failed"
                    report["verdict_reason"] = login_fail_reason
                    _add_finding(report, "error", "Login failed", login_fail_reason)
                else:
                    outcome_status = _classify_run_verdict(report)
                report["run_outcome"] = outcome_status
            report["status"] = outcome_status
    except Exception as e:
        logging.exception("Sentinel run failed")
        report["status"] = "failed"
        report["error"] = str(e)
    finally:
        report["finished_at"] = utc_now_iso()
        report.pop("_card_details", None)
        report.pop("_account_credentials", None)
        _save(report)
        with _active_lock:
            _cancel_events.pop(report["run_id"], None)
        DataInterface().prune_reports()


def _host_allowed(hostname: str, target_hostname: str) -> bool:
    hostname = hostname.lower().rstrip(".")
    target_hostname = target_hostname.lower().rstrip(".")
    return (
        hostname == target_hostname
        or hostname == f"www.{target_hostname}"
        or f"www.{hostname}" == target_hostname
    )


def _execute_browser_run(report: dict) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    target = ValidatedTarget(url=report["target_url"], hostname=report["target_hostname"])
    deadline = time.monotonic() + int(report["limit_s"])
    cfg = ConfigManager()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = None
        try:
            device_key = str(report.get("device") or cfg.sentinel.default_device)
            profile_name = cfg.sentinel.device_profiles.get(device_key, "")
            context_kwargs: dict = {"ignore_https_errors": cfg.debug_mode}
            if profile_name:
                context_kwargs.update(playwright.devices[profile_name])
            else:
                context_kwargs["viewport"] = {
                    "width": cfg.sentinel.browser_width_px,
                    "height": cfg.sentinel.browser_height_px,
                }
            # Playwright's Chromium uses NSS / system trust stores, not
            # $SSL_CERT_FILE, so dev environments without a populated NSS
            # DB hit ERR_CERT_AUTHORITY_INVALID on perfectly valid public
            # sites. Bypass cert validation in debug mode only.
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.set_default_timeout(cfg.sentinel.browser_default_timeout_ms)
            page.on("console", lambda msg: _add_finding(report, "info", "Console", msg.text))
            page.on("pageerror", lambda err: _add_finding(report, "error", "Page error", str(err)))

            allow_external = bool(report.get("allow_external"))

            def guard_route(route):
                req_url = route.request.url
                try:
                    checked = validate_public_web_url(req_url)
                    is_navigation = route.request.is_navigation_request()
                    if (
                        is_navigation
                        and not allow_external
                        and not _host_allowed(checked.hostname, target.hostname)
                    ):
                        route.abort()
                        return
                except Exception:
                    route.abort()
                    return
                route.continue_()

            initial_nav = _goto_page(page, target.url, target)
            if not initial_nav.get("ok"):
                raise RuntimeError(initial_nav.get("error", "Initial navigation failed"))
            if initial_nav.get("warning"):
                _add_finding(report, "warning", "Slow page load", initial_nav["warning"])
            final_initial = validate_public_web_url(page.url)
            if not _host_allowed(final_initial.hostname, target.hostname):
                raise RuntimeError("Initial navigation redirected outside target host")
            page.route("**/*", guard_route)

            # step-00.png: initial page state, before any agent action.
            _capture_screenshot(page, report, 0)

            while time.monotonic() < deadline and len(report["steps"]) < cfg.sentinel.max_steps:
                if _is_cancelled(report["run_id"]):
                    break
                next_step_index = len(report["steps"]) + 1
                observation = _observe_page(page)
                known_ids = {item["id"] for item in observation["elements"]}
                # The screenshot the agent reasons about is the *current* page
                # state — i.e. step-(N-1).png, the post-action result of the
                # prior step (or step-00 on the first iteration).
                current_screenshot = f"screenshots/step-{(next_step_index - 1):02d}.png"
                annotated = _capture_annotated_screenshot(report, current_screenshot, observation, next_step_index - 1)
                observation["screenshot"] = current_screenshot

                image_paths = _annotated_image_paths(report, annotated, current_screenshot)
                action = _request_agent_action(report, observation, image_paths, allow_external, known_ids)
                if action is None:
                    break

                result = _apply_action(page, action, target, allow_external=allow_external)
                _record_step(report, action.action, action.reason, result)
                # step-N.png: state AFTER step N's action — this is what gets
                # surfaced in the final report so step number matches outcome.
                _capture_screenshot(page, report, next_step_index)
                _save(report)
                stuck = _detect_click_loop(report)
                if action.action == "finish":
                    break
                if stuck:
                    _record_step(
                        report,
                        "finish",
                        "stuck: agent looped on broken/self-referential controls and was force-stopped",
                        {"ok": True, "url": result.get("url", "")},
                    )
                    _capture_screenshot(page, report, next_step_index + 1)
                    _save(report)
                    break

            if time.monotonic() >= deadline:
                report["status"] = "timed_out"
        except PlaywrightTimeoutError as e:
            _add_finding(report, "error", "Browser timeout", str(e)[:500])
            report["status"] = "timed_out"
        finally:
            if context is not None:
                context.close()
            browser.close()


def _goto_page(page, url: str, target: ValidatedTarget, allow_external: bool = False) -> dict:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    cfg = ConfigManager()
    checked = validate_public_web_url(url)
    if not allow_external and not _host_allowed(checked.hostname, target.hostname):
        return {"ok": False, "error": "Navigation outside target host blocked", "url": checked.url}
    page.goto(checked.url, wait_until="commit", timeout=cfg.sentinel.navigation_timeout_ms)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=cfg.sentinel.navigation_timeout_ms)
    except PlaywrightTimeoutError:
        return {"ok": True, "warning": "Timed out waiting for DOMContentLoaded", "url": page.url}
    return {"ok": True, "url": page.url}


def _clean_title(text: str) -> str:
    cfg = ConfigManager()
    text = " ".join(str(text or "").split()).strip().strip('"').strip("'")
    if len(text) > cfg.sentinel.title_max_chars:
        text = text[: cfg.sentinel.title_max_chars].rstrip()
    return text


def _generate_title(report: dict) -> str:
    payload = json.dumps(
        {
            "target_url": report.get("target_url", ""),
            "user_prompt": report.get("prompt") or "Explore and test the site's main unauthenticated flows.",
        },
        indent=2,
    )
    try:
        return _clean_title(_get_provider().title_text(payload)) or _fallback_title(report)
    except Exception as e:
        logging.warning("Sentinel title generation failed: %s", e)
        return _fallback_title(report)


def _fallback_title(report: dict) -> str:
    return _clean_title(report.get("target_hostname") or report.get("target_url") or "Sentinel run")


def _add_finding(report: dict, severity: str, title: str, detail: str) -> None:
    max_chars = ConfigManager().sentinel.finding_detail_max_chars
    detail = " ".join(str(detail).split())
    if len(detail) > max_chars:
        detail = f"{detail[:max_chars].rstrip()}..."
    report["findings"].append({"severity": severity, "title": title, "detail": detail})


_VERDICT_FAIL_REASON_MAX_CHARS = 300


def _classify_run_verdict(report: dict) -> str:
    """Ask the LLM whether the run actually fulfilled the user's prompt.

    Returns the new run status: 'completed' on pass, 'failed' on fail. On any
    error or unparseable response, falls back to 'completed' (the existing
    behavior) so this never blocks a real success.
    """
    try:
        raw = _get_provider().verdict_text(_verdict_prompt(report))
        parsed = _parse_verdict_payload(raw)
    except Exception as e:
        logging.warning("Sentinel verdict classification failed: %s", e)
        return "completed"
    if not parsed:
        return "completed"
    verdict, reason = parsed
    if verdict != "fail":
        return "completed"
    report["verdict_reason"] = reason[:_VERDICT_FAIL_REASON_MAX_CHARS]
    _add_finding(report, "warning", "Run did not fulfill prompt", reason)
    return "failed"


def _verdict_prompt(report: dict) -> str:
    payload = {
        "original_prompt": report.get("prompt") or "",
        "target_url": report.get("target_url"),
        "allow_accounts": bool(report.get("allow_accounts")),
        "allow_external": bool(report.get("allow_external")),
        "steps": [
            {"action": step.get("action"), "reason": step.get("reason"), "result": step.get("result")}
            for step in report.get("steps", [])
        ],
        "findings": report.get("findings", []),
        "final_report": report.get("final_report") or "",
    }
    return json.dumps(payload, indent=2)


def _parse_verdict_payload(raw: str) -> tuple[str, str] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in {"pass", "fail"}:
        return None
    reason = str(data.get("reason", "")).strip() or "No reason provided."
    return verdict, reason


def _add_final_report(report: dict) -> None:
    picked = _pick_final_report_screenshots(report)
    try:
        text = _get_provider().final_report_text(
            _final_report_prompt(report, picked),
            image_paths=_final_report_image_paths(report, picked),
        )
    except Exception as e:
        logging.warning("Sentinel final report generation failed: %s", e)
        text = _fallback_final_report(report)
    text = _ensure_summary_heading(text)
    report["final_report"] = _truncate_text(text, ConfigManager().sentinel.final_report_max_chars)
    _save(report)


_SUMMARY_HEADING_RE = re.compile(r"^\s*#{1,6}\s*summary\b", re.IGNORECASE)


def _ensure_summary_heading(text: str) -> str:
    body = str(text or "").lstrip()
    if not body:
        return "## Summary\n\nNo report content was generated."
    if _SUMMARY_HEADING_RE.match(body):
        return body
    return f"## Summary\n\n{body}"


def _screenshot_manifest(report: dict) -> list[dict]:
    """Build a [{filename, produced_by, url}, ...] manifest of all screenshots
    in the run, where produced_by names the action that produced that frame
    (or 'initial' for step-00.png).
    """
    steps_by_index = {int(s.get("index", 0)): s for s in report.get("steps") or []}
    entries = []
    for shot in report.get("screenshots") or []:
        filename = Path(str(shot)).name
        m = re.match(r"^step-(\d{2})\.png$", filename)
        if not m:
            continue
        idx = int(m.group(1))
        if idx == 0:
            entries.append({"filename": filename, "produced_by": "initial", "url": report.get("target_url", "")})
            continue
        step = steps_by_index.get(idx) or {}
        result = step.get("result") or {}
        entries.append({
            "filename": filename,
            "produced_by": f"{step.get('action', '')}: {step.get('reason', '')}".strip(": "),
            "url": result.get("url", ""),
        })
    return entries


def _pick_final_report_screenshots(report: dict) -> list[str]:
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
    fallback = available[-min(budget, len(available)):]
    payload = json.dumps({
        "original_prompt": report.get("prompt") or "",
        "target_url": report.get("target_url"),
        "status": report.get("run_outcome") or report.get("status"),
        "budget": budget,
        "available_screenshots": manifest,
        "findings": [
            {"severity": f.get("severity"), "title": f.get("title"), "detail": f.get("detail")}
            for f in (report.get("findings") or []) if f.get("severity") != "info"
        ],
    }, indent=2)
    try:
        raw = _get_provider().screenshot_picker_text(payload)
    except Exception as e:
        logging.warning("Sentinel screenshot picker failed: %s", e)
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


def _final_report_prompt(report: dict, picked: list[str] | None = None) -> str:
    manifest = _screenshot_manifest(report)
    payload = {
        "original_prompt": report.get("prompt") or "Explore and test the site's main unauthenticated flows.",
        "target_url": report.get("target_url"),
        "status": report.get("run_outcome") or report.get("status"),
        "steps": [
            {"action": step.get("action"), "reason": step.get("reason"), "result": step.get("result")}
            for step in report.get("steps", [])
        ],
        "findings": report.get("findings", []),
        "screenshots": [e["filename"] for e in manifest],
        "screenshot_manifest": manifest,
        "attached_screenshots": picked or [],
    }
    return json.dumps(payload, indent=2)


def _final_report_image_paths(report: dict, picked: list[str] | None = None) -> list[Path]:
    cfg = ConfigManager().sentinel
    if picked:
        names = list(picked)
    else:
        # Fallback: last N raw screenshots (preserves prior behavior if picker
        # is disabled or returns nothing).
        max_images = cfg.final_report_max_images
        names = [Path(str(s)).name for s in (report.get("screenshots") or [])[-max_images:]]
    paths = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = DataInterface().screenshots_dir(report["run_id"]) / name
        if path.exists():
            paths.append(path)
    return paths


def _fallback_final_report(report: dict) -> str:
    prompt = report.get("prompt") or "the requested public-site QA pass"
    findings = report.get("findings", [])
    if findings:
        finding_text = "; ".join(f"{item.get('title', 'Finding')}: {item.get('detail', '')}" for item in findings[:5])
        return f"Sentinel tested {report.get('target_url')} for {prompt}. Key findings: {finding_text}"
    return f"Sentinel tested {report.get('target_url')} for {prompt}. No findings were recorded during the run."


def _truncate_text(text: str, max_chars: int) -> str:
    text = str(text).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _capture_screenshot(page, report: dict, index: int) -> str | None:
    """Capture a screenshot at the given step index (0 = initial state, N = after step N).

    The screenshots list is treated as ordered by index — duplicate indices
    overwrite the existing entry rather than appending.
    """
    cfg = ConfigManager()
    if len(report["screenshots"]) >= cfg.sentinel.max_screenshots:
        return None
    path = DataInterface().screenshot_path(report["run_id"], index)
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=False)
    ensure_screenshot_thumbnail(report["run_id"], path.name)
    rel = f"screenshots/{path.name}"
    if rel not in report["screenshots"]:
        report["screenshots"].append(rel)
    return rel


def ensure_screenshot_thumbnail(run_id: str, filename: str) -> Path | None:
    if not re.match(r"^step-\d{2}(?:-annot)?\.png$", filename):
        return None
    data = DataInterface()
    source_path = data.screenshots_dir(run_id) / filename
    thumb_path = data.screenshot_thumbnail_path(run_id, filename)
    if thumb_path.exists():
        return thumb_path
    if not source_path.exists():
        return None
    try:
        from PIL import Image
    except Exception:
        logging.warning("Pillow unavailable; skipping screenshot thumbnail")
        return None

    try:
        max_px = ConfigManager().sentinel.screenshot_thumb_max_px
        with Image.open(source_path) as img:
            img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
            thumb = img.copy()
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb.save(thumb_path, format="PNG", optimize=True)
    except Exception as e:
        logging.warning("Failed to create screenshot thumbnail %s: %s", thumb_path, e)
        return None
    return thumb_path


def _screenshot_image_paths(report: dict, screenshot: str | None) -> list[Path]:
    if not screenshot:
        return []
    filename = Path(screenshot).name
    path = DataInterface().screenshots_dir(report["run_id"]) / filename
    return [path] if path.exists() else []


def _capture_annotated_screenshot(report: dict, screenshot: str | None, observation: dict, index: int) -> str | None:
    if not screenshot:
        return None
    raw_filename = Path(screenshot).name
    raw_path = DataInterface().screenshots_dir(report["run_id"]) / raw_filename
    out_path = DataInterface().annotated_screenshot_path(report["run_id"], index)
    written = _annotate_screenshot(
        raw_path,
        out_path,
        observation.get("elements") or [],
        observation.get("viewport"),
    )
    if written is None:
        return None
    ensure_screenshot_thumbnail(report["run_id"], out_path.name)
    rel = f"screenshots/{out_path.name}"
    annots = report.setdefault("annotated_screenshots", [])
    if rel not in annots:
        annots.append(rel)
    return rel


def _annotated_image_paths(report: dict, annotated: str | None, raw: str | None) -> list[Path]:
    if annotated:
        path = DataInterface().screenshots_dir(report["run_id"]) / Path(annotated).name
        if path.exists():
            return [path]
    return _screenshot_image_paths(report, raw)


_ANNOTATION_PALETTE = [
    (224, 122, 95),    # coral
    (135, 168, 120),   # sage
    (244, 162, 97),    # peach
    (233, 196, 106),   # gold
    (74, 93, 74),      # moss
    (107, 142, 90),    # sage-dark
]


def _annotate_screenshot(
    raw_path: Path,
    out_path: Path,
    elements: list[dict],
    viewport: dict | None,
) -> Path | None:
    if not raw_path.exists():
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        logging.warning("Pillow unavailable; skipping screenshot annotation")
        return None
    cfg = ConfigManager()
    try:
        img = Image.open(raw_path).convert("RGB")
    except Exception as e:
        logging.warning("Failed to open screenshot %s: %s", raw_path, e)
        return None

    img_w, img_h = img.size
    vp_w = float((viewport or {}).get("w") or img_w)
    vp_h = float((viewport or {}).get("h") or img_h)
    sx = img_w / vp_w if vp_w else 1.0
    sy = img_h / vp_h if vp_h else 1.0

    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", cfg.sentinel.annotation_label_font_px)
    except Exception:
        font = ImageFont.load_default()

    box_w = max(1, int(cfg.sentinel.annotation_box_width_px))
    pad = max(1, int(cfg.sentinel.annotation_label_pad_px))

    for idx, el in enumerate(elements):
        rect = el.get("rect") or {}
        try:
            x = float(rect["x"]) * sx
            y = float(rect["y"]) * sy
            w = float(rect["w"]) * sx
            h = float(rect["h"]) * sy
        except (KeyError, TypeError, ValueError):
            continue
        if w <= 1 or h <= 1:
            continue
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(img_w, x + w), min(img_h, y + h)
        if x2 <= x1 or y2 <= y1:
            continue

        color = _ANNOTATION_PALETTE[idx % len(_ANNOTATION_PALETTE)]
        draw.rectangle([x1, y1, x2, y2], outline=color + (255,), width=box_w)

        label = str(el.get("id") or "")
        if not label:
            continue
        tb = draw.textbbox((0, 0), label, font=font)
        text_w, text_h = tb[2] - tb[0], tb[3] - tb[1]

        lx2 = x1 + text_w + pad * 2
        ly2 = y1 + text_h + pad * 2
        draw.rectangle([x1, y1, lx2, ly2], fill=color + (230,))
        draw.text((x1 + pad, y1 + pad), label, fill=(255, 255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        img.save(out_path, format="PNG")
    except Exception as e:
        logging.warning("Failed to save annotated screenshot %s: %s", out_path, e)
        return None
    return out_path


def _observe_page(page) -> dict:
    cfg = ConfigManager()
    return page.evaluate(
        """
        ({ maxElements, maxTextChars, maxElementTextChars }) => {
          const rectOf = (el) => {
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height};
          };
          document.querySelectorAll('[data-sentinel-id]').forEach(el => el.removeAttribute('data-sentinel-id'));
          const usable = (el) => {
            const r = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (r.width <= 0 || r.height <= 0) return false;
            if (r.bottom <= 0 || r.right <= 0 || r.top >= window.innerHeight || r.left >= window.innerWidth) return false;
            if (style.visibility === 'hidden' || style.display === 'none' || style.pointerEvents === 'none') return false;
            if (el.disabled || el.getAttribute('aria-hidden') === 'true' || el.closest('[aria-hidden="true"],[inert]')) return false;
            const cx = Math.min(Math.max(r.left + r.width / 2, 0), window.innerWidth - 1);
            const cy = Math.min(Math.max(r.top + r.height / 2, 0), window.innerHeight - 1);
            const top = document.elementFromPoint(cx, cy);
            return Boolean(top && (el === top || el.contains(top)));
          };
          const candidates = Array.from(document.querySelectorAll(
            'a,button,input,textarea,select,'
            + '[role="button"],[role="link"],[role="menuitem"],[role="tab"],[role="checkbox"],[role="radio"],'
            + '[onclick],[tabindex]:not([tabindex="-1"])'
          ));
          const elements = [];
          for (const el of candidates) {
            if (elements.length >= maxElements) break;
            if (!usable(el)) continue;
            const id = `e${elements.length + 1}`;
            el.setAttribute('data-sentinel-id', id);
            elements.push({
              id,
              tag: el.tagName.toLowerCase(),
              type: el.getAttribute('type') || '',
              text: (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.href || '').trim().slice(0, maxElementTextChars),
              href: el.href || '',
              rect: rectOf(el)
            });
          }
          const bodyText = (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, maxTextChars);
          return {
            url: location.href,
            title: document.title,
            text: bodyText,
            elements,
            viewport: {w: window.innerWidth, h: window.innerHeight}
          };
        }
        """,
        {
            "maxElements": cfg.sentinel.observation_max_elements,
            "maxTextChars": cfg.sentinel.observation_text_max_chars,
            "maxElementTextChars": cfg.sentinel.observation_element_text_max_chars,
        },
    )


def _agent_prompt(report: dict, observation: dict) -> str:
    history = [
        {"action": step["action"], "reason": step["reason"], "result": step["result"]}
        for step in report["steps"][-6:]
    ]
    elements = [
        {
            "id": el.get("id", ""),
            "tag": el.get("tag", ""),
            "type": el.get("type", ""),
            "label": el.get("text", ""),
        }
        for el in (observation.get("elements") or [])
    ]
    hints = [
        f["detail"]
        for f in (report.get("findings") or [])
        if f.get("severity") in {"warning", "error"} and f.get("title") == "Repeated click with no navigation"
    ][-1:]
    payload = {
        "target_url": report["target_url"],
        "user_prompt": report["prompt"] or "Explore and test the site's main unauthenticated flows.",
        "history": history,
        "page": {
            "url": observation.get("url", ""),
            "title": observation.get("title", ""),
            "elements": elements,
        },
        "instructions": (
            "The attached screenshot shows the page with each interactive element outlined and "
            "labelled with a synthetic id (e.g. e1, e2). Use the screenshot as your primary input "
            "and choose elements visually. The 'elements' list is only a key for resolving labels "
            "to ids; do not rely on it for spatial layout."
        ),
    }
    if hints:
        payload["hints"] = hints
    return json.dumps(payload, indent=2)


def _apply_action(page, action: AgentAction, target: ValidatedTarget, allow_external: bool = False) -> dict:
    cfg = ConfigManager().sentinel
    try:
        if action.action == "finish":
            return {"ok": True, "url": page.url}
        if action.action == "wait":
            page.wait_for_timeout(cfg.wait_action_ms)
            return {"ok": True, "url": page.url}
        if action.action == "scroll":
            delta = -cfg.scroll_action_delta_px if (action.value or "").lower() == "up" else cfg.scroll_action_delta_px
            page.mouse.wheel(0, delta)
            page.wait_for_timeout(cfg.post_scroll_settle_ms)
            return {"ok": True, "url": page.url}
        if action.action == "goto":
            next_url = urljoin(page.url, action.url or target.url)
            return _goto_page(page, next_url, target, allow_external=allow_external)

        locator = page.locator(f'[data-sentinel-id="{action.element_id}"]').first
        if action.action == "click":
            locator.click()
            try:
                page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=cfg.post_click_load_timeout_ms,
                )
            except Exception:
                pass
            page.wait_for_timeout(cfg.post_click_settle_ms)
        elif action.action == "fill":
            locator.fill(action.value or "test")
            page.wait_for_timeout(cfg.post_fill_settle_ms)
        elif action.action == "select":
            locator.select_option(label=action.value or "")
            page.wait_for_timeout(cfg.post_select_settle_ms)
        return {"ok": True, "url": page.url}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": getattr(page, "url", "")}


def _record_step(report: dict, action: str, reason: str, result: dict) -> None:
    report["steps"].append(
        {
            "index": len(report["steps"]) + 1,
            "action": action,
            "reason": reason,
            "result": result,
            "created_at": utc_now_iso(),
        }
    )


def _request_agent_action(report, observation, image_paths, allow_external, known_ids):
    """Ask the LLM for the next action, retrying once on a parse failure.

    Returns the parsed AgentAction, or None if the run should abort. On abort,
    an ``invalid`` step has already been recorded and a finding added.
    """
    cfg = ConfigManager().sentinel
    attempts = max(1, cfg.agent_parse_retry_attempts + 1)
    last_error = None
    last_text = ""
    for attempt in range(attempts):
        agent_text = _get_provider().agent_text(
            _agent_prompt(report, observation),
            image_paths=image_paths,
            allow_accounts=bool(report.get("allow_accounts")),
            demographic=str(report.get("demographic") or ""),
            allow_external=allow_external,
            card_details=report.get("_card_details"),
            account_credentials=report.get("_account_credentials"),
        )
        try:
            return parse_agent_action(agent_text, known_ids)
        except ActionValidationError as e:
            last_error = e
            last_text = agent_text
            if attempt + 1 < attempts:
                logging.warning("Sentinel agent parse failure, retrying: %s", e)
    _record_step(report, "invalid", str(last_error), {"agent_text": last_text})
    _add_finding(report, "warning", "Agent response unparseable", str(last_error))
    return None


_LOGIN_FAIL_PREFIX = "login failed:"


def _detect_login_failure(report: dict) -> str:
    """If the agent finished with a 'login failed:' marker, return the reason."""
    if not report.get("allow_accounts") or not report.get("steps"):
        return ""
    last = report["steps"][-1]
    if last.get("action") != "finish":
        return ""
    reason = str(last.get("reason", "")).strip()
    if reason.lower().startswith(_LOGIN_FAIL_PREFIX):
        return reason
    return ""


def _detect_click_loop(report: dict) -> bool:
    """Surface a finding when the agent clicks the same element_id repeatedly
    without the URL changing — usually a sign the target control is broken or
    leads back to the same page.

    Returns True when the run should be stopped (warning count has exceeded
    ``click_loop_max_warnings``).
    """
    cfg = ConfigManager().sentinel
    threshold = cfg.click_loop_threshold
    if threshold <= 0:
        return False
    steps = report.get("steps") or []
    if len(steps) < threshold:
        return False
    tail = steps[-threshold:]
    if not all(s.get("action") == "click" for s in tail):
        return False
    urls = {(s.get("result") or {}).get("url") for s in tail}
    if len(urls) != 1:
        return False
    reasons = {s.get("reason", "")[:60] for s in tail}
    last_step = tail[-1].get("index")
    findings = report.get("findings") or []
    # Suppress if we already flagged a loop ending at this same step count.
    for finding in findings:
        if finding.get("title") == "Repeated click with no navigation" and str(last_step) in finding.get("detail", ""):
            return False
    _add_finding(
        report,
        "warning",
        "Repeated click with no navigation",
        f"The agent clicked through {threshold} consecutive steps ending at step {last_step} on URL {next(iter(urls))} "
        f"without the page changing. Reasons seen: {sorted(reasons)}. The control may be broken or self-referential; "
        "try a different element.",
    )
    loop_warnings = sum(
        1 for f in (report.get("findings") or []) if f.get("title") == "Repeated click with no navigation"
    )
    return cfg.click_loop_max_warnings > 0 and loop_warnings >= cfg.click_loop_max_warnings
