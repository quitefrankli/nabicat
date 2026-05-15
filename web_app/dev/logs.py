from pathlib import Path

from flask import jsonify, request


_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "web_app.log"
_MAX_LINES = 5000


def register_logs_routes(dev_api):
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
