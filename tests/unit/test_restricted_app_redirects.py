from unittest.mock import patch

from web_app.config import ConfigManager
from web_app.users import User


def test_admin_apps_redirect_home_with_error_flash(app):
    cfg = ConfigManager()
    users = {
        "plain": User(username="plain", password="pw", folder="plain", is_admin=False, is_elevated=False),
    }

    with app.test_client() as client, patch("web_app.auth.DataInterface") as mock_di:
        mock_di.return_value.load_users.return_value = users

        with client.session_transaction() as session:
            session["_user_id"] = "plain"
        for path in ("/proxy/", "/dev/"):
            response = client.get(path)
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/")
        with client.session_transaction() as session:
            assert session["_flashes"].count(("error", cfg.admin_access_denied_message)) == 2
