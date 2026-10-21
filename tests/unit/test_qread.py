from flask import Flask
from flask_login import LoginManager


def test_qread_is_public_and_serves_local_scanner_assets():
    from web_app.qread import qread_api

    app = Flask(__name__, template_folder="../../web_app/templates")
    app.config.update(SECRET_KEY="qread-test", WTF_CSRF_ENABLED=False)
    login = LoginManager(app)
    login.user_loader(lambda _: None)
    app.jinja_env.globals["csrf_token"] = lambda: "test"
    app.register_blueprint(qread_api)
    with app.test_client() as client:
        response = client.get("/qread/")
        assert response.status_code == 200
        assert b'id="start-camera"' in response.data
        assert b'Open camera' in response.data
        assert b'Upload photo' in response.data
        assert b'Generate QR code' in response.data
        for asset in ("qread.js", "style.css", "vendor/jsQR.js", "vendor/qrcode.js"):
            assert client.get(f"/qread/static/{asset}").status_code == 200
