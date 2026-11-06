"""
Playwright UI test configuration and fixtures.
"""
import pytest
from pathlib import Path
from tests.server_fixture import test_server as run_test_server


@pytest.fixture(scope="session")
def test_server_instance():
    """Start an isolated Flask and Redis pair for browser tests."""
    project_root = Path(__file__).parent.parent.parent
    with run_test_server(project_root) as server:
        yield server


@pytest.fixture(scope="session")
def test_server(test_server_instance):
    return test_server_instance.base_url


@pytest.fixture
def page(page, test_server):
    """Provide a Playwright page object with the base URL set."""
    page.goto(test_server)
    page.wait_for_load_state("networkidle")
    yield page


@pytest.fixture
def logged_in_page(page, test_server):
    """Provide a Playwright page object logged in as admin (debug mode)."""
    # Visiting the homepage triggers the app's debug-mode admin auto-login.
    page.goto(test_server)
    page.wait_for_load_state("networkidle")
    assert page.locator("a:has-text('Logout')").is_visible()

    yield page


# Subpage fixtures for easy navigation
@pytest.fixture
def todoist_page(logged_in_page, test_server):
    """Navigate to Todoist page."""
    logged_in_page.goto(f"{test_server}/todoist")
    logged_in_page.wait_for_load_state("networkidle")
    yield logged_in_page


@pytest.fixture
def metrics_page(logged_in_page, test_server):
    """Navigate to Metrics page."""
    logged_in_page.goto(f"{test_server}/metrics")
    logged_in_page.wait_for_load_state("networkidle")
    yield logged_in_page


@pytest.fixture
def tubio_page(logged_in_page, test_server):
    """Navigate to Tubio page."""
    logged_in_page.goto(f"{test_server}/tubio")
    logged_in_page.wait_for_load_state("networkidle")
    yield logged_in_page


@pytest.fixture
def file_store_page(logged_in_page, test_server):
    """Navigate to File Store page."""
    logged_in_page.goto(f"{test_server}/file_store")
    logged_in_page.wait_for_load_state("networkidle")
    yield logged_in_page


@pytest.fixture
def crosswords_page(logged_in_page, test_server):
    """Navigate to Crosswords page (admin only)."""
    logged_in_page.goto(f"{test_server}/crosswords")
    logged_in_page.wait_for_load_state("networkidle")
    yield logged_in_page
