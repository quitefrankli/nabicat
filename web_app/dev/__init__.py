import flask_login
from pathlib import Path
from flask import render_template, Blueprint, jsonify, request, abort

dev_api = Blueprint(
    'dev_api',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/dev')

_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "web_app.log"
_MAX_LINES = 5000


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


@dev_api.route('/logs', methods=['GET'])
def get_logs():
    since = request.args.get('since', type=int)
    limit = min(request.args.get('limit', 2000, type=int), _MAX_LINES)

    try:
        all_lines = _LOG_PATH.read_text(errors='replace').splitlines()
        total = len(all_lines)
        if since is not None:
            lines = all_lines[since:]
            start = since
        else:
            start = max(0, total - limit)
            lines = all_lines[start:]
        return jsonify({'lines': lines, 'start': start, 'total': total})
    except FileNotFoundError:
        return jsonify({'lines': [], 'start': 0, 'total': 0})
