"""Screen capture tool for evidence collection.

Captures periodic screenshots (and optionally ARIA state) to build an
evidence directory that the author tool can process into a draft task YAML.
"""

from __future__ import annotations

import json
import logging
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

logger = logging.getLogger(__name__)


def capture_session(
    output_dir: Path,
    interval_seconds: float = 2.0,
    capture_aria: bool = False,
    task_name: str = "untitled",
) -> Path:
    """Capture screenshots (and optionally ARIA state) at intervals.

    Runs until interrupted (Ctrl+C / SIGINT).
    Returns path to the evidence directory.
    """
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    aria_dir: Path | None = None
    if capture_aria:
        aria_dir = output_dir / "aria"
        aria_dir.mkdir(parents=True, exist_ok=True)

    frames: list[dict[str, int]] = []
    sequence = 0
    running = True

    def _stop(sig: int, frame: FrameType | None) -> None:
        nonlocal running
        running = False

    prev_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _stop)

    try:
        while running:
            sequence += 1
            timestamp = int(time.time())

            screenshot_path = screenshots_dir / f"{sequence:04d}_{timestamp}.png"
            try:
                subprocess.run(
                    ["screencapture", "-x", str(screenshot_path)],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
                logger.warning("screencapture failed for frame %d", sequence)
                sequence -= 1
                break

            if capture_aria and aria_dir is not None:
                aria_text = _capture_focused_app_aria()
                if aria_text:
                    aria_path = aria_dir / f"{sequence:04d}.yaml"
                    aria_path.write_text(aria_text)

            frames.append({"sequence": sequence, "timestamp": timestamp})
            logger.info("Captured frame %d", sequence)

            if running:
                time.sleep(interval_seconds)
    finally:
        signal.signal(signal.SIGINT, prev_handler)

    manifest = build_manifest(
        task_name=task_name,
        frames=frames,
        interval_seconds=interval_seconds,
        capture_aria=capture_aria,
        transcript_exists=(output_dir / "transcript.txt").exists(),
        notes_exists=(output_dir / "notes.md").exists(),
    )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return output_dir


def build_manifest(
    *,
    task_name: str,
    frames: list[dict[str, int]],
    interval_seconds: float,
    capture_aria: bool,
    transcript_exists: bool = False,
    notes_exists: bool = False,
) -> dict[str, object]:
    """Build manifest dict from capture session data."""
    duration = (frames[-1]["timestamp"] - frames[0]["timestamp"]) if len(frames) >= 2 else 0
    return {
        "task_name": task_name,
        "captured_at": datetime.now(tz=UTC).isoformat(),
        "capture_interval_ms": int(interval_seconds * 1000),
        "total_frames": len(frames),
        "duration_seconds": duration,
        "platform": "darwin",
        "has_aria": capture_aria,
        "has_transcript": transcript_exists,
        "has_notes": notes_exists,
        "frames": frames,
    }


def _capture_focused_app_aria() -> str | None:
    """Get ARIA/AX tree of the currently focused application."""
    try:
        from harness.environments.macos import _get_ax_tree, _get_window_info

        info = _get_window_info()
        pid = info.get("focused_pid")
        if pid is not None:
            return _get_ax_tree(pid)
    except Exception:
        logger.debug("Failed to capture ARIA state", exc_info=True)
    return None
