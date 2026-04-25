import subprocess
import time
from datetime import timedelta
from pathlib import Path
from flask import Flask
from flask_bootstrap import Bootstrap5
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _compute_static_version() -> str:
    try:
        repo_root = Path(__file__).resolve().parent.parent
        sha = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=repo_root, stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
        if sha:
            return sha
    except Exception:
        pass
    return str(int(time.time()))


STATIC_VERSION = _compute_static_version()


@app.url_defaults
def _add_static_version(endpoint, values):
    if endpoint and endpoint.endswith('static') and 'v' not in values:
        values['v'] = STATIC_VERSION

# Session configuration for longer-lasting sessions
# 30 days session lifetime - especially helpful for mobile/iOS users
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)

# Cookie settings for better mobile browser compatibility
app.config['SESSION_COOKIE_SECURE'] = True  # Only send over HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent XSS access to session cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection while allowing normal navigation

bootstrap = Bootstrap5(app)
csrf = CSRFProtect(app)