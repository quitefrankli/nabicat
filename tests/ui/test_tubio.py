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
    """Persistent player exposes volume controls in a compact popover."""
    expect(tubio_page.locator("#trackbar-mute")).to_be_visible()
    expect(tubio_page.locator("#trackbar-volume")).not_to_be_visible()
    tubio_page.locator("#trackbar-mute").focus()
    expect(tubio_page.locator("#trackbar-volume")).to_be_visible()


def test_trackbar_actions_align_with_main_controls(tubio_page):
    """Volume and playlist controls align with the main playback controls."""
    positions = tubio_page.evaluate("""
        () => {
            const play = document.getElementById('trackbar-playpause').getBoundingClientRect();
            const volume = document.getElementById('trackbar-mute').getBoundingClientRect();
            const playlist = document.querySelector('.trackbar-actions > button').getBoundingClientRect();
            const trackbar = document.getElementById('tubio-trackbar').getBoundingClientRect();
            return {
                playCenterY: play.top + play.height / 2,
                volumeCenterY: volume.top + volume.height / 2,
                playlistCenterY: playlist.top + playlist.height / 2,
                playCenterX: play.left + play.width / 2,
                trackbarCenterX: trackbar.left + trackbar.width / 2,
                playlistRight: playlist.right,
                trackbarRight: trackbar.right
            };
        }
    """)

    assert abs(positions["playCenterY"] - positions["volumeCenterY"]) <= 2
    assert abs(positions["playCenterY"] - positions["playlistCenterY"]) <= 2
    assert abs(positions["playCenterX"] - positions["trackbarCenterX"]) <= 24
    assert positions["trackbarRight"] - positions["playlistRight"] <= 24


def test_trackbar_volume_hover_path_stays_open(tubio_page):
    """Desktop hover path from the volume icon to slider keeps the popover open."""
    tubio_page.locator("#trackbar-mute").hover()
    expect(tubio_page.locator("#trackbar-volume")).to_be_visible()

    positions = tubio_page.evaluate("""
        () => {
            const button = document.getElementById('trackbar-mute').getBoundingClientRect();
            const popover = document.getElementById('trackbar-volume-popover').getBoundingClientRect();
            return {
                x: button.left + button.width / 2,
                bridgeY: popover.bottom + ((button.top - popover.bottom) / 2),
                sliderY: popover.top + popover.height / 2
            };
        }
    """)
    tubio_page.mouse.move(positions["x"], positions["bridgeY"])
    expect(tubio_page.locator("#trackbar-volume")).to_be_visible()
    tubio_page.mouse.move(positions["x"], positions["sliderY"])
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
