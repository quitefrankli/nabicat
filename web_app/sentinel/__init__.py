from __future__ import annotations

import re

from flask import Blueprint, abort, jsonify, render_template, request, send_from_directory
from flask_login import current_user, login_required
from markdown_it import MarkdownIt
from markupsafe import Markup

from web_app.config import ConfigManager
from web_app.sentinel.data_interface import DataInterface
from web_app.sentinel.runner import get_run, start_run
from web_app.sentinel.target_policy import TargetValidationError, validate_public_web_url


sentinel_api = Blueprint(
    "sentinel",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/sentinel",
)

_MD = MarkdownIt("commonmark", {"html": False})


@sentinel_api.before_request
@login_required
def before_request():
    if not current_user.is_admin:
        abort(403)


@sentinel_api.context_processor
def inject_app_name():
    return dict(app_name="Sentinel")


def _limit_from_request(raw_limit) -> int:
    cfg = ConfigManager()
    try:
        limit_mins = int(raw_limit) if raw_limit not in (None, "") else cfg.sentinel.default_limit_mins
    except (TypeError, ValueError):
        limit_mins = cfg.sentinel.default_limit_mins
    limit_mins = max(cfg.sentinel.min_limit_mins, min(limit_mins, cfg.sentinel.max_limit_mins))
    return limit_mins * 60


def _limit_from_report(report: dict) -> int:
    cfg = ConfigManager()
    min_seconds = cfg.sentinel.min_limit_mins * 60
    max_seconds = cfg.sentinel.max_limit_mins * 60
    try:
        limit_s = int(report.get("limit_s", cfg.sentinel.default_limit_mins * 60))
    except (TypeError, ValueError):
        limit_s = cfg.sentinel.default_limit_mins * 60
    return max(min_seconds, min(limit_s, max_seconds))


def _render_final_report(markdown_text: str) -> Markup:
    return Markup(_MD.render(markdown_text or ""))


def _report_payload(report: dict) -> dict:
    cfg = ConfigManager()
    payload = dict(report)
    payload["final_report_html"] = str(_render_final_report(str(report.get("final_report", ""))))
    payload["screenshot_load_stagger_ms"] = cfg.sentinel.screenshot_load_stagger_ms
    payload["screenshot_load_max_retries"] = cfg.sentinel.screenshot_load_max_retries
    payload["screenshot_load_retry_delay_ms"] = cfg.sentinel.screenshot_load_retry_delay_ms
    return payload


@sentinel_api.route("/")
def index():
    cfg = ConfigManager()
    return render_template(
        "sentinel_index.html",
        runs=DataInterface().list_reports()[: cfg.sentinel.max_retained_runs],
        default_limit=cfg.sentinel.default_limit_mins,
        min_limit=cfg.sentinel.min_limit_mins,
        max_limit=cfg.sentinel.max_limit_mins,
        prompt_max_chars=cfg.sentinel.prompt_max_chars,
    )


@sentinel_api.route("/api/runs", methods=["POST"])
def create_run():
    cfg = ConfigManager()
    payload = request.get_json(silent=True) or request.form
    raw_url = str(payload.get("url", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()[: cfg.sentinel.prompt_max_chars]
    limit_s = _limit_from_request(payload.get("limit"))

    try:
        target = validate_public_web_url(raw_url)
    except TargetValidationError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(start_run(target, prompt, limit_s)), 202


@sentinel_api.route("/api/runs/<run_id>")
def run_status(run_id: str):
    report = get_run(run_id)
    if report is None:
        abort(404)
    return jsonify(_report_payload(report))


@sentinel_api.route("/api/runs/<run_id>/rerun", methods=["POST"])
def rerun(run_id: str):
    report = get_run(run_id)
    if report is None:
        abort(404)

    try:
        target = validate_public_web_url(str(report.get("target_url", "")))
    except TargetValidationError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(start_run(target, str(report.get("prompt", "")), _limit_from_report(report))), 202


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


@sentinel_api.route("/report/<run_id>/screenshots/<filename>")
def screenshot(run_id: str, filename: str):
    if not re.match(r"^step-\d{2}\.png$", filename):
        abort(404)
    try:
        directory = DataInterface().screenshots_dir(run_id)
    except ValueError:
        abort(404)
    return send_from_directory(directory, filename)
