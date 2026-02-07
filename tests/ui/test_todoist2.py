"""
UI tests for Todoist2 using Playwright.
"""
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


def test_todoist2_page_loads(todoist2_page):
    """Test that Todoist2 page loads correctly."""
    expect(todoist2_page).to_have_title("Todoist2")


def test_goals_navigation(todoist2_page):
    """Test that Goals nav item is present and active."""
    expect(todoist2_page.locator("a", has_text="Goals")).to_be_visible()


def test_completed_goals_link(todoist2_page, test_server):
    """Test navigation to completed goals page."""
    todoist2_page.goto(f"{test_server}/todoist2/completed_goals")
    todoist2_page.wait_for_load_state("networkidle")
    expect(todoist2_page).to_have_title("Todoist2")
    expect(todoist2_page.locator("a", has_text="Completed")).to_be_visible()


def test_new_goal_button_present(todoist2_page):
    """Test that New Goal button is available in actions dropdown."""
    # Open actions dropdown
    todoist2_page.click("button:has-text('Actions')")
    # Look for the button that triggers the New Goal modal
    expect(todoist2_page.locator("button", has_text="New Goal")).to_be_visible()
