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

from harness.ax_state import AXNode, build_ax_tree, find_node_by_id
from harness.types import Action, ActionType, Observation, ObservationType, Task

logger = logging.getLogger(__name__)

# Post-action stabilization delay (seconds)
_ACTION_SETTLE_DELAY = 0.5

# Extra settle time after app switch is detected (let UI fully render)
_APP_SWITCH_SETTLE = 0.5

# Maximum time to wait for an app switch to take effect (seconds)
_APP_SWITCH_TIMEOUT = 5.0

# Poll interval when waiting for app switch (seconds)
_APP_SWITCH_POLL = 0.25

# Commands allowed for SHELL actions (safety allowlist)
_SHELL_ALLOWLIST = {"osascript", "open"}

# Unicode symbol → pyautogui key name mapping.
# Order matters: longer symbols must come first to avoid partial matches.
_UNICODE_KEY_MAP: list[tuple[str, str]] = [
    ("⌘", "command"),
    ("⇧", "shift"),
    ("⌥", "option"),
    ("⌃", "control"),
    ("⎋", "escape"),
    ("⇥", "tab"),
    ("⌫", "backspace"),
    ("⌦", "delete"),
    ("↩", "return"),
    ("↑", "up"),
    ("↓", "down"),
    ("←", "left"),
    ("→", "right"),
    (" ", "space"),
]

# Case-insensitive ASCII aliases → pyautogui key name
_KEY_ALIASES: dict[str, str] = {
    "cmd": "command",
    "ctrl": "control",
    "alt": "option",
    "opt": "option",
    "meta": "command",
    "win": "command",
    "enter": "return",
    "esc": "escape",
    "del": "delete",
    "bs": "backspace",
}


def _normalize_keys(raw: str) -> list[str]:
    """Normalize a key combo string into pyautogui-compatible key names.

    Handles all common formats:
      - Unicode:  ⌘S, ⌘⇧S, ⌫
      - ASCII:    CMD+S, CMD+SHIFT+S, command+s
      - Mixed:    ⌘+S
    """
    # Step 1: expand Unicode modifier/key symbols to "name+" so they
    # participate in the subsequent split.
    s = raw
    for symbol, name in _UNICODE_KEY_MAP:
        s = s.replace(symbol, name + "+")

    # Step 2: split on "+" and drop empty segments (from trailing "+")
    parts = [p.strip() for p in s.split("+") if p.strip()]

    # Step 3: normalize each part — resolve aliases, lowercase modifiers
    normalized: list[str] = []
    for part in parts:
        lower = part.lower()
        if lower in _KEY_ALIASES:
            normalized.append(_KEY_ALIASES[lower])
        elif lower in ("command", "shift", "option", "control", "fn"):
            normalized.append(lower)
        else:
            # Regular key — pyautogui expects lowercase single chars
            normalized.append(lower if len(part) == 1 else part.lower())

    return normalized if normalized else [raw]


class MacOSDesktopEnvironment:
    """Manages macOS desktop automation for task execution."""

    def __init__(self) -> None:
        self._run_dir: Path | None = None
        self._screenshots_dir: Path | None = None
        self._target_app: str | None = None
        self._last_ax_tree: AXNode | None = None
        self._last_ax_refs: dict[str, Any] = {}

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

        ax_tree_structured: AXNode | None = None
        if observation_type in (ObservationType.ARIA_STATE, ObservationType.SCREENSHOT_AND_ARIA):
            pid = window_info.get("focused_pid")
            if pid is not None:
                tree = _get_ax_tree(pid)
                if tree is not None:
                    aria_snapshot = tree
                    a11y_available = True
                else:
                    a11y_available = False
                # Build structured tree (for adapters that need it)
                ax_refs: dict[str, Any] = {}
                ax_tree_structured = _get_structured_ax_tree(pid, refs=ax_refs)
                self._last_ax_refs = ax_refs
            else:
                a11y_available = False

        obs = Observation(
            observation_type=observation_type,
            screenshot=screenshot,
            aria_snapshot=aria_snapshot,
            page_title=page_title,
            focused_app=focused_app,
            a11y_available=a11y_available,
        )
        # Attach structured tree as side-channel for adapters that use it.
        # This avoids changing the Observation model while keeping the data available.
        obs._ax_tree = ax_tree_structured  # type: ignore[attr-defined]
        self._last_ax_tree = ax_tree_structured
        return obs

    def resolve_semantic_target(self, action: Action) -> Action:
        """If action has a semantic_target but no x/y, resolve from AX tree.

        Returns the action unchanged if no resolution is needed or possible.
        """
        params = action.params
        target_id = params.get("semantic_target")
        if target_id is None or ("x" in params and "y" in params):
            return action

        if self._last_ax_tree is None:
            return action

        node = find_node_by_id(self._last_ax_tree, target_id)
        if node is not None and node.center is not None:
            new_params = {**params, "x": node.center[0], "y": node.center[1]}
            return Action(action_type=action.action_type, params=new_params)

        return action

    async def execute_action(self, action: Action) -> str:
        import pyautogui  # type: ignore[import-untyped]

        # Resolve semantic targets to coordinates if needed
        action = self.resolve_semantic_target(action)
        params = action.params

        match action.action_type:
            case ActionType.CLICK:
                if "x" in params and "y" in params:
                    pyautogui.click(int(params["x"]), int(params["y"]))
                    await asyncio.sleep(_ACTION_SETTLE_DELAY)
                    return "ok"
                if self._try_ax_press(params.get("semantic_target")):
                    await asyncio.sleep(_ACTION_SETTLE_DELAY)
                    return "ok"
                return "error:click requires x,y coordinates or a pressable AX element"

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
                keys = _normalize_keys(key)
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

                # Record pre-command frontmost app for app-switch detection
                pre_app: str | None = None
                is_app_switch = self._is_app_switch_command(command, args)
                if is_app_switch:
                    pre_info = _get_window_info()
                    pre_app = pre_info.get("focused_app")

                result = subprocess.run(
                    [command, *args],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    stderr = result.stderr.strip()
                    return f"error:shell returned {result.returncode}: {stderr}"

                if is_app_switch and pre_app is not None:
                    await _wait_for_app_focus_change(pre_app)
                else:
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

    def _try_ax_press(self, target_id: str | None) -> bool:
        """Try to perform AXPress on an element by its node ID.

        Uses stored AXUIElement refs from the last observation. Returns True
        if the action was performed successfully, False to fall back to
        coordinate-based action.
        """
        if target_id is None:
            return False

        element = self._last_ax_refs.get(target_id)
        if element is None:
            return False

        try:
            from ApplicationServices import AXUIElementPerformAction

            err = AXUIElementPerformAction(element, "AXPress")
            if err == 0:
                return True
            logger.debug("AXPress failed for %s with error %d", target_id, err)
        except Exception:
            logger.debug("AXPress exception for %s", target_id, exc_info=True)
        return False

    @staticmethod
    def _is_app_switch_command(command: str, args: list[str]) -> bool:
        """Detect whether a SHELL command is expected to change the frontmost app."""
        if command == "open" and "-a" in args:
            return True
        if command == "osascript" and any("activate" in a for a in args):
            return True
        return False

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


def _get_structured_ax_tree(pid: int, refs: dict[str, Any] | None = None) -> AXNode | None:
    """Get structured AX tree with stable IDs for an app by PID.

    If refs is provided, populates it with {node_id: raw_AXUIElement}
    so the environment can perform direct AX actions on elements.
    """
    try:
        from ApplicationServices import AXUIElementCreateApplication

        app_ref = AXUIElementCreateApplication(pid)
        return build_ax_tree(app_ref, _refs=refs)
    except Exception:
        logger.debug("Failed to get structured AX tree for PID %d", pid, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Window metadata
# ---------------------------------------------------------------------------


async def _wait_for_app_focus_change(pre_app: str) -> None:
    """Poll until the frontmost app differs from pre_app, then settle.

    Waits up to _APP_SWITCH_TIMEOUT seconds for the focus to change.
    If the timeout is reached, logs a warning and continues (does not fail).
    After focus changes, waits an additional _APP_SWITCH_SETTLE for the
    new app's UI to finish rendering.
    """
    import time

    deadline = time.monotonic() + _APP_SWITCH_TIMEOUT
    while time.monotonic() < deadline:
        info = _get_window_info()
        current_app = info.get("focused_app")
        if current_app is not None and current_app != pre_app:
            # App switched — give the new app time to render its UI
            await asyncio.sleep(_APP_SWITCH_SETTLE)
            return
        await asyncio.sleep(_APP_SWITCH_POLL)

    logger.warning(
        "App focus did not change from %r within %.1fs timeout",
        pre_app,
        _APP_SWITCH_TIMEOUT,
    )
    # Continue anyway — the action succeeded even if focus didn't shift


def _get_window_info() -> dict[str, Any]:
    """Get focused app name, PID, and window title.

    Pumps the NSRunLoop before querying so that workspace notifications
    (including app activation changes) are processed. Without this,
    NSWorkspace.frontmostApplication() returns stale data when called
    from within an asyncio event loop.
    """
    try:
        import Quartz  # type: ignore[import-untyped]
        from Foundation import NSDate, NSRunLoop  # type: ignore[import-untyped]

        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.01))

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
