"""Tests for MacOSDesktopEnvironment with mocked system calls."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from harness.environments.macos import (
    MacOSDesktopEnvironment,
    _normalize_keys,
    _serialize_ax_element,
)
from harness.runtime_results import ExecutionMethod, RuntimeResult
from harness.types import Action, ActionType, Observation, ObservationType, Task

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_task(**overrides: Any) -> Task:
    """Create a minimal Task for testing."""
    defaults: dict[str, Any] = {
        "task_id": "desktop-textedit-save",
        "version": "1.0",
        "environment": "macos_desktop",
        "goal": {"description": "Test task"},
        "verification": {"primary": {"method": "programmatic", "check": "file_exists('x')"}},
    }
    defaults.update(overrides)
    return Task(**defaults)


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------


class TestPermissionChecks:
    def test_screen_recording_check_success(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            assert MacOSDesktopEnvironment._check_screen_recording_permission()

    def test_screen_recording_check_failure(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("subprocess.run", return_value=mock_result):
            assert not MacOSDesktopEnvironment._check_screen_recording_permission()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


class TestSetup:
    def test_setup_fails_without_accessibility(self, tmp_path: Path) -> None:
        env = MacOSDesktopEnvironment()
        task = _make_task()

        with (
            patch.object(env, "_check_accessibility_permission", return_value=False),
            pytest.raises(RuntimeError, match="Accessibility permission"),
        ):
            asyncio.run(env.setup(task, tmp_path))

    def test_setup_fails_without_screen_recording(self, tmp_path: Path) -> None:
        env = MacOSDesktopEnvironment()
        task = _make_task()

        with (
            patch.object(env, "_check_accessibility_permission", return_value=True),
            patch.object(env, "_check_screen_recording_permission", return_value=False),
            pytest.raises(RuntimeError, match="Screen Recording permission"),
        ):
            asyncio.run(env.setup(task, tmp_path))

    def test_setup_creates_directories(self, tmp_path: Path) -> None:
        env = MacOSDesktopEnvironment()
        task = _make_task()

        with (
            patch.object(env, "_check_accessibility_permission", return_value=True),
            patch.object(env, "_check_screen_recording_permission", return_value=True),
        ):
            asyncio.run(env.setup(task, tmp_path))

        assert (tmp_path / "screenshots").is_dir()
        assert (tmp_path / "artifacts").is_dir()


# ---------------------------------------------------------------------------
# Observation collection
# ---------------------------------------------------------------------------


class TestObservation:
    def test_none_observation(self) -> None:
        env = MacOSDesktopEnvironment()
        obs = asyncio.run(env.collect_observation(ObservationType.NONE))
        assert obs.observation_type == ObservationType.NONE
        assert obs.screenshot is None

    def test_screenshot_observation(self) -> None:
        env = MacOSDesktopEnvironment()
        fake_png = b"\x89PNG\r\n\x1a\nfake"

        async def _fake_screenshot() -> bytes:
            return fake_png

        with (
            patch(
                "harness.environments.macos._get_window_info",
                return_value={"focused_app": "TextEdit", "focused_pid": 123},
            ),
            patch(
                "harness.environments.macos._take_screenshot",
                side_effect=_fake_screenshot,
            ),
        ):
            obs = asyncio.run(env.collect_observation(ObservationType.SCREENSHOT))

        assert obs.observation_type == ObservationType.SCREENSHOT
        assert obs.screenshot == fake_png
        assert obs.focused_app == "TextEdit"

    def test_aria_observation_with_tree(self) -> None:
        env = MacOSDesktopEnvironment()

        with (
            patch(
                "harness.environments.macos._get_window_info",
                return_value={
                    "focused_app": "TextEdit",
                    "focused_pid": 123,
                    "focused_window_title": "Untitled",
                },
            ),
            patch(
                "harness.environments.macos._get_ax_tree",
                return_value='AXApplication "TextEdit"\n  AXWindow "Untitled"',
            ),
        ):
            obs = asyncio.run(env.collect_observation(ObservationType.ARIA_STATE))

        assert obs.a11y_available is True
        assert obs.aria_snapshot is not None
        assert "TextEdit" in obs.aria_snapshot

    def test_aria_observation_without_tree(self) -> None:
        env = MacOSDesktopEnvironment()

        with (
            patch(
                "harness.environments.macos._get_window_info",
                return_value={"focused_app": "SomeApp", "focused_pid": 456},
            ),
            patch("harness.environments.macos._get_ax_tree", return_value=None),
        ):
            obs = asyncio.run(env.collect_observation(ObservationType.ARIA_STATE))

        assert obs.a11y_available is False
        assert obs.aria_snapshot is None


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------


class TestActionExecution:
    def _make_env(self) -> MacOSDesktopEnvironment:
        """Create env with readiness polling stubbed out for unit tests."""
        env = MacOSDesktopEnvironment()

        async def _noop_readiness(_pre_ids: Any) -> None:
            return None  # type: ignore[return-value]

        env._wait_for_readiness = _noop_readiness  # type: ignore[assignment]
        return env

    def _run_action(self, env: MacOSDesktopEnvironment, action: Action) -> RuntimeResult:
        return asyncio.run(env.execute_action(action))

    @patch("pyautogui.click")
    def test_click_action(self, mock_click: MagicMock) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.CLICK, params={"x": 100, "y": 200})
        result = self._run_action(env, action)
        assert result.summary == "ok"
        assert result.execution_method == ExecutionMethod.COORDINATES
        mock_click.assert_called_once_with(100, 200)

    def test_click_without_coords(self) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.CLICK, params={})
        result = self._run_action(env, action)
        assert result.summary.startswith("error")
        assert result.target_resolved is False

    @patch("pyautogui.write")
    def test_type_action(self, mock_write: MagicMock) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.TYPE, params={"text": "hello"})
        result = self._run_action(env, action)
        assert result.summary == "ok"
        assert result.execution_method == ExecutionMethod.KEYBOARD
        mock_write.assert_called_once_with("hello", interval=0.02)

    @patch("pyautogui.hotkey")
    def test_press_action(self, mock_hotkey: MagicMock) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.PRESS, params={"key": "command+s"})
        result = self._run_action(env, action)
        assert result.summary == "ok"
        mock_hotkey.assert_called_once_with("command", "s")

    @patch("pyautogui.hotkey")
    def test_press_unicode_symbols(self, mock_hotkey: MagicMock) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.PRESS, params={"key": "\u2318S"})
        result = self._run_action(env, action)
        assert result.summary == "ok"
        mock_hotkey.assert_called_once_with("command", "s")

    @patch("pyautogui.hotkey")
    def test_press_ascii_aliases(self, mock_hotkey: MagicMock) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.PRESS, params={"key": "CMD+SHIFT+S"})
        result = self._run_action(env, action)
        assert result.summary == "ok"
        mock_hotkey.assert_called_once_with("command", "shift", "s")

    @patch("pyautogui.hotkey")
    def test_press_action_with_list_key(self, mock_hotkey: MagicMock) -> None:
        """press_keys with list key param must not crash (B3)."""
        env = self._make_env()
        action = Action(action_type=ActionType.PRESS, params={"key": ["CMD", "SHIFT", "S"]})
        result = self._run_action(env, action)
        assert result.summary == "ok"
        mock_hotkey.assert_called_once_with("command", "shift", "s")

    def test_shell_action_success(self) -> None:
        env = self._make_env()
        action = Action(
            action_type=ActionType.SHELL,
            params={"command": "osascript", "args": ["-e", 'return "ok"']},
        )
        result = self._run_action(env, action)
        assert result.summary == "ok"
        assert result.execution_method == ExecutionMethod.SHELL

    def test_shell_action_blocked_command(self) -> None:
        env = self._make_env()
        action = Action(
            action_type=ActionType.SHELL,
            params={"command": "/bin/sh", "args": ["-c", "echo pwned"]},
        )
        result = self._run_action(env, action)
        assert "not in allowlist" in result.summary

    def test_type_without_text_param(self) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.TYPE, params={})
        result = self._run_action(env, action)
        assert result.summary.startswith("error")

    def test_press_without_key_param(self) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.PRESS, params={})
        result = self._run_action(env, action)
        assert result.summary.startswith("error")

    def test_shell_open_app_waits_for_focus(self) -> None:
        """open -a command should wait for frontmost app to change."""
        env = self._make_env()
        action = Action(
            action_type=ActionType.SHELL,
            params={"command": "open", "args": ["-a", "TextEdit"]},
        )

        call_count = 0

        def mock_window_info() -> dict:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # First calls: still on the old app
                return {"focused_app": "Code", "focused_pid": 100}
            # After a few polls: app switched
            return {"focused_app": "TextEdit", "focused_pid": 200}

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with (
            patch("subprocess.run", return_value=mock_result),
            patch("harness.environments.macos._get_window_info", side_effect=mock_window_info),
        ):
            result = self._run_action(env, action)

        assert result.summary == "ok"
        assert call_count >= 3  # polled until focus changed

    def test_shell_non_app_switch_uses_settle_delay(self) -> None:
        """Regular osascript commands should not poll for focus change."""
        env = self._make_env()
        action = Action(
            action_type=ActionType.SHELL,
            params={"command": "osascript", "args": ["-e", 'return "ok"']},
        )
        result = self._run_action(env, action)
        assert result.summary == "ok"

    def test_is_app_switch_detection(self) -> None:
        assert MacOSDesktopEnvironment._is_app_switch_command("open", ["-a", "TextEdit"]) is True
        assert MacOSDesktopEnvironment._is_app_switch_command("open", ["file.txt"]) is False
        assert (
            MacOSDesktopEnvironment._is_app_switch_command(
                "osascript", ["-e", 'tell application "X" to activate']
            )
            is True
        )
        assert (
            MacOSDesktopEnvironment._is_app_switch_command("osascript", ["-e", 'return "ok"'])
            is False
        )

    def test_wait_action(self) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.WAIT, params={"ms": 10})
        result = self._run_action(env, action)
        assert result.summary == "ok"

    def test_done_action(self) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.DONE)
        result = self._run_action(env, action)
        assert result.summary == "done"

    def test_fail_action(self) -> None:
        env = self._make_env()
        action = Action(action_type=ActionType.FAIL, params={"reason": "test"})
        result = self._run_action(env, action)
        assert result.summary == "fail:test"


# ---------------------------------------------------------------------------
# Key normalization
# ---------------------------------------------------------------------------


class TestKeyNormalization:
    def test_pyautogui_format_passthrough(self) -> None:
        assert _normalize_keys("command+s") == ["command", "s"]

    def test_unicode_cmd_s(self) -> None:
        assert _normalize_keys("\u2318S") == ["command", "s"]

    def test_unicode_cmd_shift_s(self) -> None:
        assert _normalize_keys("\u2318\u21e7S") == ["command", "shift", "s"]

    def test_ascii_cmd_s(self) -> None:
        assert _normalize_keys("CMD+S") == ["command", "s"]

    def test_ascii_cmd_shift_s(self) -> None:
        assert _normalize_keys("CMD+SHIFT+S") == ["command", "shift", "s"]

    def test_mixed_unicode_plus_separator(self) -> None:
        assert _normalize_keys("\u2318+S") == ["command", "s"]

    def test_ctrl_alias(self) -> None:
        assert _normalize_keys("CTRL+C") == ["control", "c"]

    def test_alt_alias(self) -> None:
        assert _normalize_keys("ALT+TAB") == ["option", "tab"]

    def test_single_key(self) -> None:
        assert _normalize_keys("escape") == ["escape"]

    def test_unicode_backspace(self) -> None:
        assert _normalize_keys("\u232b") == ["backspace"]

    def test_unicode_return(self) -> None:
        assert _normalize_keys("\u21a9") == ["return"]

    def test_option_symbol(self) -> None:
        assert _normalize_keys("\u2325N") == ["option", "n"]

    def test_enter_alias(self) -> None:
        assert _normalize_keys("ENTER") == ["return"]

    def test_multiple_modifiers(self) -> None:
        assert _normalize_keys("\u2303\u2325\u2318\u232b") == [
            "control",
            "option",
            "command",
            "backspace",
        ]

    def test_list_input(self) -> None:
        """LLM sometimes returns key as a list — must not crash."""
        assert _normalize_keys(["CMD", "SHIFT", "S"]) == ["command", "shift", "s"]

    def test_list_input_single_key(self) -> None:
        assert _normalize_keys(["escape"]) == ["escape"]


# ---------------------------------------------------------------------------
# AX tree serialization
# ---------------------------------------------------------------------------


class TestAXSerialization:
    def test_serialize_simple_element(self) -> None:
        mock_element = MagicMock()

        def mock_get_attr(el: Any, name: str) -> Any:
            attrs = {
                "AXRole": "AXButton",
                "AXTitle": "OK",
                "AXValue": "",
                "AXDescription": "",
                "AXChildren": [],
            }
            return attrs.get(name)

        with patch("harness.environments.macos._get_attr", side_effect=mock_get_attr):
            result = _serialize_ax_element(mock_element, depth=0, max_depth=5)

        assert 'AXButton "OK"' in result

    def test_serialize_respects_max_depth(self) -> None:
        mock_element = MagicMock()
        result = _serialize_ax_element(mock_element, depth=11, max_depth=10)
        assert result == ""


# ---------------------------------------------------------------------------
# file_contains grader
# ---------------------------------------------------------------------------


class TestFileContainsGrader:
    def test_passes_when_file_contains_text(self, tmp_path: Path) -> None:
        from harness.graders import grade
        from harness.types import Task

        target = tmp_path / "test.txt"
        target.write_text("Hello from the eval harness")

        task = Task(
            task_id="test",
            version="1.0",
            goal={"description": "test"},
            verification={
                "primary": {
                    "method": "programmatic",
                    "check": f"file_contains('{target}', 'Hello from the eval harness')",
                }
            },
        )
        result = grade(task, tmp_path)
        assert result.passed is True
        assert result.method == "file_contains"

    def test_fails_when_file_missing(self, tmp_path: Path) -> None:
        from harness.graders import grade
        from harness.types import Task

        task = Task(
            task_id="test",
            version="1.0",
            goal={"description": "test"},
            verification={
                "primary": {
                    "method": "programmatic",
                    "check": "file_contains('/nonexistent/file.txt', 'test')",
                }
            },
        )
        result = grade(task, tmp_path)
        assert result.passed is False
        assert result.method == "file_contains"

    def test_fails_when_text_not_present(self, tmp_path: Path) -> None:
        from harness.graders import grade
        from harness.types import Task

        target = tmp_path / "test.txt"
        target.write_text("something else entirely")

        task = Task(
            task_id="test",
            version="1.0",
            goal={"description": "test"},
            verification={
                "primary": {
                    "method": "programmatic",
                    "check": f"file_contains('{target}', 'expected text')",
                }
            },
        )
        result = grade(task, tmp_path)
        assert result.passed is False


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------


class TestTaskLoading:
    def test_desktop_task_loads(self) -> None:
        from harness.task_loader import load_task

        task = load_task("tasks/desktop_textedit_save/task.yaml")
        assert task.task_id == "desktop-textedit-save"
        assert task.environment == "macos_desktop"
        assert "content" in task.goal.variables
        assert "filename" in task.goal.variables
        assert "directory" in task.goal.variables


# ---------------------------------------------------------------------------
# Deterministic script
# ---------------------------------------------------------------------------


class TestDeterministicDesktopScript:
    def test_textedit_script_returns_shell_action(self) -> None:
        from harness.adapters.deterministic import DeterministicAdapter

        task = _make_task()
        task.goal.variables = {
            "content": MagicMock(type="string", default="test content"),
            "filename": MagicMock(type="string", default="test.txt"),
            "directory": MagicMock(type="path", default="/tmp/test"),
        }

        adapter = DeterministicAdapter()
        obs = Observation(observation_type=ObservationType.NONE)
        actions = adapter.decide(obs, task)

        assert len(actions) == 2
        assert actions[0].action_type == ActionType.SHELL
        assert actions[0].params["command"] == "osascript"
        assert "TextEdit" in actions[0].params["args"][1]
        assert actions[1].action_type == ActionType.DONE


# ---------------------------------------------------------------------------
# Environment selection
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Readiness polling and state-diff verification (M3)
# ---------------------------------------------------------------------------


class TestReadinessPolling:
    """Tests for bounded readiness polling replacing fixed settle delay."""

    def test_click_populates_state_changed(self) -> None:
        """Click action should set state_changed on the result."""
        env = MacOSDesktopEnvironment()

        async def _readiness_true(_pre_ids: Any) -> bool:
            return True

        env._wait_for_readiness = _readiness_true  # type: ignore[assignment]

        with patch("pyautogui.click"):
            result = asyncio.run(
                env.execute_action(Action(action_type=ActionType.CLICK, params={"x": 10, "y": 20}))
            )
        assert result.state_changed is True

    def test_click_state_unchanged(self) -> None:
        env = MacOSDesktopEnvironment()

        async def _readiness_false(_pre_ids: Any) -> bool:
            return False

        env._wait_for_readiness = _readiness_false  # type: ignore[assignment]

        with patch("pyautogui.click"):
            result = asyncio.run(
                env.execute_action(Action(action_type=ActionType.CLICK, params={"x": 10, "y": 20}))
            )
        assert result.state_changed is False

    def test_readiness_returns_early_on_change(self) -> None:
        """_wait_for_readiness should return True when post-state differs from pre."""
        env = MacOSDesktopEnvironment()
        pre_ids = frozenset({"ax_1", "ax_2"})
        # Simulate post-state that differs by one element
        post_ids = frozenset({"ax_1", "ax_2", "ax_3"})

        call_count = 0

        def mock_get_post_ids() -> frozenset[str]:
            nonlocal call_count
            call_count += 1
            return post_ids

        env._get_post_state_ids = mock_get_post_ids  # type: ignore[assignment]
        result = asyncio.run(env._wait_for_readiness(pre_ids))
        assert result is True
        # Should have needed very few polls (ideally 1 after the min wait)
        assert call_count >= 1

    def test_readiness_returns_false_on_timeout(self) -> None:
        """_wait_for_readiness should return False when state never changes."""
        from harness.environments import macos

        # Temporarily reduce timeout for test speed
        orig_max = macos._READINESS_MAX_WAIT
        orig_min = macos._READINESS_MIN_WAIT
        macos._READINESS_MAX_WAIT = 0.3
        macos._READINESS_MIN_WAIT = 0.05
        try:
            env = MacOSDesktopEnvironment()
            pre_ids = frozenset({"ax_1", "ax_2"})
            env._get_post_state_ids = lambda: pre_ids  # type: ignore[assignment]

            result = asyncio.run(env._wait_for_readiness(pre_ids))
            assert result is False
        finally:
            macos._READINESS_MAX_WAIT = orig_max
            macos._READINESS_MIN_WAIT = orig_min

    def test_readiness_returns_none_on_empty_pre(self) -> None:
        """When pre_ids is empty (no tree), return None and fall through."""
        env = MacOSDesktopEnvironment()
        from harness.environments import macos

        orig = macos._READINESS_MIN_WAIT
        macos._READINESS_MIN_WAIT = 0.01
        try:
            result = asyncio.run(env._wait_for_readiness(frozenset()))
            assert result is None
        finally:
            macos._READINESS_MIN_WAIT = orig


class TestAXPressErrorOverride:
    """AXPress transport error should be overridden when state evidence shows success."""

    def test_ax_press_error_overridden_by_state_change(self) -> None:
        """If AXPress fails but state changed, result should be ok."""
        env = MacOSDesktopEnvironment()
        env._last_ax_refs = {"ax_target": MagicMock()}

        # AXPress will fail (returns non-zero error code)
        with patch(
            "harness.environments.macos.MacOSDesktopEnvironment._try_ax_press",
            return_value=False,
        ):
            # But state will change
            async def _readiness_true(_pre_ids: Any) -> bool:
                return True

            env._wait_for_readiness = _readiness_true  # type: ignore[assignment]

            action = Action(
                action_type=ActionType.CLICK,
                params={"semantic_target": "ax_target"},
            )
            result = asyncio.run(env.execute_action(action))

        assert result.status.value == "ok"
        assert result.state_changed is True
        assert result.metadata.get("ax_press_transport_error") is True

    def test_ax_press_error_not_overridden_without_state_change(self) -> None:
        """If AXPress fails and no state change, result should be error."""
        env = MacOSDesktopEnvironment()
        env._last_ax_refs = {"ax_target": MagicMock()}

        with patch(
            "harness.environments.macos.MacOSDesktopEnvironment._try_ax_press",
            return_value=False,
        ):

            async def _readiness_false(_pre_ids: Any) -> bool:
                return False

            env._wait_for_readiness = _readiness_false  # type: ignore[assignment]

            action = Action(
                action_type=ActionType.CLICK,
                params={"semantic_target": "ax_target"},
            )
            result = asyncio.run(env.execute_action(action))

        assert result.status.value == "error"
        assert result.target_resolved is False


# ---------------------------------------------------------------------------
# Environment selection
# ---------------------------------------------------------------------------


class TestEnvironmentSelection:
    def test_browser_is_default(self) -> None:
        from harness.environments.browser import BrowserEnvironment
        from harness.runner import ENVIRONMENTS

        assert ENVIRONMENTS["browser"] is BrowserEnvironment

    def test_macos_desktop_registered(self) -> None:
        from harness.runner import ENVIRONMENTS

        assert "macos_desktop" in ENVIRONMENTS
        assert ENVIRONMENTS["macos_desktop"] is MacOSDesktopEnvironment
