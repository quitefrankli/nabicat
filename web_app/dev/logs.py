from pathlib import Path

from flask import jsonify, request

from web_app.config import ConfigManager


def _log_sort_key(path: Path) -> int | None:
    if path.name == "web_app.log":
        return 0
    prefix = "web_app.log."
    if not path.name.startswith(prefix):
        return None
    suffix = path.name[len(prefix):]
    return int(suffix) if suffix.isdigit() else None


def _iter_recent_log_files(logs_dir: Path, file_count: int) -> list[Path]:
    files = []
    for path in logs_dir.glob("web_app.log*"):
        if not path.is_file():
            continue
        sort_key = _log_sort_key(path)
        if sort_key is not None:
            files.append((sort_key, path))
    return [path for _, path in sorted(files)[:file_count]][::-1]


def _read_log_lines(logs_dir: Path, file_count: int | None = None) -> list[str]:
    config = ConfigManager()
    files = _iter_recent_log_files(logs_dir, file_count or config.dev.log_viewer_file_count)
    lines = []
    for path in files:
        try:
            lines.extend(path.read_text(errors='replace').splitlines())
        except OSError:
            continue
    return lines


def get_logs():
    config = ConfigManager()
    since = request.args.get('since', type=int)
    requested_limit = request.args.get('limit', type=int)

    all_lines = _read_log_lines(config.log_file_path.parent)
    total = len(all_lines)
    if since is not None:
        lines = all_lines[since:]
        start = since
    elif requested_limit is not None:
        limit = min(requested_limit, config.dev.log_viewer_max_lines)
        start = max(0, total - limit)
        lines = all_lines[start:]
    else:
        start = 0
        lines = all_lines
    return jsonify({'lines': lines, 'start': start, 'total': total})


def register_logs_routes(dev_api):
    dev_api.add_url_rule('/logs', view_func=get_logs, methods=['GET'])
