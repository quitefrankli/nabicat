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
    from urllib.parse import urlencode
    def url_for(endpoint, **kwargs):
        path = ("/file_store/thumb/" if endpoint == ".thumbnail" else "/file_store/image/") + kwargs.pop("filename", "")
        return path + "?" + urlencode({key: value for key, value in kwargs.items() if key != "filename"})
    env.globals.update(url_for=url_for, csrf_token=lambda: "test")
    name = "holiday-photographs-with-a-very-long-unbroken-filename" * 3 + ".jpg"
    file = dict(name=name, path=name, mime_type="image/jpeg", size_formatted="1 MB", kind="file")
    def open_page(mode, count=1, scope="user-a", version=1):
        files = [dict(file, name=f"{index}-{name}", path=f"{index}-{name}", crc=version) for index in range(count)]
        html = env.get_template("file_store_index.html").render(
            mode=mode, current_path="", storage_info=None,
            thumbnail_config=config, image_cache_name="nabicat-cache-v2", image_cache_scope=scope,
            directory=dict(folders=[], files=files, items=files),
        )
        bootstrap = Path(flask_bootstrap.__file__).parent / "static/bootstrap5"
        css = (bootstrap / "css/bootstrap.min.css").read_text() + Path("web_app/static/style.css").read_text() + Path("web_app/file_store/static/style.css").read_text()
        script = (bootstrap / "js/bootstrap.min.js").read_text() + "\n" + Path("web_app/file_store/static/image-cache.js").read_text() + "\n" + Path("web_app/file_store/static/script.js").read_text()
        page.route("http://localhost/", lambda route: route.fulfill(
            content_type="text/html",
            body=f'<meta charset="utf-8"><style>* {{ box-sizing: border-box; }} body {{ margin: 0; }} {css}</style>{html}<script>{script}</script>',
        ))
        page.goto("http://localhost/")
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
    page.route("**/file_store/thumb/**", lambda route: route.abort())
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
    page.route("**/file_store/thumb/**", thumbnail)
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
    page.route("**/file_store/thumb/**", lambda route: route.abort())
    page.route("**/file_store/image/**", lambda route: route.fulfill(
        content_type="image/svg+xml",
        body='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="seagreen"/></svg>',
    ))
    file_store_page("grid", count=3)
    page.locator("[data-image-url]").first.click()
    expect(page.locator("#imageModal")).to_be_visible()
    expect(page.locator("#imageModal")).to_have_class("modal fade show")
    expect(page.locator("#imageModal .modal-dialog")).to_have_css("transform", "none")
    expect(page.locator("#modalImage")).to_have_js_property("naturalWidth", 600)


def test_mobile_preview_drags_neighbours_and_springs_back_at_boundaries(page, file_store_page, tmp_path):
    open_preview(page, file_store_page)
    original = page.locator("#modalImage").get_attribute("data-image-source")
    drag_preview(page, -150, 0, release=False)
    neighbour = page.locator(".image-preview-neighbour").last.bounding_box()
    assert 0 < neighbour["x"] < 375
    page.screenshot(path=str(tmp_path / "preview-drag.png"))
    page.locator(".image-preview-stage").evaluate("""el => {
        const event = new Event('touchend', {bubbles: true});
        Object.defineProperty(event, 'touches', {value: []});
        el.dispatchEvent(event);
    }""")
    expect(page.locator("#modalImage")).not_to_have_attribute("data-image-source", original)
    expect(page.locator(".image-preview-stage")).not_to_have_class("image-preview-stage is-settling")
    drag_preview(page, 150, 0)
    expect(page.locator("#modalImage")).to_have_attribute("data-image-source", original)
    expect(page.locator(".image-preview-stage")).not_to_have_class("image-preview-stage is-settling")
    drag_preview(page, 150, 0)
    expect(page.locator(".image-preview-stage")).not_to_have_class("image-preview-stage is-settling")
    expect(page.locator("#modalImage")).to_have_attribute("data-image-source", original)
    drag_preview(page, -20, 0)
    expect(page.locator(".image-preview-stage")).not_to_have_class("image-preview-stage is-settling")
    expect(page.locator("#modalImage")).to_have_attribute("data-image-source", original)


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


def test_mobile_double_tap_zooms_pans_and_returns_to_swipe_navigation(page, file_store_page, tmp_path):
    open_preview(page, file_store_page)
    image = page.locator("#modalImage")
    stage = page.locator(".image-preview-stage")
    expect(page.locator(".image-preview-close")).to_be_hidden()
    expect(page.locator(".image-preview-hint")).to_have_count(0)
    expect(image).to_have_js_property("naturalWidth", 600)
    original = image.get_attribute("data-image-source")

    def double_tap():
        stage.evaluate("""el => {
            const bounds = el.getBoundingClientRect();
            const touch = {clientX: bounds.left + bounds.width / 4, clientY: bounds.top + bounds.height / 2};
            for (let tap = 0; tap < 2; tap++) {
                for (const type of ['touchstart', 'touchend']) {
                    const event = new Event(type, {bubbles: true, cancelable: true});
                    Object.defineProperty(event, 'touches', {value: type === 'touchstart' ? [touch] : []});
                    el.dispatchEvent(event);
                }
            }
        }""")

    double_tap()
    assert image.evaluate("(el) => Number(el.style.getPropertyValue('--preview-zoom'))") == ConfigManager().file_store.preview_double_tap_zoom
    pan_before = image.evaluate("(el) => Number.parseFloat(el.style.getPropertyValue('--preview-pan-x'))")
    assert pan_before > 0
    drag_preview(page, -100, 150)
    assert image.evaluate("(el) => Number.parseFloat(el.style.getPropertyValue('--preview-pan-x'))") < pan_before
    expect(image).to_have_attribute("data-image-source", original)
    expect(page.locator("#imageModal")).to_be_visible()
    page.screenshot(path=str(tmp_path / "preview-zoom.png"))

    double_tap()
    assert image.evaluate("(el) => Number(el.style.getPropertyValue('--preview-zoom'))") == 1
    assert image.evaluate("(el) => Number.parseFloat(el.style.getPropertyValue('--preview-pan-x'))") == 0
    drag_preview(page, -150, 0)
    expect(image).not_to_have_attribute("data-image-source", original)
    expect(stage).not_to_have_class("image-preview-stage is-settling")
    double_tap()
    assert image.evaluate("(el) => Number(el.style.getPropertyValue('--preview-zoom'))") > 1
    page.keyboard.press("Escape")
    expect(page.locator("#imageModal")).to_be_hidden()
    page.locator("[data-image-url]").first.click()
    expect(page.locator("#imageModal")).to_be_visible()
    assert image.evaluate("(el) => !el.style.getPropertyValue('--preview-zoom')")
    page.set_viewport_size({"width": 1200, "height": 800})
    expect(page.locator(".image-preview-close")).to_be_visible()


def test_private_image_cache_survives_reload_and_versions_and_users_are_separate(page, file_store_page):
    requests = []
    def serve(route):
        requests.append(route.request.url)
        route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"/>')
    page.route("**/file_store/**", serve)
    page.set_viewport_size({"width": 375, "height": 812})
    file_store_page("grid", count=2)
    expect(page.locator('[data-thumbnail-state="loaded"]')).to_have_count(2)
    page.locator("[data-image-url]").first.click()
    expect(page.locator("#modalImage")).to_have_js_property("naturalWidth", 600)
    page.wait_for_function("""async () => (await (await caches.open('nabicat-cache-v2')).keys()).length === 4""")
    expect(page.locator(".image-preview-neighbour img").last).to_have_js_property("naturalWidth", 600)
    assert len(requests) == 4

    requests.clear()
    page.reload()
    expect(page.locator('[data-thumbnail-state="loaded"]')).to_have_count(2)
    page.locator("[data-image-url]").first.click()
    expect(page.locator("#modalImage")).to_have_js_property("naturalWidth", 600)
    assert requests == []

    file_store_page("grid", count=2, scope="user-b")
    expect(page.locator('[data-thumbnail-state="loaded"]')).to_have_count(2)
    assert len(requests) == 2
    requests.clear()
    file_store_page("grid", count=2, version=2)
    expect(page.locator('[data-thumbnail-state="loaded"]')).to_have_count(2)
    assert len(requests) == 2
    # The existing logout/cache-clear manager removes private entries too.
    page.add_script_tag(content=Path("web_app/static/cache-manager.js").read_text())
    page.evaluate("window.cacheManager.clearCache()")
    assert page.evaluate("caches.keys()") == []


def test_cached_thumbnail_bypasses_blocked_network_queue_and_viewer_preempts_it(page, file_store_page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"/>'
    page.route("**/file_store/**", lambda route: route.fulfill(content_type="image/svg+xml", body=svg))
    file_store_page("grid", count=3)
    expect(page.locator('[data-thumbnail-state="loaded"]')).to_have_count(3)
    sources = page.locator("[data-thumbnail-src]").evaluate_all("(els) => els.map(el => el.dataset.thumbnailSrc)")
    page.wait_for_function("""async () => (await (await caches.open('nabicat-cache-v2')).keys()).length === 3""")
    page.evaluate("""async source => {
        const cache = await caches.open('nabicat-cache-v2');
        await cache.delete(new URL(source, location.href).href);
    }""", sources[0])
    pending = []
    page.route("**/file_store/thumb/**", lambda route: pending.append(route))
    page.reload()
    expect(page.locator('[data-thumbnail-state="loaded"]')).to_have_count(2)
    expect(page.locator(".file-grid-item").first).to_have_attribute("data-thumbnail-state", "loading")
    page.wait_for_function("document.querySelector('[data-thumbnail-src]').naturalWidth === 1")
    assert len(pending) == 1

    failed = []
    page.on("requestfailed", lambda request: failed.append(request.url))
    page.locator("[data-image-url]").first.click()
    expect(page.locator("#modalImage")).to_have_js_property("naturalWidth", 600)
    assert any("/file_store/thumb/" in url for url in failed)
    assert len(pending) == 1
    # Release the test's intercepted request after observing the browser cancellation.
    pending[0].abort()
    expect(page.locator("#imageModal .modal-dialog")).to_have_css("transform", "none")
    page.wait_for_function("bootstrap.Modal.getInstance(document.querySelector('#imageModal'))._isTransitioning === false")
    page.locator("#imageModal").focus()
    with page.expect_request("**/file_store/thumb/**", timeout=5000):
        page.keyboard.press("Escape")
    expect(page.locator("#imageModal")).to_be_hidden()
    assert not errors, errors
    # Closing resumes the cancelled thumbnail without consuming a failure retry.
    assert len(pending) == 2
    assert "retry=" not in pending[-1].request.url
    pending[-1].fulfill(content_type="image/svg+xml", body=svg)
    expect(page.locator('[data-thumbnail-state="loaded"]')).to_have_count(3)



def test_image_loading_falls_back_when_browser_storage_is_blocked(page, file_store_page):
    page.add_init_script("""Object.defineProperty(window, 'caches', {
        value: {open() { throw new DOMException('Storage blocked', 'SecurityError'); }}
    });""")
    requests = []
    def serve(route):
        requests.append(route.request.url)
        route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"/>')
    page.route("**/file_store/**", serve)
    file_store_page("grid")
    expect(page.locator('[data-thumbnail-state="loaded"]')).to_have_count(1)
    page.reload()
    expect(page.locator('[data-thumbnail-state="loaded"]')).to_have_count(1)
    assert len(requests) == 2
    page.locator("[data-image-url]").first.click()
    expect(page.locator("#modalImage")).to_have_js_property("naturalWidth", 600)
