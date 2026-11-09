import json
import logging
import time
import uuid
from pathlib import Path
from urllib.parse import unquote
from xml.sax.saxutils import escape as xml_escape

import flask_login
from flask import Response, abort, current_app, g, redirect, render_template, request, url_for

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.loft.data_interface import DataInterface as LoftDataInterface
from web_app.logging_utils import log_event


def inject_app_name():
    config = ConfigManager()
    return dict(
        app_name="NabiCat",
        cache_browser_max_size_bytes=config.cache_browser_max_size_bytes,
        cache_service_worker_prefix=config.cache_service_worker_prefix,
        cache_service_worker_ready_timeout_ms=config.cache_service_worker_ready_timeout_ms,
        cache_service_worker_message_timeout_ms=config.cache_service_worker_message_timeout_ms,
    )

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

def _has_scanner_path_signature(path: str, config: ConfigManager) -> bool:
    segments = tuple(
        segment
        for segment in unquote(path).casefold().split("/")
        if segment
    )
    return any(
        segment in config.scanner_path_segment_names
        or segment.startswith(config.scanner_path_segment_prefixes)
        for segment in segments
    )


def _request_app() -> str:
    blueprint = request.blueprint or "web"
    return blueprint.split(".", 1)[0].removesuffix("_api")


def before_request():
    config = ConfigManager()
    g.request_id = uuid.uuid4().hex
    g.request_started_ns = time.monotonic_ns()
    g.request_app = _request_app()
    # Avoid resolving current_user recursively if a low-level event is emitted
    # while Flask-Login's user loader is still running.
    g.request_user = None
    # Scanner paths abort before normal logging and must remain suppressed.
    g.request_log_suppressed = True
    if (
        request.method.upper() in config.scanner_methods
        or (
            request.url_rule is None
            and _has_scanner_path_signature(request.path, config)
        )
    ):
        abort(404)

    # Auto-login as admin in debug mode.
    if config.debug_mode and not flask_login.current_user.is_authenticated:
        di = DataInterface()
        with di.edit_users() as users:
            user = users.get("admin")
            if user is None:
                user = di.generate_new_user("admin", "admin")
                user.is_admin = True
                users.add(user)
                log_event("account", "account.debug_admin_created", user=user)
        flask_login.login_user(user, remember=True)

    if flask_login.current_user.is_authenticated:
        g.request_user = str(flask_login.current_user.id)
    else:
        g.request_user = None

    g.request_log_suppressed = False

    log_event(
        g.request_app,
        "request.started",
        method=request.method,
        path=request.path,
        route=request.url_rule.rule if request.url_rule else None,
    )


def after_request(response: Response) -> Response:
    config = ConfigManager()
    request_id = getattr(g, "request_id", None)
    if request_id:
        response.headers[config.request_id_header] = request_id

    endpoint = request.endpoint or ""
    is_versioned_static = (
        endpoint.endswith("static")
        and bool(request.args.get("v"))
    )
    is_public_media = (
        endpoint in config.cache_public_media_endpoints
        and response.cache_control.public
    )
    if (
        response.headers.getlist("Set-Cookie")
        or (
            flask_login.current_user.is_authenticated
            and not is_versioned_static
            and not is_public_media
        )
    ):
        response.headers["Cache-Control"] = "private, no-store"

    if not getattr(g, "request_log_suppressed", True):
        status = response.status_code
        if status >= config.request_log_error_status:
            level = logging.ERROR
        elif status >= config.request_log_warning_status:
            level = logging.WARNING
        else:
            level = logging.INFO
        started_ns = getattr(g, "request_started_ns", time.monotonic_ns())
        log_event(
            getattr(g, "request_app", _request_app()),
            "request.completed",
            level=level,
            method=request.method,
            path=request.path,
            route=request.url_rule.rule if request.url_rule else None,
            status=status,
            duration_ms=round((time.monotonic_ns() - started_ns) / 1_000_000, 3),
        )
    return response


def teardown_request(error: BaseException | None) -> None:
    if (error is None or getattr(g, "request_log_suppressed", True)
            or getattr(g, "request_exception_logged", False)):
        return
    log_event(
        getattr(g, "request_app", _request_app()),
        "request.exception",
        level=logging.ERROR,
        exc_info=error,
        method=request.method,
        path=request.path,
        route=request.url_rule.rule if request.url_rule else None,
        error_type=type(error).__name__,
    )

def home():
    return render_template(
        'home.html',
        build_version=current_app.config["BUILD_VERSION"],
        build_version_is_tag=current_app.config["BUILD_VERSION_IS_TAG"],
    )


def legacy_loft_redirect(legacy_path: str):
    target = url_for("loft.index") + legacy_path
    if request.query_string:
        target += "?" + request.query_string.decode("latin-1")
    return redirect(target, code=308)


def privacy():
    return render_template('privacy.html')


def service_worker():
    """Serve service worker from root for proper scope"""
    config = ConfigManager()
    source = (Path(current_app.static_folder) / 'service-worker.js').read_text()
    static_prefixes = config.cache_versioned_static_path_prefixes
    source = source.replace(
        '__NABICAT_CACHE_VERSION__',
        json.dumps(config.cache_service_worker_version),
    ).replace(
        '__NABICAT_CACHE_PREFIX__',
        json.dumps(config.cache_service_worker_prefix),
    ).replace(
        '__NABICAT_STATIC_PATH_PREFIXES__',
        json.dumps(static_prefixes),
    ).replace(
        '__NABICAT_PUBLIC_MEDIA_PATH_PREFIXES__',
        json.dumps(config.cache_public_media_path_prefixes),
    )
    response = Response(source, mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

def sitemap():
    cfg = ConfigManager()
    base_url = cfg.site_url.rstrip("/")
    urls = [
        url_for('home'),
        url_for('privacy'),
        url_for('loft.index'),
        url_for('crosswords.index'),
        url_for('simulations_api.index'),
        url_for('simulations_api.game_of_life'),
        url_for('simulations_api.astar'),
    ]
    for project in LoftDataInterface().get_posts_by_project():
        for post in project.posts:
            urls.append(url_for('loft.view_post', project=project.name, post=post))

    locs = "\n".join(
        f"  <url><loc>{xml_escape(base_url + path)}</loc></url>"
        for path in dict.fromkeys(urls)
    )
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'{locs}\n'
        f'</urlset>\n',
        mimetype='application/xml',
    )


def robots_txt():
    config = ConfigManager()
    sitemap_url = f'{config.site_url.rstrip("/")}{url_for("sitemap")}'
    return Response(
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {sitemap_url}\n",
        mimetype="text/plain",
    )


def register_web_routes(app):
    app.context_processor(inject_app_name)
    app.before_request(before_request)
    app.after_request(after_request)
    app.teardown_request(teardown_request)
    app.add_url_rule("/", view_func=home)
    app.add_url_rule("/privacy", view_func=privacy)
    app.add_url_rule("/service-worker.js", view_func=service_worker)
    app.add_url_rule("/sitemap.xml", view_func=sitemap)
    app.add_url_rule("/robots.txt", view_func=robots_txt)
    legacy_methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    app.add_url_rule("/hammock", view_func=legacy_loft_redirect,
                     defaults={"legacy_path": ""}, methods=legacy_methods, strict_slashes=False)
    app.add_url_rule("/hammock/<path:legacy_path>", view_func=legacy_loft_redirect,
                     methods=legacy_methods)
