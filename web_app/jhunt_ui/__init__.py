from __future__ import annotations

import logging
import re
import time
import uuid

import requests
from flask import Blueprint, Response, abort, jsonify, render_template, request, stream_with_context
from flask_login import current_user
from flask_wtf.csrf import CSRFError

from web_app.blueprint_registration import register_app_name, require_admin_blueprint
from web_app.config import ConfigManager
from web_app.logging_utils import log_event
from web_app.redis_client import get_redis, rmw_lock
from .connection_store import save_connection
from .bridge import (
    BridgeError,
    JHuntClient,
    valid_artifact_hash,
    valid_generation,
    valid_identifier,
    valid_store_id,
)


jhunt_ui_api = Blueprint(
    "jhunt_ui",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/jhunt-ui",
)

def _private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _error(error: BridgeError):
    response = jsonify({"error": str(error)})
    response.status_code = error.status_code
    return _private(response)


def _audit(event: str, *, status: str, reason: str | None = None, **details):
    log_event("jhunt_ui", f"jhunt_ui.{event}", user=current_user, status=status,
              reason=reason, **details)


@jhunt_ui_api.before_request
def audit_bridge_auth_rejection():
    if request.path.startswith("/jhunt-ui/api/") and request.method == "POST":
        if not current_user.is_authenticated:
            log_event("jhunt_ui", "jhunt_ui.mutation_rejected", status="rejected",
                      reason="login_required", endpoint=request.endpoint)
        elif not current_user.is_admin:
            log_event("jhunt_ui", "jhunt_ui.mutation_rejected", user=current_user,
                      status="rejected", reason="admin_required", endpoint=request.endpoint)


@jhunt_ui_api.errorhandler(CSRFError)
def csrf_rejected(error):
    log_event("jhunt_ui", "jhunt_ui.mutation_rejected", user=current_user,
              status="rejected", reason="csrf_failed", exc_info=error,
              error_type=type(error).__name__)
    return _private(jsonify({"error": "Request verification failed."})), 400


require_admin_blueprint(jhunt_ui_api, api_prefixes=("/jhunt-ui/api/",))
register_app_name(jhunt_ui_api, "JHunt")


def _json_body(required: set[str], allowed: set[str], operation: str, resource_id: str) -> dict:
    if not request.is_json:
        _audit(operation, status="rejected", reason="invalid_body", resource_id=resource_id)
        abort(400)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not required.issubset(body) or not set(body).issubset(allowed):
        _audit(operation, status="rejected", reason="invalid_body", resource_id=resource_id)
        abort(400)
    return body


def _acquire_stream_slot() -> tuple[str, str] | None:
    config = ConfigManager()
    redis = get_redis()
    owner = uuid.uuid4().hex
    try:
        with rmw_lock(config.jhunt_stream_slot_lock_name):
            for number in range(config.jhunt_stream_max_active):
                key = f"{config.jhunt_stream_slot_prefix}{number}"
                if redis.set(key, owner, nx=True, ex=config.jhunt_stream_slot_lease_s):
                    return key, owner
    except (TimeoutError, RuntimeError) as error:
        # A slot-table lock failure is distinct from a full table but safely
        # fails closed: no upstream stream is started without a reserved slot.
        log_event("jhunt_ui", "jhunt_ui.stream_slot_acquire_failed",
                  level=logging.ERROR, exc_info=error,
                  error_type=type(error).__name__)
        return None
    return None


def _release_stream_slot(slot):
    if slot is None:
        return
    key, owner = slot
    redis = get_redis()
    with rmw_lock(ConfigManager().jhunt_stream_slot_lock_name):
        if redis.get(key) in (owner, owner.encode()):
            redis.delete(key)


@jhunt_ui_api.get("/")
def index():
    config = ConfigManager()
    return render_template(
        "jhunt_index.html",
        jhunt_retry_initial_ms=config.jhunt_retry_initial_ms,
        jhunt_retry_max_ms=config.jhunt_retry_max_ms,
    )


@jhunt_ui_api.after_request
def private_bridge_responses(response):
    if request.path.startswith("/jhunt-ui/api/"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@jhunt_ui_api.get("/api/inbox")
def inbox():
    try:
        with JHuntClient() as client:
            snapshot = client.load_inbox()
    except BridgeError as error:
        _audit("inbox", status="failure", reason="upstream_unavailable",
               exc_info=error, error_type=type(error).__name__)
        return _error(error)
    _audit("inbox", status="success", revision=snapshot["revision"])
    return _private(jsonify(snapshot))


@jhunt_ui_api.post("/api/connection")
def connect():
    config = ConfigManager()
    body = _json_body({"token"}, {"token"}, "connect", "connection")
    token = body["token"]
    if (
        not isinstance(token, str)
        or not config.jhunt_token_min_chars <= len(token) <= config.jhunt_token_max_chars
        or not token.isascii()
        or any(char.isspace() or not char.isprintable() for char in token)
    ):
        _audit("connect", status="rejected", reason="invalid_token")
        return _private(jsonify({"error": "Enter a valid JHunt API token."})), 400
    candidate = {
        **config.jhunt_connection_settings(),
        "enabled": True,
        "token": token,
        "generation": uuid.uuid4().hex,
    }
    try:
        with JHuntClient(candidate) as client:
            snapshot = client.load_inbox()
            # Persist only after the token and review API have been verified.
            save_connection(client.settings)
    except BridgeError as error:
        _audit("connect", status="failure", reason="verification_failed",
               exc_info=error, error_type=type(error).__name__)
        return _error(error)
    except (OSError, ValueError, TimeoutError, RuntimeError) as error:
        # Do not attach validation errors or values containing the token.
        safe_error = BridgeError("JHunt connection could not be saved.")
        _audit("connect", status="failure", reason="save_failed",
               exc_info=safe_error, error_type=type(error).__name__)
        return _error(safe_error)
    _audit("connect", status="success", store_id=snapshot["store_id"])
    return _private(jsonify({
        "connected": True,
        "connection_generation": candidate["generation"],
        "store_id": snapshot["store_id"],
    }))


@jhunt_ui_api.post("/api/tasks/<ident>/input")
def answer_task(ident: str):
    config = ConfigManager()
    if not valid_identifier(ident):
        _audit("answer", status="rejected", reason="invalid_identifier", task_id="invalid")
        abort(400)
    body = _json_body({"answer", "expected_store_id", "expected_connection_generation"},
                      {"answer", "expected_store_id", "expected_connection_generation",
                       "expected_question_hash"}, "answer", ident)
    answer = body["answer"]
    if (
        not isinstance(answer, dict)
        or set(answer) != {"text"}
        or not isinstance(answer["text"], str)
        or not answer["text"].strip()
        or len(answer["text"]) > config.jhunt_answer_max_chars
        or not valid_store_id(body["expected_store_id"])
        or not valid_generation(body["expected_connection_generation"])
        or ("expected_question_hash" in body and not valid_artifact_hash(body["expected_question_hash"]))
    ):
        _audit("answer", status="rejected", reason="invalid_body", task_id=ident)
        abort(400)
    try:
        with JHuntClient() as client:
            if body["expected_connection_generation"] != client.generation:
                raise BridgeError("JHunt connection changed; refresh before sending.", 409)
            upstream_body = {
                "answer": {"text": answer["text"].strip()},
                "expected_store_id": body["expected_store_id"],
            }
            if "expected_question_hash" in body:
                upstream_body["expected_question_hash"] = body["expected_question_hash"]
            client.answer_task(ident, upstream_body)
    except BridgeError as error:
        _audit("answer", status="failure", reason="upstream_rejected", task_id=ident,
               status_code=error.status_code, exc_info=error,
               error_type=type(error).__name__)
        return _error(error)
    _audit("answer", status="success", task_id=ident)
    return _private(jsonify({"accepted": True}))


@jhunt_ui_api.post("/api/applications/<ident>/decision")
def decide_application(ident: str):
    if not valid_identifier(ident):
        _audit("decision", status="rejected", reason="invalid_identifier", package_id="invalid")
        abort(400)
    body = _json_body(
        {"manifest_hash", "decision", "expected_store_id", "expected_connection_generation"},
        {"manifest_hash", "decision", "expected_store_id", "expected_connection_generation"},
        "decision", ident,
    )
    if (
        not valid_artifact_hash(body["manifest_hash"])
        or body["decision"] not in ("approve", "reject")
        or not valid_store_id(body["expected_store_id"])
        or not valid_generation(body["expected_connection_generation"])
    ):
        _audit("decision", status="rejected", reason="invalid_body", package_id=ident)
        abort(400)
    try:
        with JHuntClient() as client:
            if body["expected_connection_generation"] != client.generation:
                raise BridgeError("JHunt connection changed; refresh before sending.", 409)
            client.decide_application(ident, {
                "manifest_hash": body["manifest_hash"],
                "decision": body["decision"],
                "expected_store_id": body["expected_store_id"],
            })
    except BridgeError as error:
        _audit("decision", status="failure", reason="upstream_rejected", package_id=ident,
               decision=body["decision"], status_code=error.status_code,
               exc_info=error, error_type=type(error).__name__)
        return _error(error)
    _audit("decision", status="success", package_id=ident, decision=body["decision"])
    return _private(jsonify({"accepted": True}))


@jhunt_ui_api.get("/api/artifacts/<key>")
def artifact(key: str):
    if not valid_artifact_hash(key):
        abort(404)
    client = None
    try:
        client = JHuntClient()
        upstream = client.artifact(key)
    except BridgeError as error:
        if client is not None:
            client.close()
        _audit("artifact", status="failure", reason="upstream_unavailable", artifact=key,
               exc_info=error, error_type=type(error).__name__)
        return _error(error)
    media_type = upstream.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if media_type not in {"application/pdf", "image/png", "text/plain"}:
        upstream.close()
        client.close()
        _audit("artifact", status="rejected", reason="unsupported_media", artifact=key)
        abort(502)

    def chunks():
        try:
            yield from upstream.iter_content(chunk_size=64 * 1024)
        finally:
            upstream.close()
            client.close()

    response = Response(stream_with_context(chunks()), mimetype=media_type)
    response.headers["Content-Disposition"] = f'inline; filename="{key}"'
    if upstream.headers.get("Content-Length"):
        response.headers["Content-Length"] = upstream.headers["Content-Length"]
    response.call_on_close(client.close)
    _audit("artifact", status="success", artifact=key)
    return _private(response)


@jhunt_ui_api.get("/api/events")
def events():
    cursor = request.args.get("cursor", "")
    if len(cursor) > 300 or (cursor and not re.fullmatch(r"[A-Za-z0-9._:-]+", cursor)):
        abort(400)
    slot = _acquire_stream_slot()
    if slot is None:
        _audit("stream", status="rejected", reason="stream_limit")
        return _private(jsonify({"error": "Too many review streams."})), 503
    client = None
    try:
        client = JHuntClient()
        upstream = client.events(cursor)
    except BridgeError as error:
        if client is not None:
            client.close()
        _release_stream_slot(slot)
        _audit("stream", status="failure", reason="upstream_unavailable",
               exc_info=error, error_type=type(error).__name__)
        return _error(error)

    cleaned = [False]
    stream_user = current_user.get_id()

    def cleanup():
        if cleaned[0]:
            return
        cleaned[0] = True
        upstream.close()
        client.close()
        try:
            _release_stream_slot(slot)
        except Exception as error:
            log_event("jhunt_ui", "jhunt_ui.stream_slot_release_failed",
                      level=logging.ERROR, user=stream_user, exc_info=error,
                      error_type=type(error).__name__)
        else:
            log_event("jhunt_ui", "jhunt_ui.stream_closed", user=stream_user)

    def relay():
        started = time.monotonic()
        try:
            for line in upstream.iter_lines(chunk_size=1, decode_unicode=True):
                if time.monotonic() - started >= ConfigManager().jhunt_stream_max_lifetime_s:
                    break
                if line is None:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                yield f"{line}\n"
        except requests.RequestException as error:
            log_event("jhunt_ui", "jhunt_ui.stream_failed", level=logging.WARNING,
                      user=stream_user, exc_info=error,
                      error_type=type(error).__name__)
        finally:
            cleanup()

    response = Response(
        stream_with_context(relay()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
    response.call_on_close(cleanup)
    return _private(response)
