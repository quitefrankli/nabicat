"""Browser checks for File Store assets using isolated template data."""

from pathlib import Path

import pytest
import flask_bootstrap
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from playwright.sync_api import expect

from web_app.config import ConfigManager


@pytest.fixture
def file_store_page(page):
    config = ConfigManager().file_store
    env = Environment(loader=ChoiceLoader([
        DictLoader({"subpage_base.html": "{% block page_content %}{% endblock %}"}),
        FileSystemLoader("web_app/file_store/templates"),
    ]), autoescape=True)
    env.globals.update(url_for=lambda endpoint, **kwargs: "/thumb.png" if endpoint == ".thumbnail" else "/image/" + kwargs.get("filename", ""),
                       csrf_token=lambda: "test")
    name = "holiday-photographs-with-a-very-long-unbroken-filename" * 3 + ".jpg"
    file = dict(name=name, path=name, mime_type="image/jpeg", size_formatted="1 MB", kind="file")
    def open_page(mode, count=1):
        files = [dict(file, name=f"{index}-{name}", path=f"{index}-{name}") for index in range(count)]
        html = env.get_template("file_store_index.html").render(
            mode=mode, current_path="", storage_info=None,
            thumbnail_config=config,
            directory=dict(folders=[], files=files, items=files),
        )
        bootstrap = Path(flask_bootstrap.__file__).parent / "static/bootstrap5"
        css = (bootstrap / "css/bootstrap.min.css").read_text() + Path("web_app/static/style.css").read_text() + Path("web_app/file_store/static/style.css").read_text()
        script = (bootstrap / "js/bootstrap.min.js").read_text() + "\n" + Path("web_app/file_store/static/script.js").read_text()
        page.route("http://file-store.test/", lambda route: route.fulfill(
            content_type="text/html",
            body=f'<meta charset="utf-8"><style>* {{ box-sizing: border-box; }} body {{ margin: 0; }} {css}</style>{html}<script>{script}</script>',
        ))
        page.goto("http://file-store.test/")
    return open_page


def test_long_filename_is_fully_readable_on_phone(page, file_store_page, tmp_path):
    page.set_viewport_size({"width": 375, "height": 812})
    file_store_page("list")
    name = page.locator(".directory-link span")
    assert name.evaluate("(el) => el.scrollWidth <= el.clientWidth && getComputedStyle(el).whiteSpace !== 'nowrap'")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / "file-store-list.png"), full_page=True)
    actions = page.locator(".directory-actions")
    assert actions.bounding_box()["y"] >= name.bounding_box()["y"] + name.bounding_box()["height"]


def test_density_survives_reload_and_viewport_clamping(page, file_store_page):
    page.route("**/thumb.png", lambda route: route.abort())
    page.set_viewport_size({"width": 1200, "height": 800})
    file_store_page("grid")
    slider = page.locator("#galleryColumns")
    slider.fill("9")
    page.reload()
    expect(slider).to_have_value("9")
    page.set_viewport_size({"width": 375, "height": 812})
    expect(slider).not_to_have_value("9")
    page.reload()
    page.set_viewport_size({"width": 1200, "height": 800})
    expect(slider).to_have_value("9")


def test_thumbnail_failure_can_be_retried(page, file_store_page):
    requests = []
    def thumbnail(route):
        requests.append(route.request.url)
        if len(requests) <= ConfigManager().file_store.thumbnail_load_max_retries + 1:
            route.abort()
        else:
            route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
    page.route("**/thumb.png*", thumbnail)
    file_store_page("grid")
    expect(page.locator(".thumbnail-status")).to_contain_text("Loading")
    retry = page.get_by_role("button", name="Retry thumbnail")
    expect(retry).to_be_visible(timeout=10000)
    retry.click()
    expect(page.locator(".thumbnail-status")).to_be_hidden()
    assert "_ts=" in requests[-1]
    assert page.locator("[data-thumbnail-src]").evaluate("(el) => el.naturalWidth > 0")



def drag_preview(page, x, y, *, release=True):
    stage = page.locator(".image-preview-stage")
    stage.evaluate("""(el, delta) => {
        const bounds = el.getBoundingClientRect();
        const start = {clientX: bounds.left + bounds.width / 2, clientY: bounds.top + bounds.height / 2};
        const send = (type, touches) => {
            const event = new Event(type, {bubbles: true, cancelable: true});
            Object.defineProperty(event, 'touches', {value: touches});
            el.dispatchEvent(event);
        };
        send('touchstart', [start]);
        send('touchmove', [{clientX: start.clientX + delta.x, clientY: start.clientY + delta.y}]);
        if (delta.release) send('touchend', []);
    }""", dict(x=x, y=y, release=release))


def open_preview(page, file_store_page):
    page.set_viewport_size({"width": 375, "height": 812})
    page.route("**/thumb.png*", lambda route: route.abort())
    page.route("**/image/**", lambda route: route.fulfill(
        content_type="image/svg+xml",
        body='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="seagreen"/></svg>',
    ))
    file_store_page("grid", count=3)
    page.locator("[data-image-url]").first.click()
    expect(page.locator("#imageModal")).to_be_visible()
    expect(page.locator("#imageModal")).to_have_class("modal fade show")


def test_mobile_preview_drags_neighbours_and_springs_back_at_boundaries(page, file_store_page, tmp_path):
    open_preview(page, file_store_page)
    original = page.locator("#modalImage").get_attribute("src")
    drag_preview(page, -150, 0, release=False)
    neighbour = page.locator(".image-preview-neighbour").last.bounding_box()
    assert 0 < neighbour["x"] < 375
    page.screenshot(path=str(tmp_path / "preview-drag.png"))
    page.locator(".image-preview-stage").evaluate("""el => {
        const event = new Event('touchend', {bubbles: true});
        Object.defineProperty(event, 'touches', {value: []});
        el.dispatchEvent(event);
    }""")
    expect(page.locator("#modalImage")).not_to_have_attribute("src", original)
    expect(page.locator(".image-preview-stage")).not_to_have_class("image-preview-stage is-settling")
    drag_preview(page, 150, 0)
    expect(page.locator("#modalImage")).to_have_attribute("src", original)
    expect(page.locator(".image-preview-stage")).not_to_have_class("image-preview-stage is-settling")
    drag_preview(page, 150, 0)
    expect(page.locator(".image-preview-stage")).not_to_have_class("image-preview-stage is-settling")
    expect(page.locator("#modalImage")).to_have_attribute("src", original)
    drag_preview(page, -20, 0)
    expect(page.locator(".image-preview-stage")).not_to_have_class("image-preview-stage is-settling")
    expect(page.locator("#modalImage")).to_have_attribute("src", original)


@pytest.mark.parametrize("direction", [-1, 1])
def test_mobile_preview_dismisses_vertically_and_reopens_cleanly(page, file_store_page, direction):
    open_preview(page, file_store_page)
    drag_preview(page, 0, direction * 30)
    expect(page.locator(".image-preview-stage")).not_to_have_class("image-preview-stage is-settling")
    expect(page.locator("#imageModal")).to_be_visible()
    drag_preview(page, 0, direction * 300, release=False)
    scale = page.locator(".image-preview-stage").evaluate("(el) => Number(getComputedStyle(el).getPropertyValue('--preview-scale'))")
    assert scale < 1
    page.locator(".image-preview-stage").evaluate("""el => {
        const event = new Event('touchend', {bubbles: true});
        Object.defineProperty(event, 'touches', {value: []});
        el.dispatchEvent(event);
    }""")
    expect(page.locator("#imageModal")).to_be_hidden()
    page.locator("[data-image-url]").first.click()
    expect(page.locator("#imageModal")).to_be_visible()
    assert page.locator(".image-preview-stage").evaluate("(el) => !el.style.getPropertyValue('--preview-y')")
