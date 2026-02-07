"""
UI tests for Metrics using Playwright.
"""
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


def test_metrics_page_loads(metrics_page):
    """Test that Metrics page loads correctly."""
    # Check page title
    expect(metrics_page).to_have_title("Metrics")


def test_dashboard_navigation(metrics_page):
    """Test that Dashboard nav item is present."""
    expect(metrics_page.locator("a", has_text="Dashboard")).to_be_visible()


def test_actions_dropdown(metrics_page):
    """Test that Actions dropdown is present."""
    expect(metrics_page.locator("button:has-text('Actions')")).to_be_visible()


def test_metrics_has_content(metrics_page):
    """Test that Metrics page has some content loaded."""
    # Check for main content area
    expect(metrics_page.locator(".card").first).to_be_visible()
