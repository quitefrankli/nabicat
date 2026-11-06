import subprocess
import time
from pathlib import Path

from flask import Flask, Request
from werkzeug.middleware.proxy_fix import ProxyFix

from web_app.config import ConfigManager
from web_app.extensions import bootstrap, csrf


class SetCookieNoStoreMiddleware:
    """Prevent any cookie-setting response from entering browser caches."""

    def __init__(self, wrapped_app):
        self.wrapped_app = wrapped_app

    def __call__(self, environ, start_response):
        def no_store_start_response(status, headers, exc_info=None):
            if any(name.lower() == "set-cookie" for name, _ in headers):
                headers = [
                    (name, value)
                    for name, value in headers
                    if name.lower() != "cache-control"
                ]
                headers.append(("Cache-Control", "private, no-store"))
            return start_response(status, headers, exc_info)

        return self.wrapped_app(environ, no_store_start_response)


class NabicatRequest(Request):
    """Apply route-specific request caps before Flask parses form data."""

    @property
    def max_content_length(self) -> int | None:
        configured_limit = super().max_content_length
        config = ConfigManager()
        loft = config.loft
        if (
            self.method == "POST"
            and self.path.startswith(loft.request_path_prefix)
        ):
            if configured_limit is None:
                return loft.gallery_request_max_bytes
            return min(
                configured_limit,
                loft.gallery_request_max_bytes,
            )
        if (
            self.method == "POST"
            and self.path.startswith(config.jswipe_request_path_prefix)
            and (self.content_type or "").startswith("multipart/")
        ):
            if configured_limit is None:
                return config.jswipe_multipart_request_max_bytes
            return min(configured_limit, config.jswipe_multipart_request_max_bytes)
        return configured_limit

    @max_content_length.setter
    def max_content_length(self, value: int | None) -> None:
        self._max_content_length = value


def _load_static_version() -> str:
    """Use the deployed commit as the asset cache key, even between tags."""
    repo_root = Path(__file__).resolve().parent.parent
    try:
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            timeout=ConfigManager().git_command_timeout_s,
        ).decode().strip()
    except Exception:
        return str(int(time.time()))
    return commit_hash or str(int(time.time()))


def _load_build_version() -> tuple[str, bool]:
    """Display the nearest reachable tag, falling back to the commit SHA."""
    commit_hash = _load_static_version()

    try:
        tag = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            stderr=subprocess.DEVNULL,
            timeout=ConfigManager().git_command_timeout_s,
        ).decode().strip()
    except Exception:
        tag = ""

    return (tag, True) if tag else (commit_hash, False)


BUILD_VERSION, BUILD_VERSION_IS_TAG = _load_build_version()
STATIC_VERSION = _load_static_version()


def _add_static_version(endpoint, values):
    if endpoint and endpoint.endswith('static') and 'v' not in values:
        values['v'] = STATIC_VERSION


def create_app(config=None) -> Flask:
    """Build an application with independent Flask configuration and routes."""
    from web_app.auth import init_auth
    from web_app.blueprint_registration import register_all_blueprints
    from web_app.web_routes import register_web_routes

    application = Flask(__name__)
    application.request_class = NabicatRequest
    application.wsgi_app = SetCookieNoStoreMiddleware(
        ProxyFix(application.wsgi_app, x_for=1, x_proto=1, x_host=1)
    )
    cfg = ConfigManager()
    application.config.update(
        BUILD_VERSION=BUILD_VERSION,
        BUILD_VERSION_IS_TAG=BUILD_VERSION_IS_TAG,
        PERMANENT_SESSION_LIFETIME=cfg.session_lifetime,
        SESSION_COOKIE_NAME=cfg.flask_session_cookie_name,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if config is not None:
        application.config.from_mapping(config)
    application.url_defaults(_add_static_version)
    bootstrap.init_app(application)
    csrf.init_app(application)
    init_auth(application)
    register_all_blueprints(application)
    register_web_routes(application)
    return application


# Retain the WSGI application used by existing entry points and scripts.
app = create_app()
