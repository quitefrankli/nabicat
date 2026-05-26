from pathlib import Path

from flask import jsonify, request

from web_app.config import ConfigManager


_LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"


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


def _line_has_suppressed_path(line: str, suppressed_paths: set[str]) -> bool:
    return any(f"path={path}," in line or f"path={path} " in line for path in suppressed_paths)


def _read_log_lines(logs_dir: Path, file_count: int | None = None) -> list[str]:
    config = ConfigManager()
    files = _iter_recent_log_files(logs_dir, file_count or config.dev.log_viewer_file_count)
    suppressed_paths = config.request_log_suppressed_paths
    lines = []
    for path in files:
        try:
            lines.extend(
                line
                for line in path.read_text(errors='replace').splitlines()
                if not _line_has_suppressed_path(line, suppressed_paths)
            )
        except OSError:
            continue
    return lines


def register_logs_routes(dev_api):
    @dev_api.route('/logs', methods=['GET'])
    def get_logs():
        config = ConfigManager()
        since = request.args.get('since', type=int)
        limit = min(request.args.get('limit', 2000, type=int), config.dev.log_viewer_max_lines)

        all_lines = _read_log_lines(_LOGS_DIR)
        total = len(all_lines)
        if since is not None:
            lines = all_lines[since:]
            start = since
        else:
            start = max(0, total - limit)
            lines = all_lines[start:]
        return jsonify({'lines': lines, 'start': start, 'total': total})
