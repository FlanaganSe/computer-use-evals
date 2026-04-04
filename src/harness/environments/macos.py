"""macOS desktop environment for the eval harness.

Uses screencapture for screenshots, pyobjc AXUIElement API for accessibility
trees, and pyautogui for mouse/keyboard actions.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from harness.types import Action, ActionType, Observation, ObservationType, Task

logger = logging.getLogger(__name__)

# Post-action stabilization delay (seconds)
_ACTION_SETTLE_DELAY = 0.5

# Commands allowed for SHELL actions (safety allowlist)
_SHELL_ALLOWLIST = {"osascript", "open"}


class MacOSDesktopEnvironment:
    """Manages macOS desktop automation for task execution."""

    def __init__(self) -> None:
        self._run_dir: Path | None = None
        self._screenshots_dir: Path | None = None
        self._target_app: str | None = None

    # ------------------------------------------------------------------
    # Permission checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_accessibility_permission() -> bool:
        """Check if Accessibility permission is granted."""
        try:
            from ApplicationServices import (  # type: ignore[import-untyped]  # noqa: I001
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )
            from CoreFoundation import kCFBooleanFalse  # type: ignore[import-untyped]

            options = {kAXTrustedCheckOptionPrompt: kCFBooleanFalse}
            return bool(AXIsProcessTrustedWithOptions(options))
        except ImportError:
            logger.warning("pyobjc not available — skipping AX permission check")
            return False

    @staticmethod
    def _check_screen_recording_permission() -> bool:
        """Best-effort check for Screen Recording permission via screencapture."""
        try:
            result = subprocess.run(
                ["screencapture", "-x", "-c"],  # capture to clipboard, silent
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # ------------------------------------------------------------------
    # Environment protocol
    # ------------------------------------------------------------------

    async def setup(self, task: Task, run_dir: Path) -> None:
        self._run_dir = run_dir
        self._screenshots_dir = run_dir / "screenshots"
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)

        if not self._check_accessibility_permission():
            raise RuntimeError(
                "Accessibility permission not granted. "
                "Go to System Settings → Privacy & Security → Accessibility "
                "and add this terminal/Python process."
            )

        if not self._check_screen_recording_permission():
            raise RuntimeError(
                "Screen Recording permission not granted. "
                "Go to System Settings → Privacy & Security → Screen Recording "
                "and add this terminal/Python process."
            )

    async def collect_observation(self, observation_type: ObservationType) -> Observation:
        if observation_type == ObservationType.NONE:
            return Observation(observation_type=ObservationType.NONE)

        screenshot: bytes | None = None
        aria_snapshot: str | None = None
        a11y_available: bool | None = None
        focused_app: str | None = None
        page_title: str | None = None

        # Window metadata
        window_info = _get_window_info()
        focused_app = window_info.get("focused_app")
        page_title = window_info.get("focused_window_title")

        if observation_type in (ObservationType.SCREENSHOT, ObservationType.SCREENSHOT_AND_ARIA):
            screenshot = await _take_screenshot()

        if observation_type in (ObservationType.ARIA_STATE, ObservationType.SCREENSHOT_AND_ARIA):
            pid = window_info.get("focused_pid")
            if pid is not None:
                tree = _get_ax_tree(pid)
                if tree is not None:
                    aria_snapshot = tree
                    a11y_available = True
                else:
                    a11y_available = False
            else:
                a11y_available = False

        return Observation(
            observation_type=observation_type,
            screenshot=screenshot,
            aria_snapshot=aria_snapshot,
            page_title=page_title,
            focused_app=focused_app,
            a11y_available=a11y_available,
        )

    async def execute_action(self, action: Action) -> str:
        import pyautogui  # type: ignore[import-untyped]

        params = action.params

        match action.action_type:
            case ActionType.CLICK:
                if "x" in params and "y" in params:
                    pyautogui.click(int(params["x"]), int(params["y"]))
                    await asyncio.sleep(_ACTION_SETTLE_DELAY)
                    return "ok"
                return "error:click requires x,y coordinates for desktop"

            case ActionType.DOUBLE_CLICK:
                if "x" in params and "y" in params:
                    pyautogui.doubleClick(int(params["x"]), int(params["y"]))
                    await asyncio.sleep(_ACTION_SETTLE_DELAY)
                    return "ok"
                return "error:double_click requires x,y coordinates for desktop"

            case ActionType.TYPE:
                text = params.get("text")
                if text is None:
                    return "error:type requires 'text' param"
                pyautogui.write(text, interval=0.02)
                await asyncio.sleep(_ACTION_SETTLE_DELAY)
                return "ok"

            case ActionType.PRESS:
                key = params.get("key")
                if key is None:
                    return "error:press requires 'key' param"
                keys = [k.strip() for k in key.split("+")]
                pyautogui.hotkey(*keys)
                await asyncio.sleep(_ACTION_SETTLE_DELAY)
                return "ok"

            case ActionType.SCROLL:
                clicks = params.get("delta_y", 0)
                pyautogui.scroll(int(clicks))
                await asyncio.sleep(_ACTION_SETTLE_DELAY)
                return "ok"

            case ActionType.WAIT:
                ms = params.get("ms", 1000)
                await asyncio.sleep(int(ms) / 1000.0)
                return "ok"

            case ActionType.SHELL:
                command = params.get("command", "")
                if command not in _SHELL_ALLOWLIST:
                    return f"error:shell command {command!r} not in allowlist {_SHELL_ALLOWLIST}"
                args = params.get("args", [])
                result = subprocess.run(
                    [command, *args],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    stderr = result.stderr.strip()
                    return f"error:shell returned {result.returncode}: {stderr}"
                await asyncio.sleep(_ACTION_SETTLE_DELAY)
                return "ok"

            case ActionType.MOVE:
                if "x" in params and "y" in params:
                    pyautogui.moveTo(int(params["x"]), int(params["y"]))
                    return "ok"
                return "error:move requires x,y coordinates"

            case ActionType.DONE:
                return "done"

            case ActionType.FAIL:
                reason = params.get("reason", "Agent declared failure")
                return f"fail:{reason}"

            case _:
                return f"error:unsupported action type {action.action_type} for desktop"

    async def teardown(self) -> None:
        pass  # No persistent resources to clean up


# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------


async def _take_screenshot() -> bytes:
    """Capture the screen using macOS screencapture CLI."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = Path(f.name)

    try:
        proc = await asyncio.create_subprocess_exec(
            "screencapture",
            "-x",
            "-C",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"screencapture failed with exit code {proc.returncode}")
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError("screencapture produced empty or missing file")
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Accessibility tree
# ---------------------------------------------------------------------------


def _get_attr(element: Any, attr_name: str) -> Any:
    """Safely get an AX attribute from an element."""
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue

        err, value = AXUIElementCopyAttributeValue(element, attr_name, None)
        if err == 0:
            return value
    except Exception:
        pass
    return None


def _serialize_ax_element(element: Any, depth: int = 0, max_depth: int = 10) -> str:
    """Recursively serialize an AX element tree to indented text."""
    if depth > max_depth:
        return ""

    lines: list[str] = []
    role = _get_attr(element, "AXRole") or "unknown"
    title = _get_attr(element, "AXTitle") or ""
    value = _get_attr(element, "AXValue") or ""
    desc = _get_attr(element, "AXDescription") or ""

    label = f"{'  ' * depth}{role}"
    if title:
        label += f' "{title}"'
    if desc and desc != title:
        label += f" ({desc})"
    if value:
        val_str = str(value)
        if len(val_str) > 100:
            val_str = val_str[:100] + "..."
        label += f' value="{val_str}"'
    lines.append(label)

    children = _get_attr(element, "AXChildren") or []
    for child in children:
        child_text = _serialize_ax_element(child, depth + 1, max_depth)
        if child_text:
            lines.append(child_text)

    return "\n".join(lines)


def _get_ax_tree(pid: int) -> str | None:
    """Get accessibility tree for an app by PID. Returns None if unavailable."""
    try:
        from ApplicationServices import AXUIElementCreateApplication

        app_ref = AXUIElementCreateApplication(pid)
        tree = _serialize_ax_element(app_ref)
        return tree if tree.strip() else None
    except Exception:
        logger.debug("Failed to get AX tree for PID %d", pid, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Window metadata
# ---------------------------------------------------------------------------


def _get_window_info() -> dict[str, Any]:
    """Get focused app name, PID, and window title."""
    try:
        import Quartz  # type: ignore[import-untyped]

        workspace = Quartz.NSWorkspace.sharedWorkspace()
        front_app = workspace.frontmostApplication()
        app_name: str = front_app.localizedName()
        app_pid: int = front_app.processIdentifier()

        # Get focused window title from AX
        window_title: str | None = None
        try:
            from ApplicationServices import AXUIElementCreateApplication

            app_ref = AXUIElementCreateApplication(app_pid)
            focused_window = _get_attr(app_ref, "AXFocusedWindow")
            if focused_window is not None:
                window_title = _get_attr(focused_window, "AXTitle")
        except Exception:
            pass

        return {
            "focused_app": app_name,
            "focused_pid": app_pid,
            "focused_window_title": window_title,
        }
    except Exception:
        logger.debug("Failed to get window info", exc_info=True)
        return {}
