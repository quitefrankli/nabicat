import flask
import flask_login
import logging
import re

from typing import * # type: ignore
from datetime import timedelta
from flask import request, Blueprint, render_template

from web_app.data_interface import DataInterface
from web_app.api.data_interface import DataInterface as APIDataInterface
from web_app.todoist2.data_interface import DataInterface as Todoist2DataInterface
from web_app.metrics.data_interface import DataInterface as MetricsDataInterface
from web_app.jswipe.data_interface import DataInterface as JSwipeDataInterface
from web_app.tubio.data_interface import DataInterface as TubioDataInterface
from web_app.file_store.data_interface import DataInterface as FileStoreDataInterface
from web_app.helpers import limiter, from_req


account_api = Blueprint('account_api', __name__, url_prefix='/account')

def get_default_redirect():
    return flask.redirect(flask.url_for('.login'))

def _delete_user_data(user) -> None:
    APIDataInterface().delete_user_data(user)
    Todoist2DataInterface().delete_user_data(user)
    MetricsDataInterface().delete_user_data(user)
    JSwipeDataInterface().delete_user_data(user)
    TubioDataInterface().delete_user_data(user)
    FileStoreDataInterface().delete_user_data(user)

@account_api.route('/login', methods=["GET", "POST"])
@limiter.limit("2/second")
def login():
    next_url = request.args.get('next') or request.form.get('next')
    if request.method == "GET":
        return render_template('login.html', next=next_url)
    
    username = from_req('username')
    password = from_req('password')
    existing_users = DataInterface().load_users()
    if username in existing_users and password == existing_users[username].password:
        flask_login.login_user(existing_users[username], remember=True)
        # Validate next_url to prevent open redirect vulnerabilities
        if next_url and next_url.startswith('/'):
            return flask.redirect(next_url)
        return flask.redirect(flask.url_for('home'))
    else:
        flask.flash('Invalid username or password', category='error')
        return get_default_redirect()

@account_api.route('/logout')
@flask_login.login_required
def logout():
    flask_login.logout_user()
    flask.flash('You have been logged out', category='info')
    return flask.redirect(flask.url_for('home'))

@account_api.route('/delete', methods=["GET", "POST"])
@flask_login.login_required
@limiter.limit("2/second", key_func=lambda: flask_login.current_user.id)
def delete_account():
    if request.method == "GET":
        return render_template('account_delete.html')

    password = request.form.get('password', '')
    existing_users = DataInterface().load_users()
    current_user_id = flask_login.current_user.id

    if current_user_id not in existing_users:
        flask_login.logout_user()
        flask.flash('Account not found', category='error')
        return get_default_redirect()

    user = existing_users[current_user_id]
    if password != user.password:
        flask.flash('Password is incorrect', category='error')
        return flask.redirect(flask.url_for('.delete_account'))

    admin_count = sum(1 for existing_user in existing_users.values() if existing_user.is_admin)
    if user.is_admin and admin_count <= 1:
        flask.flash('Cannot delete the last admin account', category='error')
        return flask.redirect(flask.url_for('.delete_account'))

    del existing_users[current_user_id]
    DataInterface().save_users(list(existing_users.values()))
    _delete_user_data(user)

    flask_login.logout_user()
    flask.flash('Your account has been deleted', category='info')
    return flask.redirect(flask.url_for('home'))

@account_api.route('/register', methods=["POST"])
@limiter.limit("1/second")
def register():
    username = from_req('username')
    password = from_req('password')

    if not username or not password:
        flask.flash('Username and password are required', category='error')
        return get_default_redirect()
    
    # password regex for only visible ascii characters
    validation_regex = re.compile(r'^[!-~]+$')
    if not validation_regex.match(username) or not validation_regex.match(password):
        flask.flash('Username and password must only contain visible ascii characters', category='error')
        return get_default_redirect()

    existing_users = DataInterface().load_users()
    if username in existing_users:
        flask.flash('User already exists', category='error')
        return get_default_redirect()

    new_user = DataInterface().generate_new_user(username, password)
    existing_users[username] = new_user
    DataInterface().save_users(list(existing_users.values()))
    logging.info(f"Registered new user: {username}")

    flask_login.login_user(new_user, remember=True)

    return flask.redirect(flask.url_for('home'))