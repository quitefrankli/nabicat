import json
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager

from web_app.config import ConfigManager

QREAD_STATIC = Path(__file__).resolve().parents[2] / "web_app" / "qread" / "static"


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
        assert b'Send a file' in response.data
        for asset in ("qread.js", "qstream.js", "style.css", "vendor/jsQR.js", "vendor/qrcode.js"):
            assert client.get(f"/qread/static/{asset}").status_code == 200


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_file_survives_animated_qr_stream_with_dropped_frames():
    config = ConfigManager()
    options = {
        "chunkBytes": config.qread_stream_chunk_bytes,
        "solitonC": config.qread_stream_soliton_c,
        "solitonDelta": config.qread_stream_soliton_delta,
        "qrVersion": config.qread_stream_qr_version,
        "qrLevel": config.qread_stream_qr_level,
        "quiet": config.qread_qr_quiet_modules,
        "pixels": 2,
        "fileBytes": 12_000,
        "dropEvery": 3,
    }
    completed = subprocess.run(
        ["node", str(Path(__file__).with_name("qstream_roundtrip.js")), str(QREAD_STATIC), json.dumps(options)],
        capture_output=True, text=True, timeout=120, check=True,
    )
    stats = json.loads(completed.stdout)
    assert stats["matches"]
    assert stats["name"] == "logo ✓.png"
    # A third of frames are lost, yet the file arrives well before a second full loop.
    assert stats["shown"] < stats["chunks"] * 2
