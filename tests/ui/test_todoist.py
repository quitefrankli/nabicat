"""
UI tests for Todoist using Playwright.
"""
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


def test_todoist_page_loads(todoist_page):
    """Test that Todoist page loads correctly."""
    expect(todoist_page).to_have_title("Todoist")


def test_goals_navigation(todoist_page):
    """Test that Goals nav item is present and active."""
    expect(todoist_page.locator("a", has_text="Goals")).to_be_visible()


def test_completed_goals_link(todoist_page, test_server):
    """Test navigation to completed goals page."""
    todoist_page.goto(f"{test_server}/todoist/completed_goals")
    todoist_page.wait_for_load_state("networkidle")
    expect(todoist_page).to_have_title("Todoist")
    expect(todoist_page.locator("a", has_text="Completed")).to_be_visible()


def test_new_goal_button_present(todoist_page):
    """Test that New Goal button is available in actions dropdown."""
    # Open actions dropdown
    todoist_page.click("button:has-text('Actions')")
    # Look for the button that triggers the New Goal modal
    expect(todoist_page.locator("button", has_text="New Goal")).to_be_visible()
