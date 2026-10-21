from pathlib import Path
from threading import Thread

import pytest
from flask import Flask
from flask_login import LoginManager
from playwright.sync_api import expect
from werkzeug.serving import make_server


@pytest.fixture
def qread_url():
    from web_app.qread import qread_api

    app = Flask(
        __name__, template_folder=str(Path("web_app/templates").resolve()),
        static_folder=str(Path("web_app/static").resolve()),
    )
    app.config.update(SECRET_KEY="qread-test", WTF_CSRF_ENABLED=False)
    login = LoginManager(app)
    login.user_loader(lambda _: None)
    app.jinja_env.globals["csrf_token"] = lambda: "test"
    app.register_blueprint(qread_api)
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/qread/"
    server.shutdown()
    thread.join()


def test_text_can_be_encoded_downloaded_and_read_from_photo(page, qread_url, tmp_path):
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(qread_url)
    text = "Hello 猫 🐈\n<script>alert('QR')</script> & https://example.com/"
    page.get_by_label("Text to encode").fill(text)
    page.get_by_role("button", name="Generate QR code", exact=True).click()
    page.screenshot(path=str(tmp_path / "qread-mobile.png"), full_page=True)
    with page.expect_download() as download:
        page.get_by_role("link", name="Download PNG").click()
    image = tmp_path / "qr.png"
    download.value.save_as(image)
    page.get_by_label("Upload photo").set_input_files(image)
    expect(page.get_by_label("Decoded text")).to_have_value(text)
    expect(page.get_by_role("button", name="Copy text")).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="Clear", exact=True).click()
    expect(page.get_by_label("Decoded text")).to_have_value("")
    page.get_by_label("Upload photo").set_input_files({
        "name": "broken.png", "mimeType": "image/png", "buffer": b"not an image",
    })
    expect(page.locator("#scan-status")).to_contain_text("could not be opened")


def test_camera_decodes_and_releases_stream(page, qread_url):
    page.goto(qread_url)
    page.get_by_label("Text to encode").fill("Camera QR test")
    page.get_by_role("button", name="Generate QR code", exact=True).click()
    page.evaluate("""() => {
        window.testStream = document.querySelector('#generated-qr').captureStream();
        navigator.mediaDevices.getUserMedia = async (constraints) => {
            if (constraints.video.facingMode.ideal !== 'environment') throw Error('Rear camera required');
            const canvas = document.querySelector('#generated-qr');
            const frame = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height);
            const timer = setInterval(() => {
                canvas.getContext('2d').putImageData(frame, 0, 0);
                if (window.testStream.getTracks().every(track => track.readyState === 'ended')) clearInterval(timer);
            }, 50);
            return window.testStream;
        };
    }""")
    page.get_by_role("button", name="Start camera", exact=True).click()
    expect(page.get_by_label("Decoded text")).to_have_value("Camera QR test")
    assert page.evaluate("window.testStream.getTracks().every(track => track.readyState === 'ended')")
    expect(page.locator("#camera")).to_be_hidden()
    page.evaluate("() => { navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Denied', 'NotAllowedError'); }; }")
    page.get_by_role("button", name="Start camera", exact=True).click()
    expect(page.locator("#scan-status")).to_contain_text("permission")
