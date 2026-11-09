import flask
import flask_login
from flask import request

from web_app.config import ConfigManager


def register_all_blueprints(app):
    from web_app.qread import qread_api
    from web_app.jhunt_ui import jhunt_ui_api
    from web_app.sentinel import sentinel_api
    from web_app.crosswords import crosswords_api
    from web_app.todoist import todoist_api
    from web_app.tubio import tubio_api
    from web_app.metrics import metrics_api
    from web_app.account_api import account_api
    from web_app.file_store import file_store_api
    from web_app.api import api_api
    from web_app.proxy import proxy_api
    from web_app.loft import loft_api, quitefrankli_api
    from web_app.dev import dev_api
    from web_app.simulations import simulations_api
    from web_app.oauth import oauth_api
    from web_app.todoist.api import actions_api

    blueprints = [
        qread_api,
        jhunt_ui_api,
        sentinel_api,
        todoist_api,
        crosswords_api,
        tubio_api,
        metrics_api,
        account_api,
        file_store_api,
        api_api,
        proxy_api,
        loft_api,
        quitefrankli_api,
        dev_api,
        simulations_api,
        oauth_api,
        actions_api,
    ]

    for blueprint in blueprints:
        app.register_blueprint(blueprint)


def register_app_name(blueprint, name: str) -> None:
    """Register a context processor exposing ``app_name`` to this blueprint's templates."""
    @blueprint.context_processor
    def _inject_app_name():
        return dict(app_name=name)


def require_login_blueprint(blueprint) -> None:
    """Gate every route in ``blueprint`` behind login."""
    @blueprint.before_request
    @flask_login.login_required
    def _require_login():
        pass


def require_admin_blueprint(blueprint, message: str | None = None, api_prefixes: tuple[str, ...] = ()) -> None:
    """Gate every route in ``blueprint`` behind admin access."""
    @blueprint.before_request
    @flask_login.login_required
    def _require_admin():
        if not flask_login.current_user.is_admin:
            return redirect_with_access_denied(
                message or ConfigManager().admin_access_denied_message,
                api_prefixes,
            )


def redirect_with_access_denied(message: str, api_prefixes: tuple[str, ...] = ()):
    if request.method != 'GET' or any(request.path.startswith(prefix) for prefix in api_prefixes):
        flask.abort(403)
    config = ConfigManager()
    flask.flash(message, category='error')
    return flask.redirect(flask.url_for(config.access_denied_redirect_endpoint))

