"""The uv-installed console scripts expose the expected Click commands."""
import subprocess
from pathlib import Path
import shutil

import pytest


@pytest.mark.parametrize(("command", "expected_option"), [
    ("server", "--debug"),
    ("api-helper", "login"),
])
def test_uv_console_scripts(command, expected_option):
    assert shutil.which("uv"), "uv must be installed to run the project commands"
    result = subprocess.run(
        ["uv", "run", "--locked", command, "--help"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected_option in result.stdout
