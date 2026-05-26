from __future__ import annotations

import base64
import re

from flask import Blueprint, Response, abort, jsonify, render_template, request, send_from_directory
from flask_login import current_user, login_required
from markdown_it import MarkdownIt
from markupsafe import Markup

from web_app.config import ConfigManager
from web_app.sentinel.data_interface import DataInterface
from web_app.sentinel.runner import get_run, render_report_pdf, request_cancel, start_run
from web_app.sentinel.target_policy import TargetValidationError, validate_public_web_url


_SCREENSHOT_FILENAME_RE = re.compile(r"^step-\d{2}(?:-annot)?\.png$")


sentinel_api = Blueprint(
    "sentinel",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/sentinel",
)

@sentinel_api.before_request
@login_required
def before_request():
    if not current_user.is_admin:
        abort(403)


@sentinel_api.context_processor
def inject_app_name():
    cfg = ConfigManager()
    return dict(
        app_name="Sentinel",
        sidebar_runs=DataInterface().list_reports()[: cfg.sentinel.max_retained_runs],
    )


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _limit_from_request(raw_limit) -> int:
    cfg = ConfigManager()
    try:
        limit_mins = int(raw_limit) if raw_limit not in (None, "") else cfg.sentinel.default_limit_mins
    except (TypeError, ValueError):
        limit_mins = cfg.sentinel.default_limit_mins
    limit_mins = max(cfg.sentinel.min_limit_mins, min(limit_mins, cfg.sentinel.max_limit_mins))
    return limit_mins * 60


def _resolve_screenshot_src(src: str, run_id: str, allowed_filenames: set[str]) -> str | None:
    filename = src.rsplit("/", 1)[-1]
    if filename not in allowed_filenames or not _SCREENSHOT_FILENAME_RE.match(filename):
        return None
    return f"/sentinel/report/{run_id}/screenshots/{filename}"


def _render_final_report(markdown_text: str, run_id: str, screenshots: list[str]) -> Markup:
    md = MarkdownIt("commonmark", {"html": False})
    allowed = {str(s).rsplit("/", 1)[-1] for s in screenshots or []}
    default_image = md.renderer.rules.get("image")

    def render_image(tokens, idx, options, env):
        token = tokens[idx]
        src = token.attrGet("src") or ""
        resolved = _resolve_screenshot_src(src, run_id, allowed)
        if resolved is None:
            return ""
        token.attrSet("src", resolved)
        token.attrSet("loading", "lazy")
        token.attrSet("decoding", "async")
        existing_class = token.attrGet("class") or ""
        token.attrSet("class", (existing_class + " sentinel-final-report-img").strip())
        if default_image:
            return default_image(tokens, idx, options, env)
        return md.renderer.renderToken(tokens, idx, options)

    md.renderer.rules["image"] = render_image
    return Markup(md.render(markdown_text or ""))


def _render_final_report_for_pdf(markdown_text: str, run_id: str, screenshots: list[str]) -> Markup:
    """Like _render_final_report, but inline screenshot src is a base64 data: URI so headless
    Chromium can decode the image without a network or file:// fetch (the page origin from
    set_content is about:blank, which blocks file:// subresource loads)."""
    md = MarkdownIt("commonmark", {"html": False})
    allowed = {str(s).rsplit("/", 1)[-1] for s in screenshots or []}
    default_image = md.renderer.rules.get("image")
    screenshots_dir = DataInterface().screenshots_dir(run_id)

    def render_image(tokens, idx, options, env):
        token = tokens[idx]
        src = token.attrGet("src") or ""
        filename = src.rsplit("/", 1)[-1]
        if filename not in allowed or not _SCREENSHOT_FILENAME_RE.match(filename):
            return ""
        path = screenshots_dir / filename
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        encoded = base64.b64encode(data).decode("ascii")
        token.attrSet("src", f"data:image/png;base64,{encoded}")
        existing_class = token.attrGet("class") or ""
        token.attrSet("class", (existing_class + " sentinel-final-report-img").strip())
        if default_image:
            return default_image(tokens, idx, options, env)
        return md.renderer.renderToken(tokens, idx, options)

    md.renderer.rules["image"] = render_image
    return Markup(md.render(markdown_text or ""))


def _report_payload(report: dict) -> dict:
    cfg = ConfigManager()
    payload = dict(report)
    payload["final_report_html"] = str(
        _render_final_report(
            str(report.get("final_report", "")),
            str(report.get("run_id", "")),
            list(report.get("screenshots", []) or []),
        )
    )
    payload["screenshot_load_stagger_ms"] = cfg.sentinel.screenshot_load_stagger_ms
    payload["screenshot_load_max_retries"] = cfg.sentinel.screenshot_load_max_retries
    payload["screenshot_load_retry_delay_ms"] = cfg.sentinel.screenshot_load_retry_delay_ms
    device_key = str(report.get("device") or "")
    demographic_key = str(report.get("demographic") or "")
    payload["device_label"] = cfg.sentinel.device_labels.get(device_key, "")
    payload["demographic_label"] = cfg.sentinel.demographic_labels.get(demographic_key, "")
    return payload


@sentinel_api.route("/")
def index():
    cfg = ConfigManager()
    prefill_url = str(request.args.get("url", "")).strip()
    prefill_prompt = str(request.args.get("prompt", ""))[: cfg.sentinel.prompt_max_chars]
    prefill_title = str(request.args.get("title", "")).strip()[: cfg.sentinel.title_max_chars]
    prefill_allow_accounts = _truthy(request.args.get("allow_accounts"))
    prefill_allow_external = _truthy(request.args.get("allow_external"))
    try:
        prefill_limit = int(request.args.get("limit", cfg.sentinel.default_limit_mins))
    except (TypeError, ValueError):
        prefill_limit = cfg.sentinel.default_limit_mins
    prefill_limit = max(cfg.sentinel.min_limit_mins, min(prefill_limit, cfg.sentinel.max_limit_mins))

    raw_device = str(request.args.get("device", "")).strip()
    prefill_device = raw_device if raw_device in cfg.sentinel.device_profiles else cfg.sentinel.default_device
    raw_demographic = str(request.args.get("demographic", "")).strip()
    prefill_demographic = (
        raw_demographic if raw_demographic in cfg.sentinel.demographic_personas else cfg.sentinel.default_demographic
    )

    device_options = [(key, cfg.sentinel.device_labels.get(key, key)) for key in cfg.sentinel.device_profiles]
    demographic_options = [
        (key, cfg.sentinel.demographic_labels.get(key, key)) for key in cfg.sentinel.demographic_personas
    ]

    return render_template(
        "sentinel_index.html",
        runs=DataInterface().list_reports()[: cfg.sentinel.max_retained_runs],
        default_limit=cfg.sentinel.default_limit_mins,
        min_limit=cfg.sentinel.min_limit_mins,
        max_limit=cfg.sentinel.max_limit_mins,
        prompt_max_chars=cfg.sentinel.prompt_max_chars,
        title_max_chars=cfg.sentinel.title_max_chars,
        prefill_url=prefill_url,
        prefill_prompt=prefill_prompt,
        prefill_limit=prefill_limit,
        prefill_title=prefill_title,
        prefill_allow_accounts=prefill_allow_accounts,
        prefill_allow_external=prefill_allow_external,
        prefill_device=prefill_device,
        prefill_demographic=prefill_demographic,
        device_options=device_options,
        demographic_options=demographic_options,
    )


@sentinel_api.route("/api/runs", methods=["POST"])
def create_run():
    cfg = ConfigManager()
    payload = request.get_json(silent=True) or request.form
    raw_url = str(payload.get("url", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()[: cfg.sentinel.prompt_max_chars]
    title = str(payload.get("title", "")).strip()[: cfg.sentinel.title_max_chars]
    allow_accounts = _truthy(payload.get("allow_accounts"))
    allow_external = _truthy(payload.get("allow_external"))
    device = str(payload.get("device", "")).strip()
    demographic = str(payload.get("demographic", "")).strip()
    limit_s = _limit_from_request(payload.get("limit"))

    try:
        target = validate_public_web_url(raw_url)
    except TargetValidationError as e:
        return jsonify({"error": str(e)}), 400

    return (
        jsonify(start_run(
            target,
            prompt,
            limit_s,
            title=title,
            allow_accounts=allow_accounts,
            allow_external=allow_external,
            device=device,
            demographic=demographic,
            owner=str(getattr(current_user, "id", "") or ""),
        )),
        202,
    )


@sentinel_api.route("/api/runs/<run_id>")
def run_status(run_id: str):
    report = get_run(run_id)
    if report is None:
        abort(404)
    return jsonify(_report_payload(report))


@sentinel_api.route("/api/runs/<run_id>/cancel", methods=["POST"])
def cancel(run_id: str):
    report = get_run(run_id)
    if report is None:
        abort(404)
    if report.get("status") not in {"queued", "running", "summarizing"}:
        return jsonify({"run_id": run_id, "status": report.get("status"), "cancelled": False})
    cancelled = request_cancel(run_id)
    return jsonify({"run_id": run_id, "cancelled": cancelled})


@sentinel_api.route("/report/<run_id>")
def report(run_id: str):
    report_data = get_run(run_id)
    if report_data is None:
        abort(404)
    return render_template("sentinel_report.html", report=_report_payload(report_data))


@sentinel_api.route("/report/<run_id>/json")
def report_json(run_id: str):
    report_data = get_run(run_id)
    if report_data is None:
        abort(404)
    return jsonify(_report_payload(report_data))


@sentinel_api.route("/report/<run_id>/pdf")
def report_pdf(run_id: str):
    report_data = get_run(run_id)
    if report_data is None:
        abort(404)
    if report_data.get("status") not in {"completed", "timed_out", "cancelled", "failed"}:
        return jsonify({"error": "Run is still in progress"}), 409

    payload = dict(report_data)
    payload["final_report_html"] = str(
        _render_final_report_for_pdf(
            str(report_data.get("final_report", "")),
            str(report_data.get("run_id", "")),
            list(report_data.get("screenshots", []) or []),
        )
    )
    cfg = ConfigManager()
    device_key = str(report_data.get("device") or "")
    demographic_key = str(report_data.get("demographic") or "")
    payload["device_label"] = cfg.sentinel.device_labels.get(device_key, "")
    payload["demographic_label"] = cfg.sentinel.demographic_labels.get(demographic_key, "")

    html = render_template("sentinel_report_pdf.html", report=payload)
    try:
        pdf_bytes = render_report_pdf(html)
    except Exception as e:
        return jsonify({"error": f"PDF generation failed: {e}"}), 500

    safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", str(report_data.get("title") or run_id)).strip("-")[:80] or run_id
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="sentinel-{safe_title}.pdf"'},
    )


@sentinel_api.route("/report/<run_id>/screenshots/<filename>")
def screenshot(run_id: str, filename: str):
    if not _SCREENSHOT_FILENAME_RE.match(filename):
        abort(404)
    try:
        directory = DataInterface().screenshots_dir(run_id)
    except ValueError:
        abort(404)
    return send_from_directory(directory, filename)
