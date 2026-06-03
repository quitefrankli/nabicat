from unittest.mock import patch

from web_app.config import ConfigManager
from web_app.users import User
from web_app.app import app


def _ensure_routes():
    if "home" not in app.view_functions:
        app.add_url_rule("/", "home", lambda: "home")
    if "sentinel" not in app.blueprints:
        from web_app.sentinel import sentinel_api
        app.register_blueprint(sentinel_api)
    if "proxy" not in app.blueprints:
        from web_app.proxy import proxy_api
        app.register_blueprint(proxy_api)
    if "dev_api" not in app.blueprints:
        from web_app.dev import dev_api
        app.register_blueprint(dev_api)


def test_restricted_apps_redirect_home_with_error_flash():
    _ensure_routes()
    app.config["TESTING"] = True
    app.secret_key = "test-secret"
    cfg = ConfigManager()
    users = {
        "plain": User(username="plain", password="pw", folder="plain", is_admin=False, is_elevated=False),
        "elevated": User(username="elevated", password="pw", folder="elevated", is_admin=False, is_elevated=True),
    }

    with app.test_client() as client, patch("web_app.helpers.DataInterface") as mock_di:
        mock_di.return_value.load_users.return_value = users

        with client.session_transaction() as session:
            session["_user_id"] = "plain"
        response = client.get("/sentinel/")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
        with client.session_transaction() as session:
            assert ("error", cfg.elevated_access_denied_message) in session["_flashes"]

        with client.session_transaction() as session:
            session["_flashes"] = []
            session["_user_id"] = "elevated"
        for path in ("/proxy/", "/dev/"):
            response = client.get(path)
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/")
        with client.session_transaction() as session:
            assert session["_flashes"].count(("error", cfg.admin_access_denied_message)) == 2
