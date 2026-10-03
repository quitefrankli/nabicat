from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from markdown_it import MarkdownIt
from markupsafe import Markup

from .data_interface import DataInterface
from .models import Report
from .runner import (
    RunBusyError,
    delete_run,
    ensure_screenshot_thumbnail,
    get_run,
    render_report_pdf,
    request_cancel,
    start_run,
)
from .runtime import emit_event
from .secrets import build_secrets, contains_secret, secret_scope
from .target_policy import TargetValidationError, ValidatedTarget, validate_public_web_url
from web_app.config import ConfigManager
from flask_login import current_user

_SCREENSHOT_FILENAME_RE = re.compile(r"^step-\d{2}(?:-annot)?\.png$")
_TRANSPARENT_GIF_DATA_URI = "data:image/gif;base64,R0lGODlhAQABAAAAACw="


@dataclass(frozen=True, slots=True)
class RunParameters:
    target: ValidatedTarget
    prompt: str
    limit_s: int
    title: str
    allow_accounts: bool
    allow_external: bool
    additional_domains: list[str]
    allow_financial: bool
    card_details: dict[str, str] | None
    account_credentials: dict[str, object] | None
    device: str
    demographic: str


def _detect_account_keyword(prompt: str, keywords: tuple[str, ...]) -> str:
    text = prompt.lower()
    for keyword in keywords:
        if re.search(rf"(?<!\w){re.escape(keyword.lower())}(?!\w)", text):
            return keyword
    return ""


def _validate_account_credentials(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    username = str(raw.get("username", "")).strip()
    password = str(raw.get("password", ""))
    raw_extras = raw.get("extras") or {}
    if not isinstance(raw_extras, dict):
        raise ValueError("Account credentials extras must be a JSON object.")
    extras: dict[str, str] = {}
    for key, value in raw_extras.items():
        clean_key = str(key).strip()
        if not clean_key:
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,39}", clean_key) is None:
            raise ValueError(
                f"Account credential field name {clean_key!r} is invalid. "
                "Use letters, numbers, spaces, hyphens, or underscores."
            )
        extras[clean_key] = str(value)
    if not username and not password and not extras:
        return None
    if bool(username) ^ bool(password):
        raise ValueError("Provide both username and password, or leave both blank.")
    return {"username": username, "password": password, "extras": extras}


def _validate_card_details(payload: object) -> dict[str, str]:
    if not hasattr(payload, "get"):
        raise ValueError("Card details are invalid.")
    raw_number = str(payload.get("card_number", ""))
    raw_expiry = str(payload.get("card_expiry", "")).strip()
    raw_cvv = str(payload.get("card_cvv", ""))
    digits = "".join(character for character in raw_number if character.isdigit())
    if not 13 <= len(digits) <= 19:
        raise ValueError("Card number must be 13-19 digits.")
    if re.fullmatch(r"\d{2}\s*/\s*\d{2}", raw_expiry) is None:
        raise ValueError("Expiry must be in MM/YY format.")
    cvv_digits = "".join(character for character in raw_cvv if character.isdigit())
    if not 3 <= len(cvv_digits) <= 4:
        raise ValueError("CVV must be 3 or 4 digits.")
    month, year = [part.strip() for part in raw_expiry.split("/")]
    if not 1 <= int(month) <= 12:
        raise ValueError("Expiry month must be between 01 and 12.")
    return {"card_number": digits, "expiry": f"{month}/{year}", "cvv": cvv_digits}


def _derive_batches(reports: list[Report], max_n: int | None = None) -> list[dict[str, object]]:
    groups: dict[str, dict[str, object]] = {}
    for run in reports:
        if not run.batch_id:
            continue
        group = groups.setdefault(
            run.batch_id,
            {
                "batch_id": run.batch_id,
                "name": run.batch_label or run.batch_id,
                "owner": run.owner,
                "created_at": run.created_at,
                "items": [],
            },
        )
        items = group["items"]
        assert isinstance(items, list)
        items.append(run)
        if run.created_at and run.created_at < str(group["created_at"]):
            group["created_at"] = run.created_at
    result = list(groups.values())
    return result[:max_n] if max_n is not None else result


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _limit_from_request(raw_limit: object, config) -> int:
    cfg = config
    try:
        minutes = int(str(raw_limit)) if raw_limit not in (None, "") else cfg.default_limit_mins
    except (TypeError, ValueError):
        minutes = cfg.default_limit_mins
    return max(cfg.min_limit_mins, min(minutes, cfg.max_limit_mins)) * 60


def _validate_additional_domains(raw: object, config) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        values: list[object] = list(re.split(r"[\s,]+", raw))
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        raise ValueError("Additional domains must be separated by newlines or commas.")
    domains: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        if len(item) > config.additional_domain_max_chars:
            raise ValueError("Additional domain is too long.")
        candidate = item if "://" in item else f"https://{item}"
        parsed = urlparse(candidate)
        if parsed.username is not None or parsed.password is not None or parsed.hostname is None:
            raise ValueError(f"Additional domain {item!r} is invalid.")
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname not in domains:
            domains.append(hostname)
        if len(domains) > config.additional_domains_max_count:
            raise ValueError(
                f"Additional domains are limited to {config.additional_domains_max_count}."
            )
    return domains


def _validate_run_params(payload: object, config) -> RunParameters:
    if not hasattr(payload, "get"):
        raise ValueError("Run payload must be an object.")
    cfg = config
    raw_url = str(payload.get("url", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()[: cfg.prompt_max_chars]
    title = str(payload.get("title", "")).strip()[: cfg.title_max_chars]
    allow_accounts = _truthy(payload.get("allow_accounts"))
    allow_external = _truthy(payload.get("allow_external"))
    allow_financial = _truthy(payload.get("allow_financial"))
    raw_additional_domains = payload.get("additional_domains")
    account_credentials = (
        _validate_account_credentials(payload.get("account_credentials"))
        if allow_accounts
        else None
    )
    card_details = _validate_card_details(payload) if allow_financial else None
    secrets = build_secrets(account_credentials, card_details)
    with secret_scope(secrets):
        raw_target_values = [raw_url]
        if isinstance(raw_additional_domains, str):
            raw_target_values.append(raw_additional_domains)
        elif isinstance(raw_additional_domains, (list, tuple)):
            raw_target_values.extend(str(value) for value in raw_additional_domains)
        if any(contains_secret(value) for value in raw_target_values):
            raise ValueError(
                "Target URL or allowed hostname must not contain supplied secret values."
            )
        additional_domains = (
            _validate_additional_domains(raw_additional_domains, cfg) if allow_external else []
        )
        target = validate_public_web_url(raw_url, additional_hosts=tuple(additional_domains))
    if not allow_accounts:
        keyword = _detect_account_keyword(prompt, cfg.account_keywords)
        if keyword:
            raise ValueError(
                f'Prompt mentions "{keyword}" but account operations are not permitted.'
            )
    return RunParameters(
        target=target,
        prompt=prompt,
        limit_s=_limit_from_request(payload.get("limit"), cfg),
        title=title,
        allow_accounts=allow_accounts,
        allow_external=allow_external,
        additional_domains=additional_domains,
        allow_financial=allow_financial,
        card_details=card_details,
        account_credentials=account_credentials,
        device=str(payload.get("device", "")).strip(),
        demographic=str(payload.get("demographic", "")).strip(),
    )


def _start_validated_run(
    parameters: RunParameters,
    *,
    owner: str = "",
    batch_id: str = "",
    batch_label: str = "",
) -> Report:
    return start_run(
        target=parameters.target,
        prompt=parameters.prompt,
        limit_s=parameters.limit_s,
        title=parameters.title,
        allow_accounts=parameters.allow_accounts,
        allow_external=parameters.allow_external,
        additional_domains=parameters.additional_domains,
        allow_financial=parameters.allow_financial,
        card_details=parameters.card_details,
        account_credentials=parameters.account_credentials,
        device=parameters.device,
        demographic=parameters.demographic,
        owner=owner,
        batch_id=batch_id,
        batch_label=batch_label,
    )


def _screenshot_url(run_id: str, filename: str, *, thumbnail: bool = False) -> str:
    suffix = f"/thumb/{filename}" if thumbnail else f"/{filename}"
    return f"/sentinel/report/{run_id}/screenshots{suffix}"


def _resolve_screenshot_src(
    src: str,
    run_id: str,
    allowed_filenames: set[str],
    *,
    thumbnail: bool = False,
) -> str | None:
    filename = src.rsplit("/", 1)[-1]
    if filename not in allowed_filenames or _SCREENSHOT_FILENAME_RE.fullmatch(filename) is None:
        return None
    return _screenshot_url(run_id, filename, thumbnail=thumbnail)


def _render_final_report(markdown_text: str, run_id: str, screenshots: list[str]) -> Markup:
    markdown = MarkdownIt("commonmark", {"html": False})
    renderer = cast(Any, markdown.renderer)
    allowed = {str(value).rsplit("/", 1)[-1] for value in screenshots}
    default_image = renderer.rules.get("image")

    def render_image(tokens: Any, index: int, options: Any, environment: Any) -> str:
        token = tokens[index]
        source = token.attrGet("src") or ""
        resolved = _resolve_screenshot_src(source, run_id, allowed, thumbnail=True)
        if resolved is None:
            return ""
        token.attrSet("src", _TRANSPARENT_GIF_DATA_URI)
        token.attrSet("data-screenshot-src", resolved)
        token.attrSet("data-full", _resolve_screenshot_src(source, run_id, allowed) or "")
        token.attrSet("loading", "lazy")
        token.attrSet("decoding", "async")
        token.attrSet("class", "sentinel-final-report-img")
        if default_image:
            return str(default_image(tokens, index, options, environment))
        return str(renderer.renderToken(tokens, index, options))

    renderer.rules["image"] = render_image
    return Markup(markdown.render(markdown_text or ""))


def _render_final_report_for_pdf(markdown_text: str, run_id: str, screenshots: list[str]) -> Markup:
    markdown = MarkdownIt("commonmark", {"html": False})
    renderer = cast(Any, markdown.renderer)
    allowed = {str(value).rsplit("/", 1)[-1] for value in screenshots}
    default_image = renderer.rules.get("image")

    def render_image(tokens: Any, index: int, options: Any, environment: Any) -> str:
        token = tokens[index]
        filename = (token.attrGet("src") or "").rsplit("/", 1)[-1]
        if filename not in allowed or _SCREENSHOT_FILENAME_RE.fullmatch(filename) is None:
            return ""
        value = DataInterface().read_screenshot(run_id, filename)
        if value is None:
            return ""
        token.attrSet("src", f"data:image/png;base64,{base64.b64encode(value).decode('ascii')}")
        token.attrSet("class", "sentinel-final-report-img")
        if default_image:
            return str(default_image(tokens, index, options, environment))
        return str(renderer.renderToken(tokens, index, options))

    renderer.rules["image"] = render_image
    return Markup(markdown.render(markdown_text or ""))


def _report_payload(report: Report, config) -> dict[str, object]:
    cfg = config
    payload = report.model_dump(mode="json")
    payload["final_report_html"] = str(
        _render_final_report(report.final_report, report.run_id, list(report.screenshots))
    )
    payload["screenshot_load_stagger_ms"] = cfg.screenshot_load_stagger_ms
    payload["screenshot_load_max_retries"] = cfg.screenshot_load_max_retries
    payload["screenshot_load_retry_delay_ms"] = cfg.screenshot_load_retry_delay_ms
    payload["device_label"] = cfg.device_labels.get(report.device, "")
    payload["demographic_label"] = cfg.demographic_labels.get(report.demographic, "")
    return payload


def _run_form_options(config) -> dict[str, object]:
    cfg = config
    return {
        "device_options": [(key, cfg.device_labels.get(key, key)) for key in cfg.device_profiles],
        "demographic_options": [
            (key, cfg.demographic_labels.get(key, key)) for key in cfg.demographic_personas
        ],
        "default_device": cfg.default_device,
        "default_demographic": cfg.default_demographic,
        "default_limit": cfg.default_limit_mins,
        "min_limit": cfg.min_limit_mins,
        "max_limit": cfg.max_limit_mins,
        "prompt_max_chars": cfg.prompt_max_chars,
        "title_max_chars": cfg.title_max_chars,
        "additional_domains_max_count": cfg.additional_domains_max_count,
    }


def _run_prefill(run_id: str) -> dict[str, object] | None:
    report = get_run(run_id)
    if report is None:
        return None
    return {
        "url": report.target_url,
        "prompt": report.prompt,
        "title": report.title,
        "limit": (report.limit_s or 0) // 60 or None,
        "allow_accounts": report.allow_accounts,
        "allow_external": report.allow_external,
        "allow_financial": report.allow_financial,
        "additional_domains": "\n".join(report.additional_domains),
        "device": report.device,
        "demographic": report.demographic,
    }


def _current_actor():
    return current_user if getattr(current_user, "is_authenticated", False) else None


def create_blueprint() -> Blueprint:
    config = ConfigManager().sentinel
    blueprint = Blueprint(
        "sentinel",
        __name__,
        template_folder="templates",
        static_folder="static",
        url_prefix="/sentinel",
    )

    @blueprint.before_request
    def require_elevated_access() -> Any:
        reason = None
        if not getattr(current_user, "is_authenticated", False):
            reason = "login_required"
        elif not current_user.has_elevated_access():
            reason = "elevated_required"
        if reason is not None:
            emit_event("sentinel.access_denied", reason=reason)
            if request.method != "GET" or request.path.startswith("/sentinel/api/"):
                abort(403)
            from flask import flash

            flash(ConfigManager().elevated_access_denied_message, category="error")
            return redirect(url_for(ConfigManager().access_denied_redirect_endpoint))
        return None

    @blueprint.context_processor
    def inject_sidebar() -> dict[str, object]:
        reports = DataInterface().list_reports()[: config.sidebar_run_limit]
        return {
            "app_name": "Sentinel",
            "sidebar_runs": reports,
            "sidebar_batches": _derive_batches(reports, config.sidebar_batch_limit),
        }

    @blueprint.get("/")
    def index() -> Any:
        if request.args:
            dynamic_url_for = cast(Any, url_for)
            return redirect(dynamic_url_for("sentinel.new_run", **request.args.to_dict(flat=True)))
        reports = DataInterface().list_reports()[: config.sidebar_run_limit]
        batches = _derive_batches(reports, config.sidebar_batch_limit)
        active = {"queued", "running", "summarizing"}
        return render_template(
            "sentinel_index.html",
            recent_runs=reports[:3],
            landing_stats={
                "total_runs": len(reports),
                "active_runs": sum(str(run.status) in active for run in reports),
                "completed_runs": sum(str(run.status) not in active for run in reports),
                "batches": len(batches),
            },
        )

    @blueprint.get("/run")
    def new_run() -> Any:
        source = str(request.args.get("from", "")).strip()
        return render_template(
            "sentinel_run.html",
            runs=DataInterface().list_reports()[: config.sidebar_run_limit],
            prefill_item=_run_prefill(source) if source else None,
            form_options=_run_form_options(config),
        )

    @blueprint.post("/api/runs")
    def create_run_route() -> Any:
        payload = request.get_json(silent=True) or request.form
        actor = _current_actor()
        try:
            parameters = _validate_run_params(payload, config)
            report = _start_validated_run(parameters, owner=actor.get_id() if actor else "")
        except (ValueError, TargetValidationError) as error:
            emit_event(
                "sentinel.run_rejected",
                actor=actor,
                reason="invalid_parameters",
                error_type=type(error).__name__,
            )
            return jsonify({"error": str(error)}), 400
        except RunBusyError as error:
            emit_event("sentinel.run_rejected", actor=actor, reason="execution_busy")
            return (
                jsonify({"error": str(error)}),
                409,
                {"Retry-After": str(config.lease_retry_after_s)},
            )
        emit_event(
            "sentinel.run_request_completed",
            actor=actor,
            run_id=report.run_id,
            status=report.status.value,
        )
        return jsonify(_report_payload(report, config)), 201

    @blueprint.get("/api/runs/<run_id>")
    def run_status(run_id: str) -> Any:
        report = get_run(run_id)
        if report is None:
            abort(404)
        return jsonify(_report_payload(report, config))

    @blueprint.post("/api/runs/<run_id>/cancel")
    def cancel(run_id: str) -> Any:
        report = get_run(run_id)
        if report is None:
            emit_event(
                "sentinel.run_cancel_rejected",
                actor=_current_actor(),
                run_id=run_id,
                reason="not_found",
            )
            abort(404)
        if str(report.status) not in {"queued", "running", "summarizing"}:
            emit_event(
                "sentinel.run_cancel_ignored",
                actor=_current_actor(),
                run_id=run_id,
                reason="terminal",
                status=str(report.status),
            )
            return jsonify({"run_id": run_id, "status": report.status, "cancelled": False})
        cancelled = request_cancel(run_id)
        emit_event(
            "sentinel.run_cancel_requested",
            actor=_current_actor(),
            run_id=run_id,
            cancelled=cancelled,
        )
        return jsonify({"run_id": run_id, "cancelled": cancelled})

    @blueprint.post("/api/runs/<run_id>/delete")
    def delete(run_id: str) -> Any:
        report = get_run(run_id)
        if report is None:
            emit_event(
                "sentinel.run_delete_rejected",
                actor=_current_actor(),
                run_id=run_id,
                reason="not_found",
            )
            abort(404)
        if str(report.status) in {"queued", "running", "summarizing"}:
            emit_event(
                "sentinel.run_delete_rejected",
                actor=_current_actor(),
                run_id=run_id,
                reason="active",
                status=str(report.status),
            )
            return jsonify({"error": "Run is still active"}), 409
        if not delete_run(run_id):
            emit_event(
                "sentinel.run_delete_failed",
                actor=_current_actor(),
                run_id=run_id,
                reason="storage_rejected",
            )
            abort(404)
        emit_event("sentinel.run_deleted", actor=_current_actor(), run_id=run_id)
        return jsonify({"run_id": run_id, "deleted": True})

    @blueprint.get("/batches")
    def batches_index() -> Any:
        source = str(request.args.get("from", "")).strip()
        prefill = None
        if source:
            summary = _batch_summary(source, DataInterface())
            if summary is not None:
                items = summary["items"]
                assert isinstance(items, list)
                prefill = {
                    "name": summary["name"],
                    "items": [_run_prefill(item.run_id) for item in reversed(items)],
                }
        return render_template(
            "sentinel_batches.html",
            max_batch_items=1,
            batch_name_max_chars=config.batch_name_max_chars,
            prefill_batch=prefill,
            form_options=_run_form_options(config),
        )

    @blueprint.post("/api/batches")
    def create_batch() -> Any:
        payload = request.get_json(silent=True) or {}
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        actor = _current_actor()
        if not isinstance(raw_items, list) or len(raw_items) != 1:
            emit_event(
                "sentinel.batch_rejected",
                actor=actor,
                reason="item_count",
            )
            return jsonify({"error": "A batch must contain exactly one run."}), 400
        try:
            parameters = _validate_run_params(raw_items[0], config)
            name = str(payload.get("name", "")).strip()[: config.batch_name_max_chars]
            batch_id = uuid.uuid4().hex
            report = _start_validated_run(
                parameters,
                owner=actor.get_id() if actor else "",
                batch_id=batch_id,
                batch_label=name,
            )
        except (ValueError, TargetValidationError) as error:
            emit_event(
                "sentinel.batch_rejected",
                actor=actor,
                reason="invalid_item",
                error_type=type(error).__name__,
            )
            return jsonify({"error": str(error)}), 400
        except RunBusyError as error:
            emit_event(
                "sentinel.batch_rejected",
                actor=actor,
                reason="execution_busy",
            )
            return (
                jsonify({"error": str(error)}),
                409,
                {"Retry-After": str(config.lease_retry_after_s)},
            )
        emit_event(
            "sentinel.batch_request_completed",
            actor=actor,
            batch_id=batch_id,
            runs=1,
        )
        return jsonify({"batch_id": batch_id, "run_ids": [report.run_id]}), 201

    @blueprint.get("/batch/<batch_id>")
    def batch_detail(batch_id: str) -> Any:
        summary = _batch_summary(batch_id)
        if summary is None:
            abort(404)
        return render_template(
            "sentinel_batch.html",
            batch=summary,
            child_runs=_batch_children(batch_id, DataInterface()),
        )

    @blueprint.get("/api/batch/<batch_id>")
    def batch_status(batch_id: str) -> Any:
        children = _batch_children(batch_id, DataInterface())
        if not children:
            abort(404)
        return jsonify({"batch_id": batch_id, "child_runs": children})

    @blueprint.post("/api/batch/<batch_id>/delete")
    def delete_batch(batch_id: str) -> Any:
        children = _batch_children(batch_id, DataInterface())
        if not children:
            emit_event(
                "sentinel.batch_delete_rejected",
                actor=_current_actor(),
                batch_id=batch_id,
                reason="not_found",
            )
            abort(404)
        if any(child["status"] in {"queued", "running", "summarizing"} for child in children):
            emit_event(
                "sentinel.batch_delete_rejected",
                actor=_current_actor(),
                batch_id=batch_id,
                reason="active_runs",
            )
            return jsonify({"error": "Batch has active runs"}), 409
        run_ids = [str(child["run_id"]) for child in children]
        for run_id in run_ids:
            if not delete_run(run_id):
                emit_event(
                    "sentinel.batch_delete_failed",
                    actor=_current_actor(),
                    batch_id=batch_id,
                    run_id=run_id,
                    reason="storage_rejected",
                )
                return (
                    jsonify(
                        {
                            "error": "A batch run could not be deleted",
                            "run_id": run_id,
                        }
                    ),
                    409,
                )
        emit_event(
            "sentinel.batch_deleted",
            actor=_current_actor(),
            batch_id=batch_id,
            runs=len(run_ids),
        )
        return jsonify({"batch_id": batch_id, "deleted": True, "run_ids": run_ids})

    @blueprint.get("/report/<run_id>")
    def report(run_id: str) -> Any:
        value = get_run(run_id)
        if value is None:
            abort(404)
        return render_template("sentinel_report.html", report=_report_payload(value, config))

    @blueprint.get("/report/<run_id>/json")
    def report_json(run_id: str) -> Any:
        value = get_run(run_id)
        if value is None:
            abort(404)
        return jsonify(_report_payload(value, config))

    @blueprint.get("/report/<run_id>/pdf")
    def report_pdf(run_id: str) -> Any:
        value = get_run(run_id)
        if value is None:
            abort(404)
        if str(value.status) in {"queued", "running", "summarizing"}:
            return jsonify({"error": "Run is still in progress"}), 409
        payload = value.model_dump(mode="json")
        payload["final_report_html"] = str(
            _render_final_report_for_pdf(value.final_report, value.run_id, value.screenshots)
        )
        payload["device_label"] = config.device_labels.get(value.device, "")
        payload["demographic_label"] = config.demographic_labels.get(value.demographic, "")
        html = render_template("sentinel_report_pdf.html", report=payload)
        static = Path(__file__).parent / "static"
        try:
            output = render_report_pdf(html, (static / "report_pdf.css",))
        except Exception as error:
            emit_event(
                "sentinel.report_pdf_failed",
                actor=_current_actor(),
                run_id=run_id,
                error=error,
                error_type=type(error).__name__,
            )
            return jsonify({"error": f"PDF generation failed: {error}"}), 500
        safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", value.title or run_id).strip("-")[:80]
        return Response(
            output,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="sentinel-{safe_title or run_id}.pdf"'
            },
        )

    @blueprint.get("/report/<run_id>/screenshots/<filename>")
    def screenshot(run_id: str, filename: str) -> Any:
        if _SCREENSHOT_FILENAME_RE.fullmatch(filename) is None:
            abort(404)
        try:
            value = DataInterface().read_screenshot(run_id, filename)
        except ValueError:
            abort(404)
        if value is None:
            abort(404)
        return Response(value, mimetype="image/png")

    @blueprint.get("/report/<run_id>/screenshots/thumb/<filename>")
    def screenshot_thumbnail(run_id: str, filename: str) -> Any:
        if _SCREENSHOT_FILENAME_RE.fullmatch(filename) is None:
            abort(404)
        try:
            value = ensure_screenshot_thumbnail(run_id, filename)
        except ValueError:
            abort(404)
        if value is None:
            abort(404)
        return Response(value, mimetype="image/png")

    return blueprint


def _batch_children(batch_id: str) -> list[dict[str, object]]:
    return [
        {
            "run_id": run.run_id,
            "status": str(run.status),
            "run_outcome": str(run.run_outcome or ""),
            "title": run.title or run.target_url,
            "batch_label": run.batch_label,
            "target_url": run.target_url,
        }
        for run in DataInterface().list_reports()
        if run.batch_id == batch_id
    ]


def _batch_summary(batch_id: str) -> dict[str, object] | None:
    return next(
        (item for item in _derive_batches(DataInterface().list_reports()) if item["batch_id"] == batch_id),
        None,
    )
