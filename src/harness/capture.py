"""Screen capture tool for evidence collection.

Captures periodic screenshots (and optionally ARIA state) to build an
evidence directory that the author tool can process into a draft task YAML.
"""

from __future__ import annotations

import json
import logging
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Input event recording via CGEventTap (macOS)
# ---------------------------------------------------------------------------

_SPECIAL_KEYS: dict[int, str] = {
    36: "Return",
    48: "Tab",
    49: "Space",
    51: "Delete",
    53: "Escape",
    76: "Enter",
    115: "Home",
    116: "PageUp",
    117: "ForwardDelete",
    119: "End",
    121: "PageDown",
    123: "Left",
    124: "Right",
    125: "Down",
    126: "Up",
}


def _extract_modifiers(flags: int) -> list[str]:
    """Extract human-readable modifier names from CGEvent flags."""
    try:
        import Quartz  # type: ignore[import-untyped]
    except ImportError:
        return []
    modifier_map: dict[int, str] = {
        Quartz.kCGEventFlagMaskShift: "shift",
        Quartz.kCGEventFlagMaskControl: "control",
        Quartz.kCGEventFlagMaskAlternate: "option",
        Quartz.kCGEventFlagMaskCommand: "command",
    }
    return [name for mask, name in modifier_map.items() if flags & mask]


class EventTap:
    """Passive macOS input event recorder using CGEventTap."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._loop_ref: Any = None
        self._tap: Any = None
        self._capture_start: float = 0.0
        self._capture_start_epoch: float = 0.0

    def start(self) -> bool:
        """Start recording events. Returns False if tap creation fails."""
        try:
            import Quartz
        except ImportError:
            logger.warning("Quartz not available — skipping event capture.")
            return False

        self._capture_start = time.monotonic()
        self._capture_start_epoch = time.time()

        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
        )

        events_list = self._events
        capture_start = self._capture_start

        def _callback(proxy: Any, event_type: int, event: Any, refcon: Any) -> Any:
            if event_type == Quartz.kCGEventTapDisabledByTimeout:
                Quartz.CGEventTapEnable(tap, True)
                return event

            t = round(time.monotonic() - capture_start, 3)

            try:
                if event_type in (
                    Quartz.kCGEventLeftMouseDown,
                    Quartz.kCGEventRightMouseDown,
                ):
                    loc = Quartz.CGEventGetLocation(event)
                    click_count = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGMouseEventClickState
                    )
                    events_list.append(
                        {
                            "t": t,
                            "type": "mouse",
                            "button": "left"
                            if event_type == Quartz.kCGEventLeftMouseDown
                            else "right",
                            "x": int(loc.x),
                            "y": int(loc.y),
                            "click_count": int(click_count),
                        }
                    )

                elif event_type == Quartz.kCGEventKeyDown:
                    keycode = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                    actual_len, chars = Quartz.CGEventKeyboardGetUnicodeString(
                        event, 4, None, None
                    )
                    flags = Quartz.CGEventGetFlags(event)
                    modifiers = _extract_modifiers(flags)
                    hotkey_mods = set(modifiers) & {"command", "control", "option"}
                    char = (
                        chars
                        if actual_len > 0 and chars.isprintable() and not hotkey_mods
                        else None
                    )
                    key_name = _SPECIAL_KEYS.get(keycode) if char is None else None
                    entry: dict[str, Any] = {
                        "t": t,
                        "type": "key",
                        "char": char,
                        "keycode": int(keycode),
                        "modifiers": modifiers,
                    }
                    if key_name:
                        entry["key_name"] = key_name
                    events_list.append(entry)

                elif event_type == Quartz.kCGEventScrollWheel:
                    delta = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGScrollWheelEventDeltaAxis1
                    )
                    if int(delta) != 0:
                        loc = Quartz.CGEventGetLocation(event)
                        events_list.append(
                            {
                                "t": t,
                                "type": "scroll",
                                "delta_y": int(delta),
                                "x": int(loc.x),
                                "y": int(loc.y),
                            }
                        )
            except Exception:
                logger.debug("Error in event callback", exc_info=True)

            return event

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            _callback,
            None,
        )
        if tap is None:
            logger.warning(
                "CGEventTap creation failed — Accessibility permission "
                "likely not granted. Skipping event capture."
            )
            return False

        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)

        def _run(tap: Any, source: Any) -> None:
            self._loop_ref = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(self._loop_ref, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            Quartz.CFRunLoopRun()

        self._thread = threading.Thread(target=_run, args=(tap, source), daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop recording and join the thread."""
        if self._loop_ref is not None:
            try:
                import Quartz

                Quartz.CFRunLoopStop(self._loop_ref)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        # Disable tap to prevent further callback invocations
        if self._tap is not None:
            try:
                import Quartz

                Quartz.CGEventTapEnable(self._tap, False)
            except Exception:
                pass

    def write(self, output_dir: Path) -> bool:
        """Write events.json. Returns True if events were written."""
        if not self._events:
            return False
        data = {
            "capture_start_epoch": self._capture_start_epoch,
            "events": self._events,
        }
        (output_dir / "events.json").write_text(json.dumps(data, indent=2))
        return True


def capture_session(
    output_dir: Path,
    interval_seconds: float = 2.0,
    capture_aria: bool = False,
    capture_events: bool = True,
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

    # Start event tap if requested
    event_tap: EventTap | None = None
    if capture_events:
        event_tap = EventTap()
        if not event_tap.start():
            event_tap = None

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

    # Stop event tap and write events
    has_events = False
    if event_tap is not None:
        event_tap.stop()
        has_events = event_tap.write(output_dir)

    manifest = build_manifest(
        task_name=task_name,
        frames=frames,
        interval_seconds=interval_seconds,
        capture_aria=capture_aria,
        has_events=has_events,
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
    has_events: bool = False,
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
        "has_events": has_events,
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
