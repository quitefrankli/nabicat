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
