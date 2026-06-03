import flask_login

from flask import Blueprint, render_template

from web_app.config import ConfigManager
from web_app.dev.logs import register_logs_routes
from web_app.dev.map import register_map_routes
from web_app.dev.terminal import register_terminal_routes
from web_app.helpers import redirect_with_access_denied


dev_api = Blueprint(
    'dev_api',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/dev')


@dev_api.before_request
@flask_login.login_required
def before_request():
    config = ConfigManager()
    if not flask_login.current_user.is_admin:
        return redirect_with_access_denied(
            config.admin_access_denied_message,
            config.dev_access_denied_api_prefixes,
        )


@dev_api.context_processor
def inject_app_name():
    return dict(app_name='Dev')


@dev_api.route('/', methods=['GET'])
def index():
    return render_template('dev_page.html')


register_logs_routes(dev_api)
register_map_routes(dev_api)
register_terminal_routes(dev_api)
