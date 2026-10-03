from __future__ import annotations

import io
import json
import re
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlparse


from .actions import ActionValidationError, AgentAction, parse_agent_action
from .data_interface import DataInterface, utc_now_iso
from web_app.config import ConfigManager
from .models import (
    ActionResult,
    ExecutionStatus,
    Finding,
    Report,
    RunStatus,
    RunVerdict,
    Step,
)
from .providers import _get_provider

from .runtime import (
    RequestDeadlineExceeded,
    capped_timeout_ms,
    deadline_scope,
    emit_event,
    execution_lease,
    renew_execution_lease,
    state_delete,
    state_get,
    state_put,
    state_replace,
    remaining_request_seconds,
)
from .secrets import (
    build_secrets,
    contains_secret,
    current_secrets,
    redact_text,
    secret_scope,
)
from .target_policy import ValidatedTarget

_active_runs: dict[str, Report] = {}
_active_lock = threading.RLock()
_active_leases: dict[str, Any] = {}


class RunBusyError(RuntimeError):
    pass


class LeaseLostError(RuntimeError):
    pass


def _scrub_report(report: Report) -> None:
    secrets = current_secrets()
    if not secrets.scrub_values:
        return
    for field_name in (
        "prompt",
        "title",
        "batch_label",
        "final_report",
        "error",
        "verdict_reason",
    ):
        value = getattr(report, field_name)
        if isinstance(value, str):
            setattr(report, field_name, redact_text(value))
    for finding in report.findings:
        finding.title = redact_text(finding.title)
        finding.detail = redact_text(finding.detail)
        finding.url = redact_text(finding.url)
    for step in report.steps:
        step.reason = redact_text(step.reason)
        for field_name in ("url", "error", "warning", "blocked_url", "agent_text"):
            value = getattr(step.result, field_name)
            if isinstance(value, str):
                setattr(step.result, field_name, redact_text(value))


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


def _renew_execution(report: Report) -> None:
    remaining = remaining_request_seconds()
    if remaining is not None and remaining <= 0:
        raise RequestDeadlineExceeded("Sentinel reached its request deadline")
    lease = _active_leases.get(report.run_id)
    if lease is None:
        return
    if not renew_execution_lease(lease):
        raise LeaseLostError("Sentinel lost its global execution lease")


def render_report_pdf(html: str, stylesheet_paths: tuple[Path, ...] = ()) -> bytes:
    """Render an HTML string to PDF bytes using headless Chromium (Playwright)."""
    from playwright.sync_api import sync_playwright

    cfg = ConfigManager().sentinel
    # Chromium's header/footer templates do NOT inherit the page <style>, so
    # the footer must carry fully inline styling. A near-empty header_template
    # suppresses Chromium's default date/title header.
    footer_template = (
        "<div style=\"font-family: 'Nunito', sans-serif; font-size:7.5pt; color:#8A9A8A; "
        'width:100%; padding:0 14mm; display:flex; justify-content:space-between;">'
        f'<span>{cfg.pdf_footer_label} &middot; <span class="date"></span></span>'
        '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
        "</div>"
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = None
        try:
            context = browser.new_context(
                java_script_enabled=False,
                service_workers="block",
            )

            def abort_request(route: Any) -> None:
                route.abort()

            def close_websocket(route: Any) -> None:
                route.close(code=1008, reason="PDF rendering is offline")

            context.route("**/*", abort_request)
            context.route_web_socket("**/*", close_websocket)
            page = context.new_page()
            page.set_content(html, wait_until="load")
            for stylesheet_path in stylesheet_paths:
                page.add_style_tag(path=str(stylesheet_path))
            return page.pdf(
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=footer_template,
                margin={
                    "top": cfg.pdf_margin_top,
                    "bottom": cfg.pdf_margin_bottom,
                    "left": cfg.pdf_margin_left,
                    "right": cfg.pdf_margin_right,
                },
            )
        finally:
            if context is not None:
                context.close()
            browser.close()


def request_cancel(run_id: str) -> bool:
    """Atomically replace the live cancellation marker without extending TTL."""
    try:
        updated = state_replace(f"cancel/{run_id}", b"1")
    except Exception as error:
        emit_event(
            "sentinel.cancel_signal_failed",
            run_id=run_id,
            error=error,
            error_type=type(error).__name__,
        )
        return False
    return bool(updated)


def _is_cancelled(run_id: str) -> bool:
    try:
        return state_get(f"cancel/{run_id}") == b"1"
    except Exception as error:
        emit_event(
            "sentinel.cancel_check_failed",
            run_id=run_id,
            error=error,
            error_type=type(error).__name__,
        )
        return False


_ACTIVE_STATUSES = {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.SUMMARIZING}
_ACTIVE_LIFECYCLES = {
    ExecutionStatus.QUEUED,
    ExecutionStatus.RUNNING,
    ExecutionStatus.SUMMARIZING,
}


class _PersistedRunTerminal(RuntimeError):
    def __init__(self, report: Report) -> None:
        super().__init__("Run was made terminal by another process")
        self.report = report


def _adopt_report_state(report: Report, persisted: Report) -> None:
    for field_name in Report.model_fields:
        setattr(report, field_name, getattr(persisted, field_name))


def _save(report: Report) -> None:
    # The cache snapshot is a deep copy: the run loop keeps mutating `report`
    # (appending steps/findings) after this returns, and a shallow copy would
    # alias those lists into the cached entry, so a concurrent reader via
    # get_run() could observe half-written state.
    _scrub_report(report)
    persisted = DataInterface()._save_report(report)
    cached = persisted or report
    with _active_lock:
        _active_runs[report.run_id] = cached.model_copy(deep=True)
    if persisted is not None and persisted is not report:
        raise _PersistedRunTerminal(persisted)


def get_run(run_id: str) -> Report | None:
    with _active_lock:
        cached = _active_runs.get(run_id)
    try:
        persisted = DataInterface().load_report(run_id)
    except ValueError:
        return None
    if cached is not None:
        # A separate worker/UI process may have recovered a stale active run.
        # Terminal disk state wins over this process's active snapshot.
        if (
            persisted is not None
            and cached.lifecycle in _ACTIVE_LIFECYCLES
            and persisted.lifecycle not in _ACTIVE_LIFECYCLES
        ):
            with _active_lock:
                _active_runs[run_id] = persisted.model_copy(deep=True)
            return persisted
        return cached.model_copy(deep=True)
    return persisted


def delete_run(run_id: str) -> bool:
    report = get_run(run_id)
    if report is None or report.status in _ACTIVE_STATUSES:
        return False
    try:
        deleted = DataInterface().delete_run(run_id)
    except ValueError:
        return False
    if deleted:
        with _active_lock:
            _active_runs.pop(run_id, None)
        state_delete(f"cancel/{run_id}")
    return deleted


def create_run(
    target: ValidatedTarget,
    prompt: str,
    limit_s: int,
    title: str = "",
    allow_accounts: bool = False,
    allow_external: bool = False,
    additional_domains: list[str] | None = None,
    allow_financial: bool = False,
    card_details: dict[str, str] | None = None,
    account_credentials: dict[str, object] | None = None,
    device: str = "",
    demographic: str = "",
    owner: str = "",
    batch_id: str = "",
    batch_label: str = "",
) -> Report:
    run_id = uuid.uuid4().hex
    now = utc_now_iso()
    title = _clean_title(title)
    cfg = ConfigManager().sentinel
    if device not in cfg.device_profiles:
        device = cfg.default_device
    if demographic not in cfg.demographic_personas:
        demographic = cfg.default_demographic
    report = Report(
        run_id=run_id,
        status=RunStatus.QUEUED,
        lifecycle=ExecutionStatus.QUEUED,
        verdict=RunVerdict.INCONCLUSIVE,
        owner=str(owner or ""),
        batch_id=str(batch_id or ""),
        batch_label=str(batch_label or ""),
        target_url=target.url,
        target_hostname=target.hostname,
        allowed_hosts=sorted(target.allowed_hosts),
        resolved_addresses={key: list(value) for key, value in target.addresses.items()},
        prompt=prompt,
        title=title,
        allow_accounts=bool(allow_accounts),
        allow_external=bool(allow_external),
        additional_domains=list(additional_domains or []),
        allow_financial=bool(allow_financial and card_details),
        device=device,
        demographic=demographic,
        limit_s=limit_s,
        created_at=now,
        updated_at=now,
    )
    try:
        armed = state_put(
            f"cancel/{run_id}", b"0", ttl_s=cfg.cancel_flag_ttl_s, if_absent=True
        )
        if not armed:
            raise RuntimeError("Could not arm cancellation state")
        _save(report)
    except KeyboardInterrupt:
        _terminalize_interrupted_creation(report)
        _clear_cancel_flag(run_id)
        raise
    except Exception:
        _clear_cancel_flag(run_id)
        raise
    emit_event(
        "sentinel.run_queued",
        run_id=run_id,
        owner=report.owner or None,
        batch_id=batch_id or None,
    )
    return report


def _clear_cancel_flag(run_id: str) -> None:
    try:
        state_delete(f"cancel/{run_id}")
    except Exception as error:
        emit_event(
            "sentinel.cancel_cleanup_failed",
            run_id=run_id,
            error=error,
            error_type=type(error).__name__,
        )


def _terminalize_interrupted_creation(report: Report) -> None:
    """Make a partially persisted queued report truthful after interruption."""
    try:
        persisted = DataInterface().load_report(report.run_id)
        if persisted is None:
            return
        if persisted.lifecycle in _ACTIVE_LIFECYCLES:
            _set_execution_state(persisted, ExecutionStatus.INTERRUPTED, RunVerdict.INCONCLUSIVE)
            persisted.verdict_reason_code = "interrupted"
            persisted.verdict_reason = "The run was interrupted while it was being created."
            persisted.finished_at = utc_now_iso()
            _save(persisted)
        _adopt_report_state(report, persisted)
    except _PersistedRunTerminal as terminal:
        _adopt_report_state(report, terminal.report)
    except Exception as error:
        emit_event(
            "sentinel.run_creation_terminalize_failed",
            owner=report.owner or None,
            run_id=report.run_id,
            batch_id=report.batch_id or None,
            error=error,
            error_type=type(error).__name__,
        )


def start_run(
    target: ValidatedTarget,
    prompt: str,
    limit_s: int,
    title: str = "",
    allow_accounts: bool = False,
    allow_external: bool = False,
    additional_domains: list[str] | None = None,
    allow_financial: bool = False,
    card_details: dict[str, str] | None = None,
    account_credentials: dict[str, object] | None = None,
    device: str = "",
    demographic: str = "",
    owner: str = "",
    batch_id: str = "",
    batch_label: str = "",
) -> Report:
    """Acquire the global execution lease and execute the run inline."""
    cfg = ConfigManager().sentinel
    secrets = build_secrets(
        account_credentials if allow_accounts else None,
        card_details if allow_financial else None,
    )
    report: Report | None = None
    with execution_lease() as lease:
        if lease is None:
            raise RunBusyError("Another Sentinel run is already executing")
        try:
            with secret_scope(secrets), deadline_scope(time.monotonic() + cfg.request_deadline_s):
                protected_target_values = (
                    target.url,
                    target.hostname,
                    *target.allowed_hosts,
                    *target.addresses,
                    *(address for addresses in target.addresses.values() for address in addresses),
                    *(additional_domains or ()),
                )
                if any(contains_secret(value) for value in protected_target_values):
                    raise ValueError(
                        "Target URL or allowed hostname must not contain supplied secret values."
                    )
                report = create_run(
                    target=target,
                    prompt=prompt,
                    limit_s=limit_s,
                    title=title,
                    allow_accounts=allow_accounts,
                    allow_external=allow_external,
                    additional_domains=additional_domains,
                    allow_financial=allow_financial,
                    card_details=card_details,
                    account_credentials=account_credentials,
                    device=device,
                    demographic=demographic,
                    owner=owner,
                    batch_id=batch_id,
                    batch_label=batch_label,
                )
                _active_leases[report.run_id] = lease
                return execute_run(report)
        finally:
            if report is not None:
                _active_leases.pop(report.run_id, None)


def _set_execution_state(
    report: Report,
    lifecycle: ExecutionStatus,
    verdict: RunVerdict | None = None,
) -> None:
    """Update the lifecycle, verdict, and UI status projection together."""
    report.lifecycle = lifecycle
    if verdict is not None:
        report.verdict = verdict
    if lifecycle == ExecutionStatus.QUEUED:
        report.status = RunStatus.QUEUED
    elif lifecycle == ExecutionStatus.RUNNING:
        report.status = RunStatus.RUNNING
    elif lifecycle == ExecutionStatus.SUMMARIZING:
        report.status = RunStatus.SUMMARIZING
    elif lifecycle == ExecutionStatus.CANCELLED:
        report.status = RunStatus.CANCELLED
        report.run_outcome = RunStatus.CANCELLED
    elif lifecycle == ExecutionStatus.TIMED_OUT:
        report.status = RunStatus.TIMED_OUT
        report.run_outcome = RunStatus.TIMED_OUT
    elif lifecycle == ExecutionStatus.FINISHED:
        report.status = (
            RunStatus.COMPLETED if report.verdict == RunVerdict.PASS else RunStatus.FAILED
        )
        report.run_outcome = report.status
    else:
        report.status = RunStatus.FAILED
        report.run_outcome = RunStatus.FAILED


def execute_run(report: Report) -> Report:
    """Execute a created run synchronously and return its terminal report."""
    started_ns = time.monotonic_ns()
    terminalized_elsewhere = False
    try:
        _set_execution_state(report, ExecutionStatus.RUNNING, RunVerdict.INCONCLUSIVE)
        report.started_at = utc_now_iso()
        _renew_execution(report)
        if not report.title:
            report.title = _generate_title(report.target_url, report.prompt, report.target_hostname)
        if report.batch_id and not report.batch_label:
            report.batch_label = report.title or ConfigManager().sentinel.batch_name_fallback
        _save(report)
        emit_event(
            "sentinel.run_started",
            owner=report.owner or None,
            run_id=report.run_id,
            batch_id=report.batch_id or None,
        )
        _execute_browser_run(report)
        if _is_cancelled(report.run_id):
            _set_execution_state(report, ExecutionStatus.CANCELLED)
        elif report.lifecycle == ExecutionStatus.RUNNING and report.status == RunStatus.TIMED_OUT:
            _set_execution_state(report, ExecutionStatus.TIMED_OUT)
        if report.lifecycle == ExecutionStatus.CANCELLED:
            report.final_report = "## Summary\n\nThis run was cancelled before it finished."
        else:
            browser_lifecycle = report.lifecycle
            report.run_outcome = (
                RunStatus.TIMED_OUT
                if browser_lifecycle == ExecutionStatus.TIMED_OUT
                else RunStatus.COMPLETED
            )
            _set_execution_state(report, ExecutionStatus.SUMMARIZING)
            _save(report)
            _renew_execution(report)
            _add_final_report(report)
            if _is_cancelled(report.run_id):
                _set_execution_state(report, ExecutionStatus.CANCELLED)
            elif browser_lifecycle == ExecutionStatus.TIMED_OUT:
                _set_execution_state(report, ExecutionStatus.TIMED_OUT)
                report.verdict_reason_code = "timed_out"
                report.verdict_reason = "The run reached its execution time limit."
            else:
                if _is_cancelled(report.run_id):
                    _set_execution_state(report, ExecutionStatus.CANCELLED)
                else:
                    login_fail_reason = _detect_login_failure(report)
                    if login_fail_reason:
                        report.verdict_reason = login_fail_reason
                        report.verdict_reason_code = "login_failed"
                        _add_finding(report, "error", "Login failed", login_fail_reason)
                        verdict = RunVerdict.FAIL
                    elif _is_cancelled(report.run_id):
                        _set_execution_state(report, ExecutionStatus.CANCELLED)
                        verdict = None
                    else:
                        _renew_execution(report)
                        verdict = _classify_run_verdict(report)
                    if verdict is not None:
                        if _is_cancelled(report.run_id):
                            _set_execution_state(report, ExecutionStatus.CANCELLED)
                        else:
                            _set_execution_state(report, ExecutionStatus.FINISHED, verdict)
    except KeyboardInterrupt:
        _set_execution_state(report, ExecutionStatus.INTERRUPTED, RunVerdict.INCONCLUSIVE)
        report.verdict_reason_code = "interrupted"
        report.verdict_reason = "The run was interrupted before it finished."
        raise
    except _PersistedRunTerminal as terminal:
        terminalized_elsewhere = True
        _adopt_report_state(report, terminal.report)
    except RequestDeadlineExceeded:
        _set_execution_state(report, ExecutionStatus.TIMED_OUT, RunVerdict.INCONCLUSIVE)
        report.verdict_reason_code = "request_deadline"
        report.verdict_reason = "The run reached Sentinel's request deadline."
        report.final_report = "## Summary\n\nThis run reached its request deadline."
    except LeaseLostError:
        _set_execution_state(report, ExecutionStatus.INTERRUPTED, RunVerdict.INCONCLUSIVE)
        report.verdict_reason_code = "lease_lost"
        report.verdict_reason = "The executor lost ownership of this run."
        report.final_report = "## Summary\n\nThis run stopped after its execution lease was lost."
    except Exception as e:
        emit_event(
            "sentinel.run_execution_error",
            owner=report.owner or None,
            run_id=report.run_id,
            batch_id=report.batch_id or None,
            error=e,
            error_type=type(e).__name__,
        )
        _set_execution_state(report, ExecutionStatus.EXECUTION_ERROR, RunVerdict.INCONCLUSIVE)
        report.verdict_reason_code = "execution_error"
        report.verdict_reason = "Sentinel could not complete the browser execution."
        report.error = str(e)
    finally:
        if not terminalized_elsewhere:
            report.finished_at = utc_now_iso()
            try:
                _save(report)
            except _PersistedRunTerminal as terminal:
                terminalized_elsewhere = True
                _adopt_report_state(report, terminal.report)
        _clear_cancel_flag(report.run_id)
        emit_event(
            "sentinel.run_finished",
            owner=report.owner or None,
            run_id=report.run_id,
            batch_id=report.batch_id or None,
            status=report.status.value,
            lifecycle=report.lifecycle.value,
            verdict=report.verdict.value,
            steps=len(report.steps),
            findings=len(report.findings),
            duration_ms=round((time.monotonic_ns() - started_ns) / 1_000_000, 3),
        )
    return report


def _execute_browser_run(report: Report) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth  # type: ignore[import-untyped]

    target = ValidatedTarget(
        url=report.target_url,
        hostname=report.target_hostname,
        allowed_hosts=frozenset(report.allowed_hosts),
        addresses={key: tuple(value) for key, value in report.resolved_addresses.items()},
    )
    deadline = time.monotonic() + int(report.limit_s)
    cfg = ConfigManager().sentinel

    with sync_playwright() as playwright:
        resolver_rules = [
            f"MAP {hostname} {_chromium_resolver_replacement(addresses[0])}"
            for hostname, addresses in target.addresses.items()
            if addresses
        ]
        launch_args = list(cfg.browser_launch_args)
        if resolver_rules:
            launch_args.append(f"--host-resolver-rules={','.join(resolver_rules)}")
        browser = playwright.chromium.launch(
            headless=True,
            args=launch_args,
            timeout=min(
                capped_timeout_ms(cfg.browser_launch_timeout_ms),
                max(1, int((deadline - time.monotonic()) * 1000) - 1000),
            ),
        )
        context = None
        try:
            device_key = str(report.device or cfg.default_device)
            profile_name = cfg.device_profiles.get(device_key, "")
            context_kwargs: dict[str, Any] = {
                "ignore_https_errors": cfg.ignore_https_errors,
                "service_workers": "block",
            }
            if profile_name:
                context_kwargs.update(playwright.devices[profile_name])
            else:
                context_kwargs["viewport"] = {
                    "width": cfg.browser_width_px,
                    "height": cfg.browser_height_px,
                }
                context_kwargs["user_agent"] = cfg.browser_desktop_user_agent
            context = browser.new_context(**context_kwargs)
            Stealth().apply_stealth_sync(context)
            allowed_hosts = {host.lower().rstrip(".") for host in target.allowed_hosts}

            def url_allowed(raw_url: str) -> bool:
                try:
                    parsed = urlparse(raw_url)
                except ValueError:
                    return False
                return parsed.scheme in {"http", "https", "ws", "wss"} and bool(
                    parsed.username is None
                    and parsed.password is None
                    and parsed.hostname
                    and parsed.hostname.lower().rstrip(".") in allowed_hosts
                )

            def guard_route(route: Any) -> None:
                if url_allowed(route.request.url):
                    route.continue_()
                else:
                    route.abort()

            def guard_websocket(route: Any) -> None:
                if url_allowed(route.url):
                    route.connect_to_server()
                else:
                    route.close(code=1008, reason="Host is outside Sentinel's allowlist")

            # Hooks must exist before the first page is created. This prevents
            # initial-document, subresource, and WebSocket races.
            context.route("**/*", guard_route)
            context.route_web_socket("**/*", guard_websocket)

            page = context.new_page()
            page.set_default_timeout(capped_timeout_ms(cfg.browser_default_timeout_ms))
            page.on(
                "console",
                lambda msg: _add_finding(
                    report,
                    "info",
                    cfg.console_finding_title,
                    msg.text,
                    kind=cfg.console_finding_kind,
                ),
            )
            page.on("pageerror", lambda err: _add_finding(report, "error", "Page error", str(err)))
            _register_network_findings(page, report)

            allow_external = bool(report.allow_external)
            _renew_execution(report)
            initial_nav = _goto_page(page, target.url, target)
            if not initial_nav.get("ok"):
                raise RuntimeError(initial_nav.get("error", "Initial navigation failed"))
            if initial_nav.get("warning"):
                _add_finding(report, "warning", "Slow page load", initial_nav["warning"])
            final_hostname = urlparse(page.url).hostname or ""
            if final_hostname.lower().rstrip(".") not in allowed_hosts:
                raise RuntimeError("Initial navigation redirected outside target host")

            # step-00.png: initial page state, before any agent action.
            _capture_screenshot(page, report, 0)

            while time.monotonic() < deadline and len(report.steps) < cfg.max_steps:
                _renew_execution(report)
                if _is_cancelled(report.run_id):
                    break
                next_step_index = len(report.steps) + 1
                observation = _observe_page(page)
                known_ids = {item["id"] for item in observation["elements"]}
                # The screenshot the agent reasons about is the *current* page
                # state — i.e. step-(N-1).png, the post-action result of the
                # prior step (or step-00 on the first iteration).
                current_screenshot = f"screenshots/step-{(next_step_index - 1):02d}.png"
                annotated = _capture_annotated_screenshot(
                    report, current_screenshot, observation, next_step_index - 1
                )
                observation["screenshot"] = current_screenshot

                image_bytes = _annotated_image_bytes(report, annotated, current_screenshot)
                action = _request_agent_action(
                    report, observation, image_bytes, allow_external, known_ids
                )
                if action is None:
                    break
                if _is_cancelled(report.run_id):
                    break
                if _finish_requires_more_scroll(report, action, observation):
                    action = AgentAction(
                        action="scroll",
                        value="down",
                        reason=(
                            "finish deferred: the prompt asks for full-page coverage and this page "
                            "still has unseen content below"
                        ),
                    )

                result = _apply_action(page, action, target)
                # Set/clear the peek-pending flag so _annotated_image_bytes
                # knows whether to attach the raw screenshot next iteration.
                report._peek_pending = action.action == "peek"
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
                _set_execution_state(report, ExecutionStatus.TIMED_OUT)
        except PlaywrightTimeoutError as e:
            _add_finding(report, "error", "Browser timeout", str(e)[:500])
            _set_execution_state(report, ExecutionStatus.TIMED_OUT)
        finally:
            if context is not None:
                context.close()
            browser.close()


def _goto_page(
    page: Any,
    url: str,
    target: ValidatedTarget,
) -> dict[str, Any]:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    cfg = ConfigManager().sentinel
    try:
        parsed = urlparse(url)
    except ValueError:
        return {
            "ok": False,
            "error": "Navigation outside target host blocked",
            "url": getattr(page, "url", ""),
        }
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or hostname not in target.allowed_hosts
    ):
        return {
            "ok": False,
            "error": "Navigation outside target host blocked",
            "url": getattr(page, "url", ""),
        }
    page.goto(url, wait_until="commit", timeout=capped_timeout_ms(cfg.navigation_timeout_ms))
    try:
        page.wait_for_load_state(
            "domcontentloaded", timeout=capped_timeout_ms(cfg.navigation_timeout_ms)
        )
    except PlaywrightTimeoutError:
        return {"ok": True, "warning": "Timed out waiting for DOMContentLoaded", "url": page.url}
    return {"ok": True, "url": page.url}


def _clean_title(text: str) -> str:
    cfg = ConfigManager().sentinel
    text = " ".join(str(text or "").split()).strip().strip('"').strip("'")
    if len(text) > cfg.title_max_chars:
        text = text[: cfg.title_max_chars].rstrip()
    return text


def _chromium_resolver_replacement(address: str) -> str:
    """Format a pinned address for Chromium's host-resolver rules."""
    return f"[{address}]" if ":" in address else address


def _generate_title(target_url: str, prompt: str, target_hostname: str = "") -> str:
    payload = json.dumps(
        {
            "target_url": redact_text(target_url),
            "user_prompt": redact_text(
                prompt or "Explore and test the site's main unauthenticated flows."
            ),
        },
        indent=2,
    )
    try:
        generated = redact_text(_get_provider().title_text(payload))
        return _clean_title(generated) or _fallback_title(target_url, target_hostname)
    except Exception as e:
        emit_event(
            "sentinel.title_generation_failed",
            error=e,
            error_type=type(e).__name__,
        )
        return _fallback_title(target_url, target_hostname)


def _fallback_title(target_url: str, target_hostname: str = "") -> str:
    return _clean_title(target_hostname or target_url or "Sentinel run")


def _add_finding(
    report: Report,
    severity: str,
    title: str,
    detail: str,
    *,
    kind: str = "",
    url: str = "",
    method: str = "",
    status_code: int | None = None,
) -> None:
    max_chars = ConfigManager().sentinel.finding_detail_max_chars
    detail = " ".join(str(detail).split())
    if len(detail) > max_chars:
        detail = f"{detail[:max_chars].rstrip()}..."
    report.findings.append(
        Finding(
            severity=severity,
            title=title,
            detail=detail,
            kind=kind,
            url=url,
            method=method,
            status_code=status_code,
        )
    )


def _origin(parsed: Any) -> tuple[str, str, int | None]:
    scheme = parsed.scheme.lower()
    port = parsed.port
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return scheme, (parsed.hostname or "").lower().rstrip("."), port


def _first_party_evidence_url(raw_url: str, target_url: str) -> str | None:
    """Return a credential/query-free first-party URL for persisted evidence."""
    try:
        parsed = urlparse(str(raw_url or ""))
        target = urlparse(str(target_url or ""))
        hostname = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or _origin(parsed) != _origin(target):
            return None
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return None
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{parsed.scheme}://{rendered_host}{port}{parsed.path or '/'}"


def _register_network_findings(page: Any, report: Report) -> None:
    """Capture actionable first-party network failures without third-party noise."""

    def on_request_failed(request: Any) -> None:
        evidence_url = _first_party_evidence_url(request.url, report.target_url)
        if evidence_url is None:
            return
        method = str(getattr(request, "method", "") or "").upper()
        failure = str(getattr(request, "failure", "") or "Request failed")
        _add_finding(
            report,
            "error",
            "First-party request failed",
            f"{method or 'REQUEST'} {evidence_url}: {failure}",
            kind="network.request_failed",
            url=evidence_url,
            method=method,
        )

    def on_response(response: Any) -> None:
        try:
            status_code = int(response.status)
        except (TypeError, ValueError):
            return
        if status_code < 500:
            return
        request = response.request
        evidence_url = _first_party_evidence_url(request.url, report.target_url)
        if evidence_url is None:
            return
        method = str(getattr(request, "method", "") or "").upper()
        _add_finding(
            report,
            "error",
            "First-party server error",
            f"{method or 'REQUEST'} {evidence_url} returned HTTP {status_code}.",
            kind="network.http_5xx",
            url=evidence_url,
            method=method,
            status_code=status_code,
        )

    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)


def _classify_run_verdict(report: Report) -> RunVerdict:
    """Ask the LLM whether the run actually fulfilled the user's prompt.

    Provider errors and malformed output are explicitly inconclusive rather
    than silently passing the run.
    """
    remaining = remaining_request_seconds()
    if remaining is not None and remaining <= 2:
        report.verdict_reason_code = "deadline_budget_exhausted"
        report.verdict_reason = "There was not enough request time to classify the QA verdict."
        return RunVerdict.INCONCLUSIVE
    try:
        raw = _get_provider().verdict_text(_verdict_prompt(report))
        parsed = _parse_verdict_payload(raw)
    except Exception as e:
        emit_event(
            "sentinel.verdict_classification_failed",
            owner=report.owner or None,
            run_id=report.run_id,
            error=e,
            error_type=type(e).__name__,
        )
        report.verdict_reason_code = "verdict_provider_error"
        report.verdict_reason = "The QA verdict provider could not classify the run."
        report.verdict = RunVerdict.INCONCLUSIVE
        return RunVerdict.INCONCLUSIVE
    if not parsed:
        emit_event(
            "sentinel.verdict_response_invalid",
            owner=report.owner or None,
            run_id=report.run_id,
        )
        report.verdict_reason_code = "invalid_verdict_response"
        report.verdict_reason = "The QA verdict provider returned an invalid response."
        report.verdict = RunVerdict.INCONCLUSIVE
        return RunVerdict.INCONCLUSIVE
    verdict, reason = parsed
    report.verdict_reason_code = f"qa_{verdict}"
    if verdict == "fail":
        report.verdict_reason = reason[: ConfigManager().sentinel.verdict_reason_max_chars]
        _add_finding(report, "warning", "Run did not fulfill prompt", reason)
        report.verdict = RunVerdict.FAIL
        return RunVerdict.FAIL
    report.verdict = RunVerdict.PASS
    return RunVerdict.PASS


def _verdict_prompt(report: Report) -> str:
    cfg = ConfigManager().sentinel
    payload = {
        "original_prompt": redact_text(report.prompt or ""),
        "target_url": redact_text(report.target_url),
        "allow_accounts": bool(report.allow_accounts),
        "allow_external": bool(report.allow_external),
        "additional_domains": [redact_text(item) for item in report.additional_domains],
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
            if finding.kind != cfg.console_finding_kind
            and finding.title != cfg.console_finding_title
        ],
        "final_report": redact_text(report.final_report or ""),
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
    reason = redact_text(str(data.get("reason", "")).strip()) or "No reason provided."
    return verdict, reason


def _add_final_report(report: Report) -> None:
    remaining = remaining_request_seconds()
    if remaining is not None and remaining <= 5:
        report.final_report = _ensure_summary_heading(_fallback_final_report(report))
        _save(report)
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
    _save(report)


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


def _capture_screenshot(page: Any, report: Report, index: int) -> str | None:
    """Capture a screenshot at the given step index (0 = initial state, N = after step N).

    The screenshots list is treated as ordered by index — duplicate indices
    overwrite the existing entry rather than appending.
    """
    cfg = ConfigManager().sentinel
    if len(report.screenshots) >= cfg.max_screenshots:
        return None
    filename = f"step-{index:02d}.png"
    value = page.screenshot(full_page=False)
    if not isinstance(value, bytes):
        raise RuntimeError("Playwright did not return screenshot bytes")
    rel = DataInterface().write_screenshot(report.run_id, filename, value)
    ensure_screenshot_thumbnail(report.run_id, filename)
    if rel not in report.screenshots:
        report.screenshots.append(rel)
    return rel


def ensure_screenshot_thumbnail(run_id: str, filename: str) -> bytes | None:
    if not re.match(r"^step-\d{2}(?:-annot)?\.png$", filename):
        return None
    data = DataInterface()
    existing = data.read_thumbnail(run_id, filename)
    if existing is not None:
        return existing
    source = data.read_screenshot(run_id, filename)
    if source is None:
        return None
    try:
        from PIL import Image
    except Exception as error:
        emit_event(
            "sentinel.thumbnail_skipped",
            run_id=run_id,
            filename=filename,
            reason="pillow_unavailable",
            error_type=type(error).__name__,
        )
        return None

    try:
        max_px = ConfigManager().sentinel.screenshot_thumb_max_px
        with Image.open(io.BytesIO(source)) as img:
            img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
            thumb = img.copy()
        output = io.BytesIO()
        thumb.save(output, format="PNG", optimize=True)
        value = output.getvalue()
        data.write_thumbnail(run_id, filename, value)
    except Exception as e:
        emit_event(
            "sentinel.thumbnail_create_failed",
            run_id=run_id,
            filename=filename,
            error=e,
            error_type=type(e).__name__,
        )
        return None
    return value


def _screenshot_image_bytes(report: Report, screenshot: str | None) -> list[bytes]:
    if not screenshot:
        return []
    filename = Path(screenshot).name
    value = DataInterface().read_screenshot(report.run_id, filename)
    return [value] if value is not None else []


def _capture_annotated_screenshot(
    report: Report,
    screenshot: str | None,
    observation: dict[str, Any],
    index: int,
) -> str | None:
    if not screenshot:
        return None
    raw_filename = Path(screenshot).name
    raw = DataInterface().read_screenshot(report.run_id, raw_filename)
    if raw is None:
        return None
    filename = f"step-{index:02d}-annot.png"
    written = _annotate_screenshot(
        raw,
        observation.get("elements") or [],
        observation.get("viewport"),
    )
    if written is None:
        return None
    rel = DataInterface().write_screenshot(report.run_id, filename, written)
    ensure_screenshot_thumbnail(report.run_id, filename)
    if rel not in report.annotated_screenshots:
        report.annotated_screenshots.append(rel)
    return rel


def _annotated_image_bytes(report: Report, annotated: str | None, raw: str | None) -> list[bytes]:
    if annotated:
        value = DataInterface().read_screenshot(report.run_id, Path(annotated).name)
        if value is not None:
            images = [value]
            # If the prior step was 'peek', also attach the raw (un-annotated)
            # screenshot so the model can see ui obscured by annotation boxes.
            if report._peek_pending and raw:
                raw_value = DataInterface().read_screenshot(report.run_id, Path(raw).name)
                if raw_value is not None:
                    images.append(raw_value)
            return images
    return _screenshot_image_bytes(report, raw)


def _annotate_screenshot(
    raw: bytes,
    elements: list[dict[str, Any]],
    viewport: dict[str, Any] | None,
) -> bytes | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as error:
        emit_event(
            "sentinel.annotation_skipped",
            reason="pillow_unavailable",
            error_type=type(error).__name__,
        )
        return None
    cfg = ConfigManager().sentinel
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:
        emit_event(
            "sentinel.screenshot_open_failed",
            error=e,
            error_type=type(e).__name__,
        )
        return None

    img_w, img_h = img.size
    vp_w = float((viewport or {}).get("w") or img_w)
    vp_h = float((viewport or {}).get("h") or img_h)
    sx = img_w / vp_w if vp_w else 1.0
    sy = img_h / vp_h if vp_h else 1.0

    draw = ImageDraw.Draw(img, "RGBA")
    font: Any
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", cfg.annotation_label_font_px)
    except Exception:
        font = ImageFont.load_default()

    box_w = max(1, int(cfg.annotation_box_width_px))
    pad = max(1, int(cfg.annotation_label_pad_px))

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

        color = cfg.annotation_palette[idx % len(cfg.annotation_palette)]
        draw.rectangle([x1, y1, x2, y2], outline=(*color, 255), width=box_w)

        label = str(el.get("id") or "")
        if not label:
            continue
        tb = draw.textbbox((0, 0), label, font=font)
        text_w, text_h = tb[2] - tb[0], tb[3] - tb[1]

        lx2 = x1 + text_w + pad * 2
        ly2 = y1 + text_h + pad * 2
        draw.rectangle([x1, y1, lx2, ly2], fill=(*color, 230))
        draw.text((x1 + pad, y1 + pad), label, fill=(255, 255, 255, 255), font=font)

    try:
        output = io.BytesIO()
        img.save(output, format="PNG")
    except Exception as e:
        emit_event(
            "sentinel.annotation_save_failed",
            error=e,
            error_type=type(e).__name__,
        )
        return None
    return output.getvalue()


def _observe_page(page: Any) -> dict[str, Any]:
    cfg = ConfigManager().sentinel
    result = page.evaluate(
        """
        ({ maxElements, maxTextChars, maxElementTextChars, scrollTolerancePx }) => {
          const rectOf = (el) => {
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height};
          };
          const SELECTOR = (
            'a,button,input,textarea,select,'
            + '[role="button"],[role="link"],[role="menuitem"],[role="tab"],[role="checkbox"],[role="radio"],'
            + '[onclick],[tabindex]:not([tabindex="-1"])'
          );
          // Walk a root (document or shadowRoot) and collect candidates that
          // match SELECTOR, descending into any open shadow roots we find.
          // Closed shadow roots are unreachable from JS and silently skipped.
          const walk = (root, out, hosts) => {
            for (const el of root.querySelectorAll(SELECTOR)) out.push(el);
            for (const el of root.querySelectorAll('*')) {
              if (el.shadowRoot) {
                hosts.set(el.shadowRoot, el);
                walk(el.shadowRoot, out, hosts);
              }
            }
          };
          // shadowRoot.elementFromPoint(x,y) descends one level; chain it so
          // the topmost element returned is the deepest visible one.
          const deepElementFromPoint = (x, y) => {
            let el = document.elementFromPoint(x, y);
            while (el && el.shadowRoot) {
              const inner = el.shadowRoot.elementFromPoint(x, y);
              if (!inner || inner === el) break;
              el = inner;
            }
            return el;
          };
          // Clear ids set by previous observations, including those stamped
          // inside open shadow roots.
          const clearRoot = (root) => {
            for (const el of root.querySelectorAll('[data-sentinel-id]')) el.removeAttribute('data-sentinel-id');
            for (const el of root.querySelectorAll('*')) if (el.shadowRoot) clearRoot(el.shadowRoot);
          };
          clearRoot(document);

          const usable = (el) => {
            const r = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (r.width <= 0 || r.height <= 0) return false;
            if (r.bottom <= 0 || r.right <= 0 || r.top >= window.innerHeight || r.left >= window.innerWidth) return false;
            if (style.visibility === 'hidden' || style.display === 'none' || style.pointerEvents === 'none') return false;
            if (el.disabled || el.getAttribute('aria-hidden') === 'true' || el.closest('[aria-hidden="true"],[inert]')) return false;
            const cx = Math.min(Math.max(r.left + r.width / 2, 0), window.innerWidth - 1);
            const cy = Math.min(Math.max(r.top + r.height / 2, 0), window.innerHeight - 1);
            const top = deepElementFromPoint(cx, cy);
            return Boolean(top && (el === top || el.contains(top) || (top.getRootNode && top.getRootNode().host && el.contains(top.getRootNode().host))));
          };

          const candidates = [];
          const hosts = new Map();  // shadowRoot -> host element (currently unused but reserved)
          walk(document, candidates, hosts);
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
              text: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.href || '').trim().slice(0, maxElementTextChars),
              href: el.href || '',
              rect: rectOf(el)
            });
          }
          const bodyText = (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, maxTextChars);
          const scrollingEl = document.scrollingElement || document.documentElement;
          const scrollY = Math.max(0, window.scrollY || scrollingEl.scrollTop || 0);
          const scrollHeight = Math.max(
            scrollingEl.scrollHeight || 0,
            document.documentElement ? document.documentElement.scrollHeight : 0,
            document.body ? document.body.scrollHeight : 0
          );
          const screenHeight = window.innerHeight || scrollingEl.clientHeight || 0;
          const maxY = Math.max(0, scrollHeight - screenHeight);
          const canScrollDown = scrollY < maxY - scrollTolerancePx;
          return {
            url: location.href,
            title: document.title,
            text: bodyText,
            elements,
            viewport: {w: window.innerWidth, h: window.innerHeight},
            scroll: {
              y: Math.round(scrollY),
              max_y: Math.round(maxY),
              can_scroll_down: canScrollDown,
              can_scroll_up: scrollY > scrollTolerancePx,
              at_bottom: !canScrollDown
            }
          };
        }
        """,
        {
            "maxElements": cfg.observation_max_elements,
            "maxTextChars": cfg.observation_text_max_chars,
            "maxElementTextChars": cfg.observation_element_text_max_chars,
            "scrollTolerancePx": cfg.scroll_position_tolerance_px,
        },
    )
    if not isinstance(result, dict):
        raise RuntimeError("Playwright returned an invalid page observation")
    return cast(dict[str, Any], result)


def _agent_prompt(report: Report, observation: dict[str, Any]) -> str:
    history = [
        {
            "action": step.action,
            "reason": redact_text(step.reason),
            "result": _redact_dynamic_values(step.result.model_dump(exclude_none=True)),
        }
        for step in report.steps[-6:]
    ]
    elements = [
        {
            "id": el.get("id", ""),
            "tag": el.get("tag", ""),
            "type": el.get("type", ""),
            "label": redact_text(str(el.get("text", ""))),
        }
        for el in (observation.get("elements") or [])
    ]
    hints = [
        f.detail
        for f in report.findings
        if f.severity in {"warning", "error"} and f.title == "Repeated click with no navigation"
    ][-1:]
    payload = {
        "target_url": redact_text(report.target_url),
        "user_prompt": redact_text(
            report.prompt or "Explore and test the site's main unauthenticated flows."
        ),
        "history": history,
        "page": {
            "url": redact_text(str(observation.get("url", ""))),
            "title": redact_text(str(observation.get("title", ""))),
            "elements": elements,
            "scroll": observation.get("scroll") or {},
        },
        "additional_domains": list(report.additional_domains or []),
        "instructions": (
            "The attached screenshot shows the page with each interactive element outlined and "
            "labelled with a synthetic id (e.g. e1, e2). Use the screenshot as your primary input "
            "and choose elements visually. The 'elements' list is only a key for resolving labels "
            "to ids; do not rely on it for spatial layout. If additional_domains is non-empty, "
            "external navigation is permitted only to those domains; other external domains are blocked. "
            "If the annotation boxes are obscuring text you need to read, or you think a clickable "
            "element is missing from the elements list, use the peek action — the next step will "
            "include a clean un-annotated copy of the same screenshot. When the user asks you to "
            "check all items, apps, links, cards, rows, or sections on a page, use scroll to inspect "
            "below the current screen before finishing. Do not assume the visible screen is the "
            "whole page."
        ),
    }
    if hints:
        payload["hints"] = [redact_text(hint) for hint in hints]
    return json.dumps(payload, indent=2)


def _full_page_coverage_requested(prompt: str | None) -> bool:
    pattern = ConfigManager().sentinel.full_page_scope_prompt_pattern
    return bool(re.search(pattern, prompt or "", re.IGNORECASE))


def _finish_requires_more_scroll(
    report: Report, action: AgentAction, observation: dict[str, Any]
) -> bool:
    if action.action != "finish" or not _full_page_coverage_requested(report.prompt):
        return False
    scroll = observation.get("scroll") or {}
    return bool(scroll.get("can_scroll_down"))


def _apply_action(
    page: Any,
    action: AgentAction,
    target: ValidatedTarget,
) -> dict[str, Any]:
    cfg = ConfigManager().sentinel
    try:
        if action.action == "finish":
            return {"ok": True, "url": page.url}
        if action.action == "peek":
            # No-op on the page; the runner re-attaches the raw screenshot to
            # the *next* agent call so the model can see ui obscured by the
            # annotation overlay.
            return {"ok": True, "url": page.url}
        if action.action == "wait":
            page.wait_for_timeout(capped_timeout_ms(cfg.wait_action_ms, reserve_ms=0))
            return {"ok": True, "url": page.url}
        if action.action == "scroll":
            delta = (
                -cfg.scroll_action_delta_px
                if (action.value or "").lower() == "up"
                else cfg.scroll_action_delta_px
            )
            page.mouse.wheel(0, delta)
            page.wait_for_timeout(capped_timeout_ms(cfg.post_scroll_settle_ms, reserve_ms=0))
            return {"ok": True, "url": page.url}
        if action.action == "goto":
            next_url = urljoin(page.url, action.url or target.url)
            return _goto_page(page, next_url, target)

        locator = page.locator(f'[data-sentinel-id="{action.element_id}"]').first
        if action.action == "click":
            blocked_url = _blocked_external_click_url(page, locator, target)
            if blocked_url:
                return {
                    "ok": False,
                    "error": "Navigation outside target host blocked",
                    "url": page.url,
                    "blocked_url": blocked_url,
                }
            locator.click()
            with suppress(Exception):
                page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=capped_timeout_ms(cfg.post_click_load_timeout_ms),
                )
            page.wait_for_timeout(capped_timeout_ms(cfg.post_click_settle_ms, reserve_ms=0))
        elif action.action == "fill":
            locator.fill(current_secrets().resolve(action.value or "test"))
            page.wait_for_timeout(capped_timeout_ms(cfg.post_fill_settle_ms, reserve_ms=0))
        elif action.action == "select":
            locator.select_option(label=current_secrets().resolve(action.value or ""))
            page.wait_for_timeout(capped_timeout_ms(cfg.post_select_settle_ms, reserve_ms=0))
        return {"ok": True, "url": page.url}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": getattr(page, "url", "")}


def _blocked_external_click_url(
    page: Any,
    locator: Any,
    target: ValidatedTarget,
) -> str:
    href = locator.evaluate(
        """
        (el) => {
          const anchor = el.closest ? el.closest('a[href]') : null;
          if (anchor) return anchor.href || '';
          const form = el.closest ? el.closest('form[action]') : null;
          if (form) return form.action || '';
          return '';
        }
        """
    )
    parsed = urlparse(urljoin(str(page.url), str(href or "")))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in target.allowed_hosts:
        return ""
    return parsed.geturl()


def _record_step(report: Report, action: str, reason: str, result: dict[str, Any]) -> None:
    report.steps.append(
        Step(
            index=len(report.steps) + 1,
            action=action,
            reason=reason,
            result=ActionResult.model_validate(result),
            created_at=utc_now_iso(),
        )
    )


def _request_agent_action(
    report: Report,
    observation: dict[str, Any],
    image_bytes: list[bytes],
    allow_external: bool,
    known_ids: set[str],
) -> AgentAction | None:
    """Ask the LLM for the next action, retrying once on a parse failure.

    Returns the parsed AgentAction, or None if the run should abort. On abort,
    an ``invalid`` step has already been recorded and a finding added.
    """
    cfg = ConfigManager().sentinel
    secrets = current_secrets()
    attempts = max(1, cfg.agent_parse_retry_attempts + 1)
    last_error: ActionValidationError | None = None
    last_text = ""
    for attempt in range(attempts):
        agent_text = _get_provider().agent_text(
            _agent_prompt(report, observation),
            image_bytes=image_bytes,
            allow_accounts=bool(report.allow_accounts),
            demographic=str(report.demographic or ""),
            allow_external=allow_external or bool(report.additional_domains),
            card_fields=secrets.card_fields,
            account_fields=secrets.account_fields,
        )
        try:
            action = parse_agent_action(agent_text, known_ids)
            value = action.value
            if value not in secrets.placeholders:
                value = redact_text(value) if value is not None else None
            return AgentAction(
                action=action.action,
                reason=redact_text(action.reason),
                element_id=action.element_id,
                value=value,
                url=redact_text(action.url) if action.url is not None else None,
            )
        except ActionValidationError as e:
            last_error = e
            last_text = redact_text(agent_text)
            if attempt + 1 < attempts:
                emit_event(
                    "sentinel.agent_parse_retry",
                    owner=report.owner or None,
                    run_id=report.run_id,
                    attempt=attempt + 1,
                    reason="invalid_agent_output",
                    error_type=type(e).__name__,
                )
    _record_step(report, "invalid", str(last_error), {"agent_text": last_text})
    _add_finding(report, "warning", "Agent response unparseable", str(last_error))
    return None


_LOGIN_FAIL_PREFIX = "login failed:"


def _detect_login_failure(report: Report) -> str:
    """If the agent finished with a 'login failed:' marker, return the reason."""
    if not report.allow_accounts or not report.steps:
        return ""
    last = report.steps[-1]
    if last.action != "finish":
        return ""
    reason = str(last.reason or "").strip()
    if reason.lower().startswith(_LOGIN_FAIL_PREFIX):
        return reason
    return ""


def _detect_click_loop(report: Report) -> bool:
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
    steps = report.steps
    if len(steps) < threshold:
        return False
    tail = steps[-threshold:]
    if not all(s.action == "click" for s in tail):
        return False
    urls = {s.result.url for s in tail}
    if len(urls) != 1:
        return False
    reasons = {(s.reason or "")[:60] for s in tail}
    last_step = tail[-1].index
    # Suppress if we already flagged a loop ending at this same step count.
    for finding in report.findings:
        if (
            finding.title == "Repeated click with no navigation"
            and str(last_step) in finding.detail
        ):
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
        1 for f in report.findings if f.title == "Repeated click with no navigation"
    )
    return cfg.click_loop_max_warnings > 0 and loop_warnings >= cfg.click_loop_max_warnings
