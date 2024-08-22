import flask_login

import flask
from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from web_app.app import app
from web_app.data_interface import DataInterface
from web_app.users import User


login_manager = flask_login.LoginManager()
login_manager.init_app(app)
@login_manager.user_loader
def user_loader(username: str) -> User | None:
    users = DataInterface.instance().load_users()
    return users.get(username, None)

@login_manager.request_loader
def request_loader(request: flask.Request) -> User | None:
    username = request.form.get('username')
    existing_users = DataInterface.instance().load_users()
    return existing_users.get(username, None)

@login_manager.unauthorized_handler
def unauthorized_handler():
    flask.flash('Log in required', category='error')
    return flask.redirect(flask.url_for('account_api.login'))

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["1 per second"],
    storage_uri="memory://",
    strategy="fixed-window", # or "moving-window"
)

def from_req(key: str) -> str:
    val = request.form[key] if key in request.form else request.args[key]
    return val.encode('ascii', 'ignore').decode('ascii')