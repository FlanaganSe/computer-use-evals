"""Screen capture tool for evidence collection.

Captures periodic screenshots (and optionally ARIA state) to build an
evidence directory that the author tool can process into a draft task YAML.
"""

from __future__ import annotations

import json
import logging
import queue
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


# Minimum gap between event-triggered aligned captures (seconds)
_ALIGNED_DEBOUNCE = 0.3


def _get_app_context() -> dict[str, str] | None:
    """Get current focused app context. Best-effort, returns None on failure."""
    try:
        from harness.environments.macos import _get_window_info

        info = _get_window_info()
        if not info:
            return None
        result: dict[str, str] = {}
        if info.get("focused_app"):
            result["app"] = str(info["focused_app"])
        if info.get("focused_window_title"):
            result["window"] = str(info["focused_window_title"])
        return result if result else None
    except Exception:
        logger.debug("Failed to get app context", exc_info=True)
        return None


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

    def __init__(self, aligned: bool = False) -> None:
        self._events: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._loop_ref: Any = None
        self._tap: Any = None
        self._capture_start: float = 0.0
        self._capture_start_epoch: float = 0.0
        self._aligned = aligned
        self._trigger_queue: queue.Queue[dict[str, Any]] = queue.Queue()

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
        aligned = self._aligned
        trigger_queue = self._trigger_queue

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
                    entry_mouse = {
                        "t": t,
                        "type": "mouse",
                        "button": "left"
                        if event_type == Quartz.kCGEventLeftMouseDown
                        else "right",
                        "x": int(loc.x),
                        "y": int(loc.y),
                        "click_count": int(click_count),
                    }
                    events_list.append(entry_mouse)
                    if aligned:
                        trigger_queue.put_nowait(entry_mouse)

                elif event_type == Quartz.kCGEventKeyDown:
                    keycode = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                    actual_len, chars = Quartz.CGEventKeyboardGetUnicodeString(
                        event, 4, None, None
                    )
                    flags = Quartz.CGEventGetFlags(event)
                    modifiers = _extract_modifiers(flags)
                    printable = actual_len > 0 and chars.isprintable()
                    char = chars if printable else None
                    key_name = _SPECIAL_KEYS.get(keycode) if not printable else None
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

    def drain_triggers(self) -> dict[str, Any] | None:
        """Drain all pending trigger events, return the most recent (or None)."""
        latest: dict[str, Any] | None = None
        while True:
            try:
                latest = self._trigger_queue.get_nowait()
            except queue.Empty:
                break
        return latest

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


def _take_screenshot(path: Path) -> bool:
    """Capture a screenshot to *path*. Returns True on success."""
    try:
        subprocess.run(
            ["screencapture", "-x", str(path)],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _make_timeline_entry(
    *,
    t: float,
    epoch: float,
    trigger: str,
    screenshot: str,
    ax_snapshot: str | None = None,
    event: dict[str, Any] | None = None,
    app_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a single aligned timeline entry."""
    entry: dict[str, Any] = {
        "t": round(t, 3),
        "epoch": round(epoch, 3),
        "trigger": trigger,
        "screenshot": screenshot,
    }
    if ax_snapshot is not None:
        entry["ax_snapshot"] = ax_snapshot
    if event is not None:
        entry["event"] = event
    if app_context is not None:
        entry["app_context"] = app_context
    return entry


def capture_session(
    output_dir: Path,
    interval_seconds: float = 2.0,
    capture_aria: bool = False,
    capture_events: bool = True,
    task_name: str = "untitled",
    aligned: bool = False,
) -> Path:
    """Capture screenshots (and optionally ARIA state) at intervals.

    When *aligned* is True, additional screenshots are captured on
    high-signal input events (clicks) and app-focus changes, producing
    an ``aligned_timeline`` in the manifest that correlates events,
    screenshots, and optional AX snapshots on a shared time base.

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
        event_tap = EventTap(aligned=aligned)
        if not event_tap.start():
            event_tap = None

    capture_start_mono = time.monotonic()
    frames: list[dict[str, int]] = []
    timeline: list[dict[str, Any]] = []
    sequence = 0
    running = True
    last_app_context: dict[str, str] | None = None
    last_aligned_capture: float = 0.0

    def _stop(sig: int, frame: FrameType | None) -> None:
        nonlocal running
        running = False

    def _do_capture(
        trigger: str,
        event: dict[str, Any] | None = None,
    ) -> bool:
        """Capture a screenshot (+ optional AX) and record it.

        Returns True if the screenshot was taken successfully.
        """
        nonlocal sequence, last_app_context, last_aligned_capture

        now_mono = time.monotonic()
        now_epoch = time.time()
        t = round(now_mono - capture_start_mono, 3)
        sequence += 1
        timestamp = int(now_epoch)

        screenshot_name = f"{sequence:04d}_{timestamp}.png"
        screenshot_path = screenshots_dir / screenshot_name
        if not _take_screenshot(screenshot_path):
            logger.warning("screencapture failed for frame %d", sequence)
            sequence -= 1
            return False

        ax_name: str | None = None
        if capture_aria and aria_dir is not None:
            aria_text = _capture_focused_app_aria()
            if aria_text:
                ax_name = f"{sequence:04d}.yaml"
                (aria_dir / ax_name).write_text(aria_text)

        frames.append({"sequence": sequence, "timestamp": timestamp})

        if aligned:
            last_aligned_capture = now_mono  # set before screenshot I/O
            app_ctx = _get_app_context()

            # Detect focus changes on any trigger type
            if (
                app_ctx is not None
                and last_app_context is not None
                and app_ctx.get("app") != last_app_context.get("app")
            ):
                trigger = "focus_change"
                focus_event: dict[str, Any] = {
                    "type": "focus",
                    "from_app": last_app_context.get("app", ""),
                    "to_app": app_ctx.get("app", ""),
                }
                event = focus_event

            last_app_context = app_ctx

            timeline.append(
                _make_timeline_entry(
                    t=t,
                    epoch=now_epoch,
                    trigger=trigger,
                    screenshot=screenshot_name,
                    ax_snapshot=ax_name,
                    event=event,
                    app_context=app_ctx,
                )
            )

        logger.info("Captured frame %d (%s)", sequence, trigger if aligned else "interval")
        return True

    prev_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _stop)

    try:
        if aligned and event_tap is not None:
            # Aligned mode: poll-based loop with event-triggered captures
            _do_capture("interval")
            next_periodic = time.monotonic() + interval_seconds

            while running:
                now = time.monotonic()

                # Drain all pending trigger events; keep only the most recent
                trigger_event = event_tap.drain_triggers()
                if trigger_event is not None and now - last_aligned_capture >= _ALIGNED_DEBOUNCE:
                    _do_capture("click", event=trigger_event)

                # Check for periodic capture
                if now >= next_periodic:
                    _do_capture("interval")
                    next_periodic = now + interval_seconds

                if running:
                    time.sleep(0.05)  # 50ms poll interval
        else:
            if aligned and event_tap is None:
                logger.warning(
                    "Aligned capture requested but EventTap unavailable — "
                    "falling back to interval-only capture with timeline tracking"
                )
            # Standard / degraded-aligned interval-only mode
            while running:
                if not _do_capture("interval"):
                    break
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
        capture_mode="aligned" if aligned else "interval",
        aligned_timeline=timeline if aligned and timeline else None,
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
    capture_mode: str = "interval",
    aligned_timeline: list[dict[str, Any]] | None = None,
) -> dict[str, object]:
    """Build manifest dict from capture session data."""
    duration = (frames[-1]["timestamp"] - frames[0]["timestamp"]) if len(frames) >= 2 else 0
    result: dict[str, object] = {
        "task_name": task_name,
        "captured_at": datetime.now(tz=UTC).isoformat(),
        "capture_mode": capture_mode,
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
    if aligned_timeline is not None:
        result["aligned_timeline"] = aligned_timeline
    return result


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
