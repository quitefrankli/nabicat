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


def test_new_metric_button_present(metrics_page):
    """Test that New Metric button is available in actions dropdown."""
    metrics_page.click("button:has-text('Actions')")
    expect(metrics_page.locator("button", has_text="New Metric")).to_be_visible()
