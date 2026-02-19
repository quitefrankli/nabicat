import click
import logging
import flask_login
import http.cookiejar
import urllib.request

from git import Repo
from typing import * # type: ignore
from pathlib import Path
from flask import render_template, request, send_from_directory
from flask_apscheduler import APScheduler
from logging.handlers import RotatingFileHandler

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.helpers import get_ip, get_all_data_interfaces, register_all_blueprints
from web_app.app import app


register_all_blueprints(app)

@app.context_processor
def inject_app_name():
    return dict(app_name="NabiCat")

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

scheduler = APScheduler()


@scheduler.task('cron', id='scheduled_backup', day_of_week='sun', hour=0, minute=0, misfire_grace_time=3600)
def scheduled_backup():
    logging.info("Running scheduled backup")
    backup_dir = DataInterface().generate_backup_dir()
    DataInterface().backup_data(backup_dir)
    for data_interface_class in get_all_data_interfaces():
        data_interface_class().backup_data(backup_dir)
    logging.info("Backup complete")


@scheduler.task('cron', id='scheduled_cookie_keepalive', day='*', hour=4, minute=0, misfire_grace_time=3600)
def run_cookie_keepalive() -> None:
    logging.info("Running scheduled cookie keepalive")
    cookie_path = ConfigManager().tubio_cookie_path

    jar = http.cookiejar.MozillaCookieJar(cookie_path)
    jar.load(ignore_discard=True, ignore_expires=True)

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    ]

    try:
        response = opener.open("https://www.youtube.com/feed/subscriptions", timeout=30)
        jar.save(ignore_discard=True, ignore_expires=True)
        logging.info(f"Cookie keepalive OK - status {response.status}")
    except Exception:
        logging.exception("Cookie keepalive failed")


def start_scheduler() -> None:
    scheduler.init_app(app)
    if scheduler.running:
        return
    scheduler.start()

@app.before_request
def before_request():
    # Auto-login as admin in debug mode
    if ConfigManager().debug_mode and not flask_login.current_user.is_authenticated:
        user = DataInterface().load_users()["admin"]
        flask_login.login_user(user, remember=True)

    message = f"Processing request: client={get_ip()}, path={request.path}, method={request.method}"

    if request.method == 'POST':
        if request.is_json:
            message += f", json={request.get_json()}"
        elif request.form:
            message += f", form={request.form}"

    if len(message) > 500:
        message = message[:500] + f"... (truncated {len(message) - 500} characters)"
    logging.info(message)

@app.route('/')
def home():
    commit_hash = Repo(".").head.commit.hexsha
    return render_template('home.html', commit_hash=commit_hash)

@app.route('/service-worker.js')
def service_worker():
    """Serve service worker from root for proper scope"""
    return send_from_directory('static', 'service-worker.js', mimetype='application/javascript')

def configure_logging(debug: bool) -> None:
    log_path = Path("logs/web_app.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotating_log_handler = RotatingFileHandler(str(log_path),
                                                   maxBytes=int(1e6),
                                                   backupCount=10)
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, 
                        handlers=[] if debug else [rotating_log_handler],
                        format='%(asctime)s %(levelname)s %(message)s')

@click.command()
@click.option('--debug', is_flag=True, default=False)
@click.option('--port', default=80, type=int)
def cli_start(debug: bool, port: int):
    configure_logging(debug=debug)
    app.secret_key = ConfigManager().flask_secret_key
    ConfigManager().debug_mode = debug

    logging.info("Starting server")
    app.run(host='0.0.0.0', port=port, debug=debug)

if __name__ == '__main__':
    # Dev Startup
    cli_start()
else:
    # Prod Startup
    configure_logging(debug=False)
    app.secret_key = ConfigManager().flask_secret_key
    ConfigManager().debug_mode = False

    logging.info("Starting server")
    start_scheduler()
