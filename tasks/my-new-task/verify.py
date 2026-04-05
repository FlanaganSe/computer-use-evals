"""Verify that a calendar event with the expected title exists."""

import subprocess
import sys

_EVENT_TITLE = "new event!"

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

result = subprocess.run(
    ["osascript", "-e", _CHECK_SCRIPT],
    capture_output=True,
    text=True,
    timeout=10,
)

if result.stdout.strip() == "found":
    print(f"Calendar event '{_EVENT_TITLE}' found")
    sys.exit(0)
else:
    print(f"Calendar event '{_EVENT_TITLE}' not found")
    sys.exit(1)
