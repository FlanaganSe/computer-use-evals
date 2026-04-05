"""Setup and cleanup for the create-calendar-event task."""

from __future__ import annotations

import subprocess
import time

_EVENT_TITLE = "new event!"

_DELETE_SCRIPT = f"""
tell application "Calendar"
    repeat with cal in calendars
        set evts to (every event of cal whose summary is "{_EVENT_TITLE}")
        repeat with e in evts
            delete e
        end repeat
    end repeat
    save
end tell
"""


def _run_osascript(script: str, timeout: int = 10) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def setup() -> None:
    """Quit Calendar and remove any stale test events."""
    subprocess.run(
        ["osascript", "-e", 'tell application "Calendar" to quit'],
        capture_output=True,
        timeout=5,
    )
    time.sleep(1)

    # Briefly relaunch to delete stale events
    subprocess.run(
        ["osascript", "-e", 'tell application "Calendar" to activate'],
        capture_output=True,
        timeout=5,
    )
    time.sleep(2)

    try:
        _run_osascript(_DELETE_SCRIPT)
    except Exception:
        pass

    subprocess.run(
        ["osascript", "-e", 'tell application "Calendar" to quit'],
        capture_output=True,
        timeout=5,
    )
    time.sleep(1)


def cleanup() -> None:
    """Remove test events and quit Calendar."""
    try:
        _run_osascript(_DELETE_SCRIPT)
    except Exception:
        pass
    subprocess.run(
        ["osascript", "-e", 'tell application "Calendar" to quit'],
        capture_output=True,
        timeout=5,
    )
