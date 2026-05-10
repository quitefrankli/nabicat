import os
import pty
import fcntl
import struct
import termios
import select
import signal
import subprocess
import threading
import time
import uuid
import flask_login

from pathlib import Path
from flask import render_template, Blueprint, jsonify, request, abort

from web_app.app import csrf
from web_app.config import ConfigManager
from web_app.helpers import limiter

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


# ---------------------------------------------------------------------------
# In-browser terminal (PTY-backed bash)
# ---------------------------------------------------------------------------

_sessions: dict[str, "TerminalSession"] = {}
_sessions_lock = threading.Lock()


class TerminalSession:
    def __init__(self, sid: str, shell: str, buffer_cap: int):
        self.sid = sid
        self.buffer_cap = buffer_cap
        self.buffer = bytearray()
        self.dropped = 0  # bytes dropped off the front
        self.lock = threading.Lock()
        self.last_active = time.time()
        self.alive = True

        master_fd, slave_fd = pty.openpty()
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        except OSError:
            pass

        env = {**os.environ, 'TERM': 'xterm-256color'}
        self.process = subprocess.Popen(
            [shell, '-l'],
            preexec_fn=os.setsid,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            env=env,
            close_fds=True,
        )
        os.close(slave_fd)

        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.master_fd = master_fd

        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    def _read_loop(self):
        chunk_size = ConfigManager().dev_terminal_read_chunk
        while self.alive:
            try:
                ready, _, _ = select.select([self.master_fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not ready:
                if self.process.poll() is not None:
                    break
                continue
            try:
                data = os.read(self.master_fd, chunk_size)
            except OSError:
                break
            if not data:
                break
            with self.lock:
                self.buffer.extend(data)
                excess = len(self.buffer) - self.buffer_cap
                if excess > 0:
                    del self.buffer[:excess]
                    self.dropped += excess
        self.alive = False

    def write(self, data: bytes):
        try:
            os.write(self.master_fd, data)
            self.last_active = time.time()
        except OSError:
            self.alive = False

    def read_since(self, since: int) -> tuple[bytes, int]:
        with self.lock:
            end = self.dropped + len(self.buffer)
            start = max(since, self.dropped)
            offset = start - self.dropped
            return bytes(self.buffer[offset:]), end

    def resize(self, rows: int, cols: int):
        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ,
                        struct.pack('HHHH', rows, cols, 0, 0))
        except OSError:
            pass

    def close(self):
        self.alive = False
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.process.terminate()
        except Exception:
            pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass


def _reap_idle_sessions():
    now = time.time()
    timeout = ConfigManager().dev_terminal_idle_timeout_s
    stale = [sid for sid, s in _sessions.items()
             if not s.alive or (now - s.last_active) > timeout]
    for sid in stale:
        s = _sessions.pop(sid, None)
        if s:
            s.close()


def _get_session(sid: str | None) -> TerminalSession | None:
    if not sid:
        return None
    return _sessions.get(sid)


@dev_api.route('/terminal/start', methods=['POST'])
@csrf.exempt
@limiter.exempt
def terminal_start():
    config = ConfigManager()
    with _sessions_lock:
        _reap_idle_sessions()
        if len(_sessions) >= config.dev_terminal_max_sessions:
            return jsonify({'error': 'session limit reached'}), 429
        sid = uuid.uuid4().hex
        sess = TerminalSession(sid, config.dev_terminal_shell, config.dev_terminal_buffer_bytes)
        _sessions[sid] = sess
    return jsonify({'sid': sid})


@dev_api.route('/terminal/output', methods=['GET'])
@limiter.exempt
def terminal_output():
    sess = _get_session(request.args.get('sid'))
    if not sess:
        return jsonify({'error': 'no session'}), 404
    since = request.args.get('since', 0, type=int)
    data, total = sess.read_since(since)
    return jsonify({
        'data': data.decode('utf-8', errors='replace'),
        'total': total,
        'alive': sess.alive,
    })


@dev_api.route('/terminal/input', methods=['POST'])
@csrf.exempt
@limiter.exempt
def terminal_input():
    sess = _get_session(request.args.get('sid'))
    if not sess or not sess.alive:
        return jsonify({'error': 'no session'}), 404
    sess.write(request.get_data())
    return ('', 204)


@dev_api.route('/terminal/resize', methods=['POST'])
@csrf.exempt
@limiter.exempt
def terminal_resize():
    sess = _get_session(request.args.get('sid'))
    if not sess or not sess.alive:
        return jsonify({'error': 'no session'}), 404
    body = request.get_json(silent=True) or {}
    rows = max(1, min(int(body.get('rows', 24)), 500))
    cols = max(1, min(int(body.get('cols', 80)), 500))
    sess.resize(rows, cols)
    return ('', 204)


@dev_api.route('/terminal/close', methods=['POST'])
@csrf.exempt
@limiter.exempt
def terminal_close():
    sid = request.args.get('sid')
    with _sessions_lock:
        sess = _sessions.pop(sid, None)
    if sess:
        sess.close()
    return ('', 204)
