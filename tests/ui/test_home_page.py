"""
UI tests for the home page using Playwright.
"""
import time
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


def test_home_page_loads(logged_in_page, test_server):
    """Test that the home page loads successfully."""
    expect(logged_in_page).to_have_title("NabiCat")
    welcome_heading = logged_in_page.locator("h1", has_text="Welcome to")
    expect(welcome_heading).to_be_visible()


def test_app_grid_visible(logged_in_page, test_server):
    """Test that the app grid is displayed with all expected apps for admin."""
    expected_apps = ["Todoist2", "Metrics", "Tubio", "JSwipe", "File Store"]
    
    for app_name in expected_apps:
        app_card = logged_in_page.locator("text=" + app_name)
        expect(app_card).to_be_visible()


def test_all_app_cards_clickable(logged_in_page, test_server):
    """Test that all app cards are clickable links for admin."""
    apps = [
        ("Todoist2", "/todoist2"),
        ("Metrics", "/metrics"),
        ("Tubio", "/tubio"),
        ("JSwipe", "/jswipe"),
        ("File Store", "/file_store"),
    ]
    
    for app_name, path in apps:
        # Check that each app is wrapped in a link
        link = logged_in_page.locator(f"a:has-text('{app_name}')")
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", path)


def test_crosswords_visible_for_admin(logged_in_page, test_server):
    """Test that Crosswords is visible for admin user."""
    expect(logged_in_page.locator("text=Crosswords")).to_be_visible()


def test_version_badge_displayed(logged_in_page):
    """Test that the version/build badge is displayed."""
    expect(logged_in_page.locator("code")).to_be_visible()


def test_navbar_present(logged_in_page):
    """Test that the navigation bar is present with key elements."""
    expect(logged_in_page.locator("nav")).to_be_visible()
    expect(logged_in_page.locator("a[href='/']", has_text="NabiCat")).to_be_visible()
    expect(logged_in_page.locator("button:has-text('Actions')")).to_be_visible()


def test_logout_button_present(logged_in_page):
    """Test that logout button is present (or login if not logged in)."""
    expect(logged_in_page.locator("a:has-text('Logout')")).to_be_visible()


def test_admin_only_apps_hidden_for_non_admin(page, test_server):
    """Test that admin-only apps are hidden for non-admin users."""
    # Register and log in as a fresh non-admin user (self-contained test)
    username = f"ui_non_admin_{int(time.time() * 1000)}"
    password = "testpass123"

    page.goto(f"{test_server}/account/login")
    page.wait_for_load_state("networkidle")
    page.fill("input#username", username)
    page.fill("input#password", password)
    page.click("button:has-text('Create Account')")
    page.wait_for_url(f"{test_server}/", timeout=10000)
    page.wait_for_load_state("networkidle")
    
    # Verify non-admin apps are visible
    expect(page.locator("text=Metrics")).to_be_visible()
    expect(page.locator("text=Tubio")).to_be_visible()
    expect(page.locator("text=File Store")).to_be_visible()
    
    # Verify admin-only apps are NOT visible
    expect(page.locator("text=Todoist2")).not_to_be_visible()
    expect(page.locator("text=JSwipe")).not_to_be_visible()
    expect(page.locator("text=Crosswords")).not_to_be_visible()
