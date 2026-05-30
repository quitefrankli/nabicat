"""
UI tests for Tubio using Playwright.
"""
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


def test_tubio_page_loads(tubio_page):
    """Test that Tubio page loads correctly."""
    expect(tubio_page).to_have_title("Tubio")


def test_playlists_nav_present(tubio_page):
    """Test that Playlists nav item is present."""
    expect(tubio_page.locator("a", has_text="Playlists")).to_be_visible()


def test_search_nav_present(tubio_page):
    """Test that Search nav item is present."""
    expect(tubio_page.locator("a", has_text="Search")).to_be_visible()


def test_actions_dropdown_works(tubio_page):
    """Test that Actions dropdown opens and has items."""
    tubio_page.click("button:has-text('Actions')")
    
    # Check that dropdown menu is visible and has items
    expect(tubio_page.locator(".dropdown-menu:visible")).to_be_visible()
    # At least one dropdown item should be present
    assert tubio_page.locator(".dropdown-menu .dropdown-item").count() > 0


def test_search_tab_content(tubio_page):
    """Test that search tab has search input."""
    # Click on Search tab
    tubio_page.click("a:has-text('Search')")

    # Check for search input
    expect(tubio_page.locator("input[placeholder*='Search']")).to_be_visible()


def test_audio_elements_have_preload_none(tubio_page):
    """Audio elements must have preload=none to avoid overwhelming server on page load."""
    audio_elements = tubio_page.locator("audio")
    count = audio_elements.count()

    for i in range(count):
        preload = audio_elements.nth(i).get_attribute("preload")
        assert preload == "none", f"Audio element {i} has preload='{preload}', expected 'none'"


def test_no_failed_requests_on_page_load(tubio_page):
    """Page load should not trigger failed audio requests (503 errors)."""
    failed_requests = []

    def handle_response(response):
        if "/tubio/audio/" in response.url and response.status >= 400:
            failed_requests.append((response.url, response.status))

    tubio_page.on("response", handle_response)
    tubio_page.reload()
    tubio_page.wait_for_load_state("networkidle")

    assert len(failed_requests) == 0, f"Failed audio requests on page load: {failed_requests}"


def test_trackbar_volume_controls_present(tubio_page):
    """Persistent player exposes volume controls."""
    expect(tubio_page.locator("#trackbar-mute")).to_be_visible()
    expect(tubio_page.locator("#trackbar-volume")).to_be_visible()


def test_trackbar_volume_applies_to_audio_elements(tubio_page):
    """Changing the volume slider updates audio elements and persists the value."""
    tubio_page.evaluate("""
        const audio = document.createElement('audio');
        audio.id = 'audio-volume-test';
        document.body.appendChild(audio);
        initializeAudioEventListeners();
        initializeTrackbarVolume();
    """)

    tubio_page.locator("#trackbar-volume").evaluate("(el) => { el.value = '35'; el.dispatchEvent(new Event('input', { bubbles: true })); }")

    assert tubio_page.evaluate("document.getElementById('audio-volume-test').volume") == pytest.approx(0.35)
    assert tubio_page.evaluate("localStorage.getItem(document.getElementById('tubio-trackbar').dataset.volumeStorageKey)") == "35"
