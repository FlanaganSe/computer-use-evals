"""Setup and cleanup for the desktop-calendar-event task.

Quits Calendar, removes any prior test events via AppleScript.
"""

from __future__ import annotations

import subprocess
import time

_EVENT_TITLE = "Harness Test Event"
_CALENDAR_NAME = "Home"

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

_CHECK_SCRIPT = f"""
tell application "Calendar"
    set found to false
    repeat with cal in calendars
        set evts to (every event of cal whose summary is "{_EVENT_TITLE}")
        if (count of evts) > 0 then
            set found to true
        end if
    end repeat
    if found then
        return "found"
    else
        return "not_found"
    end if
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
    """Prepare for the Calendar task: quit app, remove stale test events."""
    # Quit Calendar gracefully
    subprocess.run(
        ["osascript", "-e", 'tell application "Calendar" to quit'],
        capture_output=True,
        timeout=5,
    )
    time.sleep(1)

    # Relaunch briefly to delete stale events, then quit
    subprocess.run(
        ["osascript", "-e", 'tell application "Calendar" to activate'],
        capture_output=True,
        timeout=5,
    )
    time.sleep(2)

    try:
        _run_osascript(_DELETE_SCRIPT)
    except Exception:
        pass  # No stale events — fine

    subprocess.run(
        ["osascript", "-e", 'tell application "Calendar" to quit'],
        capture_output=True,
        timeout=5,
    )
    time.sleep(1)


def check_event_exists() -> bool:
    """Check if the test event exists in Calendar. Used by verify.py."""
    try:
        result = _run_osascript(_CHECK_SCRIPT)
        return result == "found"
    except Exception:
        return False


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
