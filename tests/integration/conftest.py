"""Pytest configuration for integration tests - handles server startup/shutdown"""

import pytest
import subprocess
import sys
import time
import socket
import signal
import os
from pathlib import Path

# Port for the test server
TEST_PORT = 54321
TEST_BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


def wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    """Wait for server to become available"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            time.sleep(0.1)
    return False


def find_free_port() -> int:
    """Find a free port on localhost"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@pytest.fixture(scope="session")
def server_url():
    """Start the Flask server for integration tests and return the base URL"""
    # Use a dynamic port to avoid conflicts
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    
    # Get the project root directory
    project_root = Path(__file__).parent.parent.parent
    
    # Start the server as a subprocess
    # Use -m web_app to run the Flask app
    env = os.environ.copy()
    env['X_RAPID_API_KEY'] = 'test_key_for_integration_tests'
    
    process = subprocess.Popen(
        [sys.executable, "-m", "web_app", "--port", str(port), "--debug"],
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    
    # Wait for server to be ready
    if not wait_for_server("127.0.0.1", port, timeout=30.0):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        raise RuntimeError(f"Server failed to start on port {port}")
    
    yield base_url
    
    # Cleanup: terminate the server
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
