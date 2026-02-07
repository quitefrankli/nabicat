"""
UI tests for JSwipe using Playwright.
"""
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


def test_jswipe_page_loads(jswipe_page):
    """Test that JSwipe page loads correctly."""
    expect(jswipe_page.locator("h1.text-forest", has_text="JSwipe")).to_be_visible()
    expect(jswipe_page.locator("h5", has_text="Search Settings")).to_be_visible()


def test_search_form_elements(jswipe_page):
    """Test that the search form has all required elements."""
    job_type_input = jswipe_page.locator("input#job-type")
    expect(job_type_input).to_be_visible()
    expect(job_type_input).to_have_attribute("placeholder", "e.g., Software Engineer")
    
    location_select = jswipe_page.locator("select#location")
    expect(location_select).to_be_visible()
    
    search_button = jswipe_page.locator("button:has-text('Start Searching')")
    expect(search_button).to_be_visible()


def test_location_options(jswipe_page):
    """Test that location dropdown has Australian cities."""
    # Check that the select element has the expected options
    for city in ["Sydney", "Melbourne", "Brisbane", "Perth"]:
        option = jswipe_page.locator(f"select#location option[value='{city}']")
        expect(option).to_have_count(1)


def test_job_search_flow(jswipe_page):
    """Test the complete job search flow."""
    jswipe_page.fill("input#job-type", "Software Engineer")
    jswipe_page.select_option("select#location", "Sydney")
    jswipe_page.click("button:has-text('Start Searching')")
    
    # Wait for results to load (in debug mode, hardcoded jobs appear)
    jswipe_page.wait_for_selector(".job-card", timeout=10000)
    
    job_cards = jswipe_page.locator(".job-card")
    expect(job_cards.first).to_be_visible()


def test_card_swipe_buttons(jswipe_page):
    """Test that swipe action buttons are present."""
    jswipe_page.fill("input#job-type", "Developer")
    jswipe_page.select_option("select#location", "Melbourne")
    jswipe_page.click("button:has-text('Start Searching')")
    
    jswipe_page.wait_for_selector(".job-card", timeout=10000)
    
    # Check all three action buttons
    expect(jswipe_page.locator("#reject-btn")).to_be_visible()
    expect(jswipe_page.locator("#apply-job-btn")).to_be_visible()
    expect(jswipe_page.locator("#save-btn")).to_be_visible()


def test_job_card_content(jswipe_page):
    """Test that job cards display the correct information."""
    jswipe_page.fill("input#job-type", "Manager")
    jswipe_page.select_option("select#location", "Perth")
    jswipe_page.click("button:has-text('Start Searching')")
    
    jswipe_page.wait_for_selector(".job-card", timeout=10000)
    
    first_card = jswipe_page.locator(".job-card").first
    
    expect(first_card.locator(".job-title")).to_be_visible()
    expect(first_card.locator(".job-company")).to_be_visible()
    expect(first_card.locator(".job-location")).to_be_visible()
    expect(first_card.locator("a:has-text('View Job')")).to_be_visible()


def test_debug_mode_notification(jswipe_page):
    """Test that debug mode notification appears."""
    jswipe_page.fill("input#job-type", "Developer")
    jswipe_page.select_option("select#location", "Sydney")
    jswipe_page.click("button:has-text('Start Searching')")
    
    # Wait for flash message to appear (it's dismissed after a few seconds)
    jswipe_page.wait_for_selector("text=Debug mode: Using test jobs", timeout=5000)
    expect(jswipe_page.locator("text=Debug mode: Using test jobs")).to_be_visible()


def test_empty_state_shown_initially(jswipe_page):
    """Test that empty state is shown before search."""
    expect(jswipe_page.locator("text=No jobs found yet")).to_be_visible()
