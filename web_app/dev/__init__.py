from flask import Blueprint, render_template

from web_app.config import ConfigManager
from web_app.dev.logs import register_logs_routes
from web_app.dev.map import register_map_routes
from web_app.dev.terminal import register_terminal_routes
from web_app.helpers import register_app_name, require_admin_blueprint


dev_api = Blueprint(
    'dev_api',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/dev')


require_admin_blueprint(
    dev_api,
    api_prefixes=ConfigManager().dev_access_denied_api_prefixes,
)
register_app_name(dev_api, 'Dev')


@dev_api.route('/', methods=['GET'])
def index():
    return render_template('dev_page.html')


register_logs_routes(dev_api)
register_map_routes(dev_api)
register_terminal_routes(dev_api)
