"""Verify that the Calendar test event was created."""

import sys
from pathlib import Path

# Import the setup module's check function
sys.path.insert(0, str(Path(__file__).parent))
from setup import check_event_exists

if check_event_exists():
    print("Calendar event 'Harness Test Event' found")
    sys.exit(0)
else:
    print("Calendar event 'Harness Test Event' not found")
    sys.exit(1)
