"""
UI tests for File Store using Playwright.
"""
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


def test_file_store_page_loads(file_store_page):
    """Test that File Store page loads correctly."""
    expect(file_store_page).to_have_title("File Store")


def test_upload_action_present(file_store_page):
    """Test that Upload File action is available."""
    file_store_page.click("button:has-text('Actions')")
    expect(file_store_page.locator(".dropdown-item", has_text="Upload File").first).to_be_visible()


def test_files_card_present(file_store_page):
    """Test that the files card is present."""
    expect(file_store_page.locator("h5", has_text="Your Files")).to_be_visible()


def test_file_list_or_empty_state(file_store_page):
    """Test that either file list or empty state is shown."""
    # Check if we have a list group (files exist) or empty state message
    has_list = file_store_page.locator("ul.list-group, div.list-group").count() > 0
    has_empty = file_store_page.locator("text=No files").count() > 0
    
    assert has_list or has_empty, "Should show either file list or empty state"
