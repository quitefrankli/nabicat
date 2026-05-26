from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin

from web_app.config import ConfigManager
from web_app.helpers import bedrock_text, meridian_text
from web_app.sentinel.actions import ActionValidationError, AgentAction, parse_agent_action
from web_app.sentinel.data_interface import DataInterface, utc_now_iso
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

_TITLE_SYSTEM = (
    "You are titling a Sentinel QA run. Given the target URL and the user's prompt, return a single "
    "short, professional title (4 to 8 words) describing the run. Plain text only. No quotes, no "
    "trailing punctuation, no emojis, no em or en dashes. Do not start with words like 'Sentinel' "
    "or 'QA'."
)


_SYSTEM_BASE = (
    "You are controlling a browser as a practical human QA tester. "
    "Given the current observation and prior steps, choose exactly one next action. "
    "Return ONLY JSON with one of these shapes: "
    "{\"action\":\"click\",\"element_id\":\"e1\",\"reason\":\"...\"}, "
    "{\"action\":\"fill\",\"element_id\":\"e2\",\"value\":\"...\",\"reason\":\"...\"}, "
    "{\"action\":\"goto\",\"url\":\"/path\",\"reason\":\"...\"}, "
    "{\"action\":\"wait\",\"reason\":\"...\"}, "
    "{\"action\":\"finish\",\"reason\":\"...\"}. "
    "Prefer exploring core navigation, links, and obvious broken states. "
    "Avoid using search bars, search boxes, or generic query inputs unless the user's prompt "
    "explicitly asks you to test search. Most real users navigate by clicking visible links and "
    "menu items, not by typing into a search field. "
    "Do not navigate by typing URLs or guessing paths. The 'goto' action is reserved for "
    "following links you can actually see in the screenshot or element list. Do not invent or "
    "guess URLs, slugs, or path fragments. If a destination is not reachable through visible "
    "links or menu items, treat it as out of scope rather than guessing the URL. "
    "Do not attempt payment or destructive account actions."
)

_SYSTEM_ACCOUNTS_FORBIDDEN = (
    " Do not attempt login, account registration, account deletion, password reset, or any "
    "credential entry. Skip authentication flows entirely."
)

_SYSTEM_ACCOUNTS_ALLOWED = (
    " You may attempt login, account registration, account deletion, and credential entry as part "
    "of testing the requested flows. Always use synthetic, throwaway test values; never use real "
    "personal data."
)

# TODO: consider removing _SYSTEM_EXTERNAL_FORBIDDEN from the system prompt in the future. The
# network-layer guard already enforces this, and a real user does sometimes click off-site links
# (footer "powered by", partner logos, etc.). Letting the agent attempt those gives us a useful
# signal about how often a human would have left the target site.
_SYSTEM_EXTERNAL_FORBIDDEN = (
    " Stay on the target site. Do not click links, banners, or buttons that lead to a different "
    "hostname. External navigation is blocked at the network layer and will fail; do not waste "
    "steps trying."
)

_SYSTEM_EXTERNAL_ALLOWED = (
    " You may follow links to external sites if doing so is part of testing the requested user "
    "flow (for example, OAuth providers or partner checkout)."
)


def _system_prompt(allow_accounts: bool, demographic: str = "", allow_external: bool = False) -> str:
    persona = ConfigManager().sentinel.demographic_personas.get(demographic, "")
    base = _SYSTEM_BASE + (_SYSTEM_ACCOUNTS_ALLOWED if allow_accounts else _SYSTEM_ACCOUNTS_FORBIDDEN)
    base += _SYSTEM_EXTERNAL_ALLOWED if allow_external else _SYSTEM_EXTERNAL_FORBIDDEN
    if persona:
        return f"{persona} {base}"
    return base

_REPORT_SYSTEM = (
    "You are writing the final QA report for Sentinel. Directly answer the user's original prompt "
    "using only the run data provided. Be concise, factual, and practical. Include what was tested, "
    "what worked, what failed or looked risky, and any important caveats. Do not invent findings. "
    "Write in the voice of a human QA engineer producing a professional internal report. Use plain, "
    "neutral prose and standard punctuation. Do not use emojis. Do not use em dashes or en dashes; "
    "use commas, periods, parentheses, or colons instead. Avoid marketing language, exclamation "
    "marks, and filler superlatives. Prefer short paragraphs and short bullet lists over long prose. "
    "The report MUST begin with a level-2 markdown heading exactly equal to '## Summary' followed "
    "by a brief two to four sentence overview answering the user's prompt and stating the overall "
    "outcome. Additional sections (for example '## Findings', '## What Was Tested', '## Caveats') "
    "may follow the Summary as needed. "
    "Output GitHub-flavored markdown. Embed relevant screenshots inline as evidence using exactly "
    "this syntax: ![short caption](step-NN.png), where step-NN.png is one of the filenames listed "
    "in the run data's screenshots array. Reference at most one screenshot per finding, and only "
    "when it visually supports the claim. Do not invent filenames or use any other image URL."
)


def _save(report: dict) -> None:
    DataInterface().save_report(report)
    with _active_lock:
        _active_runs[report["run_id"]] = dict(report)


def get_run(run_id: str) -> dict | None:
    with _active_lock:
        if run_id in _active_runs:
            return dict(_active_runs[run_id])
    try:
        return DataInterface().load_report(run_id)
    except ValueError:
        return None


def start_run(
    target: ValidatedTarget,
    prompt: str,
    limit_s: int,
    title: str = "",
    allow_accounts: bool = False,
    allow_external: bool = False,
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
            report["status"] = outcome_status
    except Exception as e:
        logging.exception("Sentinel run failed")
        report["status"] = "failed"
        report["error"] = str(e)
    finally:
        report["finished_at"] = utc_now_iso()
        _save(report)
        with _active_lock:
            _cancel_events.pop(report["run_id"], None)
        DataInterface().prune_reports()


def _build_codex_cmd(output_path: str, image_paths: list[Path] | None = None) -> list[str]:
    cfg = ConfigManager()
    permissions_profile = cfg.sentinel.codex_permissions_profile
    cmd = [
        cfg.llm.codex_cli_command,
        "-a",
        cfg.llm.codex_cli_approval_policy,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-rules",
        "-c",
        "project_doc_max_bytes=0",
        "-c",
        "project_doc_fallback_filenames=[]",
        "-c",
        f"default_permissions={json.dumps(permissions_profile)}",
        "-c",
        f'permissions.{permissions_profile}.filesystem.:minimal="read"',
    ]
    for image_path in image_paths or []:
        if image_path.exists():
            cmd.extend(["--image", str(image_path)])
    cmd.extend(["--output-last-message", output_path])
    model = cfg.llm.model_for(cfg.sentinel.llm_tier)
    if model:
        cmd.extend(["--model", model])
    return cmd


def _codex_text(system: str, user_message: str, image_paths: list[Path] | None, timeout_s: float) -> str:
    cfg = ConfigManager()
    prompt = f"{system}\n\nUser request:\n{user_message}"
    with tempfile.NamedTemporaryFile("r+", encoding="utf-8") as output:
        cmd = _build_codex_cmd(output.name, image_paths)
        cmd.append(prompt)
        proc = subprocess.run(
            cmd,
            cwd=str(cfg.project_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"codex cli exited {proc.returncode}: {detail[:500]}")
        output.seek(0)
        text = output.read().strip()
        if not text:
            raise RuntimeError("codex cli returned an empty response")
        return text


class _LLMProvider:
    def agent_text(
        self,
        user_message: str,
        image_paths: list[Path] | None = None,
        allow_accounts: bool = False,
        demographic: str = "",
        allow_external: bool = False,
    ) -> str:
        raise NotImplementedError

    def final_report_text(self, user_message: str, image_paths: list[Path] | None = None) -> str:
        raise NotImplementedError

    def title_text(self, user_message: str) -> str:
        raise NotImplementedError


class _CodexProvider(_LLMProvider):
    def agent_text(
        self,
        user_message: str,
        image_paths: list[Path] | None = None,
        allow_accounts: bool = False,
        demographic: str = "",
        allow_external: bool = False,
    ) -> str:
        return _codex_text(
            system=_system_prompt(allow_accounts, demographic, allow_external),
            user_message=user_message,
            image_paths=image_paths,
            timeout_s=ConfigManager().sentinel.llm_step_timeout_s,
        )

    def final_report_text(self, user_message: str, image_paths: list[Path] | None = None) -> str:
        return _codex_text(
            system=_REPORT_SYSTEM,
            user_message=user_message,
            image_paths=image_paths,
            timeout_s=ConfigManager().sentinel.final_report_timeout_s,
        )

    def title_text(self, user_message: str) -> str:
        return _codex_text(
            system=_TITLE_SYSTEM,
            user_message=user_message,
            image_paths=None,
            timeout_s=ConfigManager().sentinel.llm_title_timeout_s,
        )


class _MeridianProvider(_LLMProvider):
    def _model(self) -> str | None:
        cfg = ConfigManager()
        return cfg.llm.model_for(cfg.sentinel.llm_tier)

    def agent_text(
        self,
        user_message: str,
        image_paths: list[Path] | None = None,
        allow_accounts: bool = False,
        demographic: str = "",
        allow_external: bool = False,
    ) -> str:
        cfg = ConfigManager()
        return meridian_text(
            user_message=user_message,
            system=_system_prompt(allow_accounts, demographic, allow_external),
            model=self._model(),
            max_tokens=cfg.sentinel.llm_step_max_tokens,
            timeout_s=cfg.sentinel.llm_step_timeout_s,
            agent="sentinel",
            image_paths=image_paths,
        )

    def final_report_text(self, user_message: str, image_paths: list[Path] | None = None) -> str:
        cfg = ConfigManager()
        return meridian_text(
            user_message=user_message,
            system=_REPORT_SYSTEM,
            model=self._model(),
            max_tokens=cfg.sentinel.llm_final_report_max_tokens,
            timeout_s=cfg.sentinel.final_report_timeout_s,
            agent="sentinel",
            image_paths=image_paths,
        )

    def title_text(self, user_message: str) -> str:
        cfg = ConfigManager()
        return meridian_text(
            user_message=user_message,
            system=_TITLE_SYSTEM,
            model=self._model(),
            max_tokens=cfg.sentinel.llm_title_max_tokens,
            timeout_s=cfg.sentinel.llm_title_timeout_s,
            agent="sentinel",
            image_paths=None,
        )


class _BedrockProvider(_LLMProvider):
    def _model(self) -> str:
        cfg = ConfigManager()
        return cfg.llm.model_for(cfg.sentinel.llm_tier)

    def agent_text(
        self,
        user_message: str,
        image_paths: list[Path] | None = None,
        allow_accounts: bool = False,
        demographic: str = "",
        allow_external: bool = False,
    ) -> str:
        cfg = ConfigManager()
        return bedrock_text(
            user_message=user_message,
            system=_system_prompt(allow_accounts, demographic, allow_external),
            model=self._model(),
            max_tokens=cfg.sentinel.llm_step_max_tokens,
            timeout_s=cfg.sentinel.llm_step_timeout_s,
            image_paths=image_paths,
        )

    def final_report_text(self, user_message: str, image_paths: list[Path] | None = None) -> str:
        cfg = ConfigManager()
        return bedrock_text(
            user_message=user_message,
            system=_REPORT_SYSTEM,
            model=self._model(),
            max_tokens=cfg.sentinel.llm_final_report_max_tokens,
            timeout_s=cfg.sentinel.final_report_timeout_s,
            image_paths=image_paths,
        )

    def title_text(self, user_message: str) -> str:
        cfg = ConfigManager()
        return bedrock_text(
            user_message=user_message,
            system=_TITLE_SYSTEM,
            model=self._model(),
            max_tokens=cfg.sentinel.llm_title_max_tokens,
            timeout_s=cfg.sentinel.llm_title_timeout_s,
            image_paths=None,
        )


def _get_provider() -> _LLMProvider:
    source = ConfigManager().llm.api_source
    if source == "meridian":
        return _MeridianProvider()
    if source == "bedrock":
        return _BedrockProvider()
    return _CodexProvider()


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

            while time.monotonic() < deadline and len(report["steps"]) < cfg.sentinel.max_steps:
                if _is_cancelled(report["run_id"]):
                    break
                observation = _observe_page(page)
                known_ids = {item["id"] for item in observation["elements"]}
                screenshot = _capture_screenshot(page, report)
                annotated = _capture_annotated_screenshot(report, screenshot, observation)
                if screenshot:
                    observation["screenshot"] = screenshot

                image_paths = _annotated_image_paths(report, annotated, screenshot)
                agent_text = _get_provider().agent_text(
                    _agent_prompt(report, observation),
                    image_paths=image_paths,
                    allow_accounts=bool(report.get("allow_accounts")),
                    demographic=str(report.get("demographic") or ""),
                    allow_external=allow_external,
                )
                try:
                    action = parse_agent_action(agent_text, known_ids)
                except ActionValidationError as e:
                    _record_step(report, "invalid", str(e), {"agent_text": agent_text})
                    break

                result = _apply_action(page, action, target, allow_external=allow_external)
                _record_step(report, action.action, action.reason, result)
                _save(report)
                if action.action == "finish":
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


def _add_final_report(report: dict) -> None:
    try:
        text = _get_provider().final_report_text(
            _final_report_prompt(report),
            image_paths=_final_report_image_paths(report),
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


def _final_report_prompt(report: dict) -> str:
    payload = {
        "original_prompt": report.get("prompt") or "Explore and test the site's main unauthenticated flows.",
        "target_url": report.get("target_url"),
        "status": report.get("run_outcome") or report.get("status"),
        "steps": [
            {"action": step.get("action"), "reason": step.get("reason"), "result": step.get("result")}
            for step in report.get("steps", [])
        ],
        "findings": report.get("findings", []),
        "screenshots": report.get("screenshots", []),
    }
    return json.dumps(payload, indent=2)


def _final_report_image_paths(report: dict) -> list[Path]:
    max_images = ConfigManager().sentinel.final_report_max_images
    paths = []
    for screenshot in report.get("screenshots", [])[-max_images:]:
        filename = Path(str(screenshot)).name
        path = DataInterface().screenshots_dir(report["run_id"]) / filename
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


def _capture_screenshot(page, report: dict) -> str | None:
    cfg = ConfigManager()
    if len(report["screenshots"]) >= cfg.sentinel.max_screenshots:
        return None
    path = DataInterface().screenshot_path(report["run_id"], len(report["screenshots"]) + 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=False)
    rel = f"screenshots/{path.name}"
    report["screenshots"].append(rel)
    return rel


def _screenshot_image_paths(report: dict, screenshot: str | None) -> list[Path]:
    if not screenshot:
        return []
    filename = Path(screenshot).name
    path = DataInterface().screenshots_dir(report["run_id"]) / filename
    return [path] if path.exists() else []


def _capture_annotated_screenshot(report: dict, screenshot: str | None, observation: dict) -> str | None:
    if not screenshot:
        return None
    raw_filename = Path(screenshot).name
    raw_path = DataInterface().screenshots_dir(report["run_id"]) / raw_filename
    index = len(report["screenshots"])
    out_path = DataInterface().annotated_screenshot_path(report["run_id"], index)
    written = _annotate_screenshot(
        raw_path,
        out_path,
        observation.get("elements") or [],
        observation.get("viewport"),
    )
    if written is None:
        return None
    rel = f"screenshots/{out_path.name}"
    report.setdefault("annotated_screenshots", []).append(rel)
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
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const candidates = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role="button"]'));
          const elements = [];
          for (const el of candidates.slice(0, maxElements)) {
            if (!visible(el)) continue;
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
    return json.dumps(payload, indent=2)


def _apply_action(page, action: AgentAction, target: ValidatedTarget, allow_external: bool = False) -> dict:
    try:
        if action.action == "finish":
            return {"ok": True, "url": page.url}
        if action.action == "wait":
            page.wait_for_timeout(ConfigManager().sentinel.wait_action_ms)
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
                    timeout=ConfigManager().sentinel.post_click_load_timeout_ms,
                )
            except Exception:
                pass
        elif action.action == "fill":
            locator.fill(action.value or "test")
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
