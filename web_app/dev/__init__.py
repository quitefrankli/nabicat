import flask_login

from flask import Blueprint, abort, render_template

from web_app.dev.logs import register_logs_routes
from web_app.dev.map import register_map_routes
from web_app.dev.terminal import register_terminal_routes


dev_api = Blueprint(
    'dev_api',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/dev')


@dev_api.before_request
@flask_login.login_required
def before_request():
    if not flask_login.current_user.is_admin:
        abort(403)


@dev_api.context_processor
def inject_app_name():
    return dict(app_name='Dev')


@dev_api.route('/', methods=['GET'])
def index():
    return render_template('dev_page.html')


register_logs_routes(dev_api)
register_map_routes(dev_api)
register_terminal_routes(dev_api)
