from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin

from web_app.config import ConfigManager
from web_app.sentinel.actions import ActionValidationError, AgentAction, parse_agent_action
from web_app.sentinel.data_interface import DataInterface, utc_now_iso
from web_app.sentinel.target_policy import ValidatedTarget, validate_public_web_url


_active_runs: dict[str, dict] = {}
_active_lock = threading.RLock()

_SYSTEM = (
    "You are controlling a browser as a practical human QA tester. "
    "Given the current observation and prior steps, choose exactly one next action. "
    "Return ONLY JSON with one of these shapes: "
    "{\"action\":\"click\",\"element_id\":\"e1\",\"reason\":\"...\"}, "
    "{\"action\":\"fill\",\"element_id\":\"e2\",\"value\":\"...\",\"reason\":\"...\"}, "
    "{\"action\":\"goto\",\"url\":\"/path\",\"reason\":\"...\"}, "
    "{\"action\":\"wait\",\"reason\":\"...\"}, "
    "{\"action\":\"finish\",\"reason\":\"...\"}. "
    "Prefer exploring core navigation, forms, buttons, and obvious broken states. "
    "Do not attempt payment or destructive account actions. "
    "Only attempt login, account registration, or credential entry when the user's prompt explicitly asks for it, "
    "and use synthetic test values."
)

_REPORT_SYSTEM = (
    "You are writing the final QA report for Sentinel. Directly answer the user's original prompt "
    "using only the run data provided. Be concise, factual, and practical. Include what was tested, "
    "what worked, what failed or looked risky, and any important caveats. Do not invent findings."
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


def start_run(target: ValidatedTarget, prompt: str, limit_s: int) -> dict:
    run_id = uuid.uuid4().hex
    now = utc_now_iso()
    report = {
        "run_id": run_id,
        "status": "queued",
        "target_url": target.url,
        "target_hostname": target.hostname,
        "prompt": prompt,
        "limit_s": limit_s,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
        "run_outcome": None,
        "steps": [],
        "findings": [],
        "screenshots": [],
        "final_report": "",
        "error": None,
    }
    _save(report)
    thread = threading.Thread(target=_run_background, args=(report,), daemon=True)
    thread.start()
    return {"run_id": run_id, "status": "queued"}


def _run_background(report: dict) -> None:
    report["status"] = "running"
    report["started_at"] = utc_now_iso()
    _save(report)
    try:
        _execute_browser_run(report)
        outcome_status = "completed" if report["status"] == "running" else report["status"]
        report["run_outcome"] = outcome_status
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
        DataInterface().prune_reports()


def _build_codex_cmd(output_path: str, image_paths: list[Path] | None = None) -> list[str]:
    cfg = ConfigManager()
    permissions_profile = cfg.sentinel_codex_permissions_profile
    cmd = [
        cfg.sentinel_codex_cli_command,
        "-a",
        cfg.sentinel_codex_cli_approval_policy,
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
    if cfg.sentinel_codex_model:
        cmd.extend(["--model", cfg.sentinel_codex_model])
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


def _codex_agent_text(user_message: str, image_paths: list[Path] | None = None) -> str:
    return _codex_text(
        system=_SYSTEM,
        user_message=user_message,
        image_paths=image_paths,
        timeout_s=ConfigManager().sentinel_codex_step_timeout_s,
    )


def _codex_final_report_text(user_message: str, image_paths: list[Path] | None = None) -> str:
    return _codex_text(
        system=_REPORT_SYSTEM,
        user_message=user_message,
        image_paths=image_paths,
        timeout_s=ConfigManager().sentinel_final_report_timeout_s,
    )


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
        try:
            page = browser.new_page(
                viewport={"width": cfg.sentinel_browser_width_px, "height": cfg.sentinel_browser_height_px}
            )
            page.set_default_timeout(cfg.sentinel_browser_default_timeout_ms)
            page.on("console", lambda msg: _add_finding(report, "info", "Console", msg.text))
            page.on("pageerror", lambda err: _add_finding(report, "error", "Page error", str(err)))

            def guard_route(route):
                req_url = route.request.url
                try:
                    checked = validate_public_web_url(req_url)
                    is_navigation = route.request.is_navigation_request()
                    if is_navigation and not _host_allowed(checked.hostname, target.hostname):
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

            while time.monotonic() < deadline and len(report["steps"]) < cfg.sentinel_max_steps:
                observation = _observe_page(page)
                known_ids = {item["id"] for item in observation["elements"]}
                screenshot = _capture_screenshot(page, report)
                if screenshot:
                    observation["screenshot"] = screenshot

                image_paths = _screenshot_image_paths(report, screenshot)
                agent_text = _codex_agent_text(_agent_prompt(report, observation), image_paths=image_paths)
                try:
                    action = parse_agent_action(agent_text, known_ids)
                except ActionValidationError as e:
                    _record_step(report, "invalid", str(e), {"agent_text": agent_text})
                    break

                result = _apply_action(page, action, target)
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
            browser.close()


def _goto_page(page, url: str, target: ValidatedTarget) -> dict:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    cfg = ConfigManager()
    checked = validate_public_web_url(url)
    if not _host_allowed(checked.hostname, target.hostname):
        return {"ok": False, "error": "Navigation outside target host blocked", "url": checked.url}
    page.goto(checked.url, wait_until="commit", timeout=cfg.sentinel_navigation_timeout_ms)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=cfg.sentinel_navigation_timeout_ms)
    except PlaywrightTimeoutError:
        return {"ok": True, "warning": "Timed out waiting for DOMContentLoaded", "url": page.url}
    return {"ok": True, "url": page.url}


def _add_finding(report: dict, severity: str, title: str, detail: str) -> None:
    max_chars = ConfigManager().sentinel_finding_detail_max_chars
    detail = " ".join(str(detail).split())
    if len(detail) > max_chars:
        detail = f"{detail[:max_chars].rstrip()}..."
    report["findings"].append({"severity": severity, "title": title, "detail": detail})


def _add_final_report(report: dict) -> None:
    try:
        text = _codex_final_report_text(
            _final_report_prompt(report),
            image_paths=_final_report_image_paths(report),
        )
    except Exception as e:
        logging.warning("Sentinel final report generation failed: %s", e)
        text = _fallback_final_report(report)
    report["final_report"] = _truncate_text(text, ConfigManager().sentinel_final_report_max_chars)
    _save(report)


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
    max_images = ConfigManager().sentinel_final_report_max_images
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
    if len(report["screenshots"]) >= cfg.sentinel_max_screenshots:
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


def _observe_page(page) -> dict:
    cfg = ConfigManager()
    return page.evaluate(
        """
        ({ maxElements, maxTextChars, maxElementTextChars }) => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
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
              href: el.href || ''
            });
          }
          const bodyText = (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, maxTextChars);
          return {url: location.href, title: document.title, text: bodyText, elements};
        }
        """,
        {
            "maxElements": cfg.sentinel_observation_max_elements,
            "maxTextChars": cfg.sentinel_observation_text_max_chars,
            "maxElementTextChars": cfg.sentinel_observation_element_text_max_chars,
        },
    )


def _agent_prompt(report: dict, observation: dict) -> str:
    history = [
        {"action": step["action"], "reason": step["reason"], "result": step["result"]}
        for step in report["steps"][-6:]
    ]
    payload = {
        "target_url": report["target_url"],
        "user_prompt": report["prompt"] or "Explore and test the site's main unauthenticated flows.",
        "history": history,
        "observation": observation,
    }
    return json.dumps(payload, indent=2)


def _apply_action(page, action: AgentAction, target: ValidatedTarget) -> dict:
    try:
        if action.action == "finish":
            return {"ok": True, "url": page.url}
        if action.action == "wait":
            page.wait_for_timeout(ConfigManager().sentinel_wait_action_ms)
            return {"ok": True, "url": page.url}
        if action.action == "goto":
            next_url = urljoin(page.url, action.url or target.url)
            return _goto_page(page, next_url, target)

        locator = page.locator(f'[data-sentinel-id="{action.element_id}"]').first
        if action.action == "click":
            locator.click()
            try:
                page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=ConfigManager().sentinel_post_click_load_timeout_ms,
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
