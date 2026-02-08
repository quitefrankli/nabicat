"""
Playwright UI test configuration and fixtures.
"""
import pytest
import subprocess
import time
import os
import socket
from pathlib import Path


def _find_free_port():
    """Find a free port for the test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def _wait_for_server(url, timeout=30):
    """Poll the server until it's ready or timeout."""
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


@pytest.fixture(scope="session")
def test_server():
    """Start the Flask application in debug mode on a free port."""
    port = _find_free_port()
    base_url = f"http://localhost:{port}"
    
    env = os.environ.copy()
    env["FLASK_DEBUG"] = "1"
    
    process = subprocess.Popen(
        ["python", "-m", "web_app", "--debug", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=Path(__file__).parent.parent.parent,
        env=env,
    )
    
    if not _wait_for_server(base_url, timeout=30):
        process.terminate()
        process.wait()
        raise RuntimeError(f"Server failed to start on port {port}")
    
    yield base_url
    
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@pytest.fixture
def page(page, test_server):
    """Provide a Playwright page object with the base URL set."""
    page.goto(test_server)
    page.wait_for_load_state("networkidle")
    yield page


@pytest.fixture
def logged_in_page(page, test_server):
    """Provide a Playwright page object logged in as admin (debug mode)."""
    # Navigate to login page
    page.goto(f"{test_server}/account/login")
    page.wait_for_load_state("networkidle")
    
    # Log in as admin
    page.fill("input#username", "admin")
    page.fill("input#password", "admin")
    page.click("button:has-text('Sign In')")
    
    # Wait for redirect to home page
    page.wait_for_url(f"{test_server}/", timeout=10000)
    page.wait_for_load_state("networkidle")
    
    yield page


# Subpage fixtures for easy navigation
@pytest.fixture
def todoist2_page(logged_in_page, test_server):
    """Navigate to Todoist2 page."""
    logged_in_page.goto(f"{test_server}/todoist2")
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


@pytest.fixture
def jswipe_page(logged_in_page, test_server):
    """Navigate to JSwipe page."""
    logged_in_page.goto(f"{test_server}/jswipe")
    logged_in_page.wait_for_load_state("networkidle")
    yield logged_in_page
