"""Setup and cleanup for the desktop-textedit-save task.

Creates the target directory and kills any running TextEdit instances.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

TARGET_DIR = Path("/tmp/harness_desktop_test")
TARGET_FILE = TARGET_DIR / "harness-test.txt"


def setup() -> dict[str, Any]:
    """Prepare for the TextEdit task: clean state and create target dir."""
    # Kill any running TextEdit instances
    subprocess.run(
        ["osascript", "-e", 'tell application "TextEdit" to quit'],
        capture_output=True,
        timeout=5,
    )

    # Clean prior test artifacts
    if TARGET_FILE.exists():
        TARGET_FILE.unlink()

    # Create target directory
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    return {"directory": str(TARGET_DIR)}


def cleanup() -> None:
    """Close TextEdit and remove test artifacts."""
    # Quit TextEdit gracefully
    subprocess.run(
        ["osascript", "-e", 'tell application "TextEdit" to quit'],
        capture_output=True,
        timeout=5,
    )

    # Clean up test files
    if TARGET_FILE.exists():
        TARGET_FILE.unlink()
    if TARGET_DIR.exists():
        try:
            TARGET_DIR.rmdir()
        except OSError:
            pass  # Directory not empty — leave it
