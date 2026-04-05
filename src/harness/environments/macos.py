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

from harness.ax_state import (
    AXNode,
    AXQuality,
    build_ax_tree,
    compute_ax_quality,
    find_node_by_id,
    interactive_id_set,
    prune_interactive,
)
from harness.runtime_results import (
    ExecutionMethod,
    ResultStatus,
    RuntimeResult,
    done,
    error,
    fail,
    ok,
)
from harness.types import Action, ActionType, Observation, ObservationType, Task

logger = logging.getLogger(__name__)

# Readiness polling configuration (replaces fixed _ACTION_SETTLE_DELAY)
_READINESS_POLL_INTERVAL = 0.1  # seconds between AX tree polls
_READINESS_MAX_WAIT = 2.0  # upper bound total wait
_READINESS_MIN_WAIT = 0.15  # floor: always wait at least this long

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


# ---------------------------------------------------------------------------
# Semantic intent: transport builders + postcondition checkers
# ---------------------------------------------------------------------------

# Type alias for transport tuples: (method, args)
_Transport = tuple[str, list[str]]


def _safe_app_name(name: str) -> str:
    """Remove characters that break AppleScript string interpolation."""
    return name.replace('"', "").replace("\\", "").strip()


def _save_document_transports(app_name: str) -> list[_Transport]:
    """Ordered transports for save_document intent."""
    transports: list[_Transport] = []
    safe = _safe_app_name(app_name)
    if safe:
        transports.append(("osascript", ["-e", f'tell application "{safe}" to save document 1']))
    transports.append(("keyboard", ["command", "s"]))
    return transports


def _new_document_transports(app_name: str) -> list[_Transport]:
    """Ordered transports for new_document intent."""
    transports: list[_Transport] = []
    safe = _safe_app_name(app_name)
    if safe:
        transports.append(("osascript", ["-e", f'tell application "{safe}" to make new document']))
    transports.append(("keyboard", ["command", "n"]))
    return transports


def _close_window_transports(app_name: str) -> list[_Transport]:
    """Ordered transports for close_window intent."""
    transports: list[_Transport] = []
    safe = _safe_app_name(app_name)
    if safe:
        transports.append(
            ("osascript", ["-e", f'tell application "{safe}" to close front window'])
        )
    transports.append(("keyboard", ["command", "w"]))
    return transports


def _check_save_postcondition(
    pre_title: str | None, post_title: str | None, state_changed: bool | None
) -> bool | None:
    """Check if save_document had the expected effect.

    Strong signal: window title changed (e.g., lost "Edited" flag, gained filename).
    Weaker signal: AX state changed (dialog appeared, tree restructured).
    """
    if pre_title and post_title and pre_title != post_title:
        return True
    if state_changed is True:
        return True
    return None


def _check_new_document_postcondition(
    pre_title: str | None, post_title: str | None, state_changed: bool | None
) -> bool | None:
    """Check if new_document created a new editable document."""
    if post_title and "untitled" in post_title.lower():
        return True
    if pre_title != post_title and state_changed is True:
        return True
    if state_changed is True:
        return True
    return None


def _check_close_window_postcondition(
    pre_title: str | None, post_title: str | None, state_changed: bool | None
) -> bool | None:
    """Check if close_window closed or dismissed the front window."""
    if pre_title and not post_title:
        return True
    if pre_title and post_title and pre_title != post_title:
        return True
    if state_changed is True:
        return True
    return None


# Registry: intent name → (transport builder, postcondition checker)
_PostconditionFn = type(_check_save_postcondition)
_TransportFn = type(_save_document_transports)

_INTENT_REGISTRY: dict[str, tuple[_TransportFn, _PostconditionFn]] = {
    "save_document": (_save_document_transports, _check_save_postcondition),
    "new_document": (_new_document_transports, _check_new_document_postcondition),
    "close_window": (_close_window_transports, _check_close_window_postcondition),
}


def _normalize_keys(raw: str | list[str]) -> list[str]:
    """Normalize a key combo string into pyautogui-compatible key names.

    Handles all common formats:
      - Unicode:  ⌘S, ⌘⇧S, ⌫
      - ASCII:    CMD+S, CMD+SHIFT+S, command+s
      - Mixed:    ⌘+S
      - List:     ["CMD", "SHIFT", "S"]  (LLM sometimes returns this)
    """
    # Handle list input by normalizing each element individually.
    if isinstance(raw, list):
        result: list[str] = []
        for item in raw:
            result.extend(_normalize_keys(str(item)))
        return result

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
        self._last_focused_pid: int | None = None

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
            self._last_focused_pid = pid
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

    def get_ax_quality(self) -> AXQuality | None:
        """Compute AX quality from the last observed tree, if available."""
        if self._last_ax_tree is None:
            return None
        interactive = prune_interactive(self._last_ax_tree, max_elements=500)
        return compute_ax_quality(interactive)

    def _capture_pre_state(self) -> frozenset[str]:
        """Capture the interactive element ID set before an action.

        Returns an empty frozenset if the tree is unavailable. The caller
        should treat empty pre-state as "cannot determine change".
        """
        if self._last_ax_tree is not None:
            return interactive_id_set(self._last_ax_tree)
        return frozenset()

    def _get_post_state_ids(self) -> frozenset[str]:
        """Query a fresh AX tree and return the interactive ID set.

        Also updates _last_ax_tree so subsequent AX quality queries
        reflect the post-action state.
        """
        if self._last_focused_pid is None:
            return frozenset()
        tree = _get_structured_ax_tree(self._last_focused_pid)
        if tree is None:
            return frozenset()
        self._last_ax_tree = tree
        return interactive_id_set(tree)

    def _poll_post_state_ids(self) -> frozenset[str]:
        """Query a fresh AX tree for polling without mutating _last_ax_tree."""
        if self._last_focused_pid is None:
            return frozenset()
        tree = _get_structured_ax_tree(self._last_focused_pid)
        if tree is None:
            return frozenset()
        return interactive_id_set(tree)

    async def _wait_for_readiness(self, pre_ids: frozenset[str]) -> bool | None:
        """Poll until AX state changes or timeout, replacing fixed sleep.

        Returns True if state changed, False if unchanged after timeout,
        None if we cannot determine (empty pre-state).

        Only updates _last_ax_tree on the final check (not during intermediate
        polls) so transient states don't corrupt the stored tree.
        """
        import time

        if not pre_ids:
            # Cannot determine change — fall back to minimum wait
            await asyncio.sleep(_READINESS_MIN_WAIT)
            return None

        # Always wait the minimum floor first
        await asyncio.sleep(_READINESS_MIN_WAIT)

        deadline = time.monotonic() + (_READINESS_MAX_WAIT - _READINESS_MIN_WAIT)
        while time.monotonic() < deadline:
            post_ids = self._poll_post_state_ids()
            if post_ids and post_ids != pre_ids:
                # State changed — do a final fetch that updates _last_ax_tree
                self._get_post_state_ids()
                return True
            await asyncio.sleep(_READINESS_POLL_INTERVAL)

        # Final check after timeout (updates _last_ax_tree)
        post_ids = self._get_post_state_ids()
        return bool(post_ids and post_ids != pre_ids)

    async def execute_action(self, action: Action) -> RuntimeResult:
        import pyautogui  # type: ignore[import-untyped]

        # Resolve semantic targets to coordinates if needed
        action = self.resolve_semantic_target(action)
        params = action.params

        match action.action_type:
            case ActionType.CLICK:
                pre_ids = self._capture_pre_state()
                if "x" in params and "y" in params:
                    pyautogui.click(int(params["x"]), int(params["y"]))
                    changed = await self._wait_for_readiness(pre_ids)
                    return ok(
                        method=ExecutionMethod.COORDINATES,
                        state_changed=changed,
                    )
                # Try AXPress — may return error even when action succeeds
                ax_ok = self._try_ax_press(params.get("semantic_target"))
                changed = await self._wait_for_readiness(pre_ids)
                if ax_ok:
                    return ok(
                        method=ExecutionMethod.AX_PRESS,
                        state_changed=changed,
                    )
                # AXPress reported failure — check if state actually changed
                if changed is True:
                    # State evidence overrides transport error
                    return ok(
                        message="ax_press_error_overridden_by_state_change",
                        method=ExecutionMethod.AX_PRESS,
                        state_changed=True,
                        metadata={"ax_press_transport_error": True},
                    )
                return error(
                    "click requires x,y coordinates or a pressable AX element",
                    target_resolved=False,
                )

            case ActionType.DOUBLE_CLICK:
                pre_ids = self._capture_pre_state()
                if "x" in params and "y" in params:
                    pyautogui.doubleClick(int(params["x"]), int(params["y"]))
                    changed = await self._wait_for_readiness(pre_ids)
                    return ok(
                        method=ExecutionMethod.COORDINATES,
                        state_changed=changed,
                    )
                return error(
                    "double_click requires x,y coordinates for desktop",
                    target_resolved=False,
                )

            case ActionType.TYPE:
                text = params.get("text")
                if text is None:
                    return error("type requires 'text' param")
                pre_ids = self._capture_pre_state()
                pyautogui.write(text, interval=0.02)
                changed = await self._wait_for_readiness(pre_ids)
                return ok(
                    method=ExecutionMethod.KEYBOARD,
                    state_changed=changed,
                )

            case ActionType.PRESS:
                key = params.get("key")
                if key is None:
                    return error("press requires 'key' param")
                keys = _normalize_keys(key)
                if not keys:
                    return error("press requires a non-empty key list")
                pre_ids = self._capture_pre_state()
                pyautogui.hotkey(*keys)
                changed = await self._wait_for_readiness(pre_ids)
                return ok(
                    method=ExecutionMethod.KEYBOARD,
                    state_changed=changed,
                )

            case ActionType.SCROLL:
                pre_ids = self._capture_pre_state()
                clicks = params.get("delta_y", 0)
                pyautogui.scroll(int(clicks))
                changed = await self._wait_for_readiness(pre_ids)
                return ok(
                    method=ExecutionMethod.COORDINATES,
                    state_changed=changed,
                )

            case ActionType.WAIT:
                ms = params.get("ms", 1000)
                await asyncio.sleep(int(ms) / 1000.0)
                return ok(method=ExecutionMethod.WAIT)

            case ActionType.SHELL:
                command = params.get("command", "")
                if command not in _SHELL_ALLOWLIST:
                    return error(
                        f"shell command {command!r} not in allowlist {_SHELL_ALLOWLIST}",
                        method=ExecutionMethod.SHELL,
                    )
                args = params.get("args", [])

                # Record pre-command frontmost app for app-switch detection
                pre_app: str | None = None
                is_app_switch = self._is_app_switch_command(command, args)
                if is_app_switch:
                    pre_info = _get_window_info()
                    pre_app = pre_info.get("focused_app")

                proc_result = subprocess.run(
                    [command, *args],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc_result.returncode != 0:
                    stderr = proc_result.stderr.strip()
                    return error(
                        f"shell returned {proc_result.returncode}: {stderr}",
                        method=ExecutionMethod.SHELL,
                    )

                if is_app_switch and pre_app is not None:
                    await _wait_for_app_focus_change(pre_app)
                else:
                    await asyncio.sleep(_READINESS_MIN_WAIT)
                return ok(method=ExecutionMethod.SHELL)

            case ActionType.SEMANTIC_INTENT:
                return await self._execute_semantic_intent(action)

            case ActionType.MOVE:
                if "x" in params and "y" in params:
                    pyautogui.moveTo(int(params["x"]), int(params["y"]))
                    return ok(method=ExecutionMethod.COORDINATES)
                return error("move requires x,y coordinates")

            case ActionType.DONE:
                return done()

            case ActionType.FAIL:
                reason = params.get("reason", "Agent declared failure")
                return fail(reason)

            case _:
                return error(f"unsupported action type {action.action_type} for desktop")

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

    async def _execute_semantic_intent(self, action: Action) -> RuntimeResult:
        """Execute a semantic intent with deterministic transport and postcondition check.

        Tries transports in order. After each attempt, runs an intent-specific
        postcondition check. If postcondition clearly fails, tries the next
        transport. If all transports are exhausted, returns error.
        """
        import pyautogui  # type: ignore[import-untyped]

        intent = action.params.get("intent", "")
        registry_entry = _INTENT_REGISTRY.get(intent)
        if registry_entry is None:
            return error(f"Unknown semantic intent: {intent}")

        transport_fn, postcondition_fn = registry_entry

        # Capture pre-action window context for postcondition comparison
        pre_info = _get_window_info()
        app_name = pre_info.get("focused_app", "")
        pre_title = pre_info.get("focused_window_title")

        transports = transport_fn(app_name)

        for method, args in transports:
            pre_ids = self._capture_pre_state()
            transport_ok = False

            if method == "osascript":
                try:
                    proc = subprocess.run(
                        ["osascript", *args],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    transport_ok = proc.returncode == 0
                except (subprocess.TimeoutExpired, OSError):
                    transport_ok = False
                if not transport_ok:
                    logger.debug("osascript transport failed for intent %s, trying next", intent)
                    continue
            elif method == "keyboard":
                try:
                    pyautogui.hotkey(*args)
                    transport_ok = True
                except Exception:
                    logger.debug("keyboard transport failed for intent %s", intent, exc_info=True)
                    continue

            state_changed = await self._wait_for_readiness(pre_ids)

            # Postcondition check
            post_info = _get_window_info()
            post_title = post_info.get("focused_window_title")
            postcondition_met = postcondition_fn(pre_title, post_title, state_changed)

            exec_method = (
                ExecutionMethod.SHELL if method == "osascript" else ExecutionMethod.KEYBOARD
            )

            if postcondition_met is not False:
                # Success or uncertain — accept this transport
                return ok(
                    method=exec_method,
                    state_changed=state_changed,
                    expected_change_observed=postcondition_met,
                    metadata={"intent": intent, "transport": method},
                )

            # Postcondition clearly failed — try next transport
            logger.debug(
                "Postcondition failed for %s via %s, trying next transport",
                intent,
                method,
            )

        # All transports exhausted
        return error(
            f"semantic intent '{intent}': all transports failed postconditions",
            method=ExecutionMethod.OTHER,
        )

    @staticmethod
    def _is_app_switch_command(command: str, args: list[str]) -> bool:
        """Detect whether a SHELL command is expected to change the frontmost app."""
        if command == "open" and "-a" in args:
            return True
        return command == "osascript" and any("activate" in a for a in args)

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
