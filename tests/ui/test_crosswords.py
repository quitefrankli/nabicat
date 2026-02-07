"""
UI tests for Crosswords using Playwright.
"""
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


def test_crosswords_page_loads(crosswords_page):
    """Test that Crosswords page loads correctly (admin only)."""
    # Crosswords uses the default app title "LazyWombat"
    expect(crosswords_page).to_have_title("LazyWombat")


def test_generate_button_present(crosswords_page):
    """Test that Generate New Crossword button is present."""
    expect(crosswords_page.locator("button:has-text('Generate')")).to_be_visible()


def test_crosswords_icon_visible(crosswords_page):
    """Test that the crosswords icon/illustration is visible."""
    # Check for grid icon or similar
    expect(crosswords_page.locator("i.bi-grid-3x3, i.bi-grid-3x3-gap").first).to_be_visible()


def test_actions_dropdown(crosswords_page):
    """Test that Actions dropdown is present."""
    expect(crosswords_page.locator("button:has-text('Actions')")).to_be_visible()
