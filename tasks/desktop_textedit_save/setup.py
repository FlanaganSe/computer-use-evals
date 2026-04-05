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

    # Force TextEdit to default to plain text instead of RTF.
    # Without this the agent must discover Cmd+Shift+T or Format → Make Plain Text,
    # which is not what this eval is testing.
    subprocess.run(
        ["defaults", "write", "com.apple.TextEdit", "RichText", "-int", "0"],
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

    # Restore TextEdit RTF default so we don't leave the user's preference changed
    subprocess.run(
        ["defaults", "write", "com.apple.TextEdit", "RichText", "-int", "1"],
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
