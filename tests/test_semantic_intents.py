"""Tests for semantic intent layer: adapter emission, environment resolution,
transport fallback, postcondition checks, and expected_change_observed wiring.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from harness.ax_state import AXNode, build_ax_tree_from_dict
from harness.environments.macos import (
    MacOSDesktopEnvironment,
    _check_close_window_postcondition,
    _check_new_document_postcondition,
    _check_save_postcondition,
    _close_window_transports,
    _new_document_transports,
    _safe_app_name,
    _save_document_transports,
)
from harness.runtime_results import ExecutionMethod, ok
from harness.types import Action, ActionType, Observation, ObservationType, Task


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_task(**overrides: Any) -> Task:
    defaults: dict[str, Any] = {
        "task_id": "desktop-textedit-save",
        "version": "1.0",
        "environment": "macos_desktop",
        "goal": {"description": "Open TextEdit and save a file"},
        "verification": {"primary": {"method": "programmatic", "check": "file_exists('x')"}},
    }
    defaults.update(overrides)
    return Task(**defaults)


def _sample_tree() -> AXNode:
    data = {
        "role": "AXApplication",
        "title": "TextEdit",
        "children": [
            {
                "role": "AXWindow",
                "title": "Untitled",
                "bounds": [0, 0, 800, 600],
                "children": [
                    {
                        "role": "AXButton",
                        "title": "Save",
                        "enabled": True,
                        "bounds": [450, 320, 80, 24],
                    },
                ],
            },
        ],
    }
    tree = build_ax_tree_from_dict(data)
    assert tree is not None
    return tree


def _make_observation(ax_tree: AXNode | None = None) -> Observation:
    obs = Observation(
        observation_type=ObservationType.ARIA_STATE,
        aria_snapshot='AXApplication "TextEdit"',
        focused_app="TextEdit",
        page_title="Untitled",
        a11y_available=True,
    )
    if ax_tree is not None:
        obs._ax_tree = ax_tree  # type: ignore[attr-defined]
    return obs


def _make_adapter():
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}),
        patch("harness.adapters.structured_state_desktop.OpenAI") as mock_openai_cls,
    ):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        from harness.adapters.structured_state_desktop import StructuredStateDesktopAdapter

        adapter = StructuredStateDesktopAdapter()
        adapter._client = mock_client
        return adapter


# ---------------------------------------------------------------------------
# Adapter: emitting semantic intents
# ---------------------------------------------------------------------------


class TestAdapterEmitsSemanticIntents:
    def test_save_document_emits_semantic_intent(self) -> None:
        adapter = _make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {
                            "action": "save_document",
                            "expected_change": "Document saved to disk",
                        }
                    )
                )
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.SEMANTIC_INTENT
        assert actions[0].params["intent"] == "save_document"
        assert actions[0].params["expected_change"] == "Document saved to disk"

    def test_new_document_emits_semantic_intent(self) -> None:
        adapter = _make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"action": "new_document"})))
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.SEMANTIC_INTENT
        assert actions[0].params["intent"] == "new_document"

    def test_close_window_emits_semantic_intent(self) -> None:
        adapter = _make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"action": "close_window"})))
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.SEMANTIC_INTENT
        assert actions[0].params["intent"] == "close_window"

    def test_semantic_intent_no_expected_change(self) -> None:
        """expected_change is optional — should not appear in params if absent."""
        adapter = _make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"action": "save_document"})))
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert actions[0].action_type == ActionType.SEMANTIC_INTENT
        assert "expected_change" not in actions[0].params

    def test_text_fallback_emits_semantic_intent(self) -> None:
        """Semantic intents should work even without a structured AX tree."""
        adapter = _make_adapter()
        obs = _make_observation(ax_tree=None)  # no structured tree
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"action": "save_document"})))
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.SEMANTIC_INTENT
        assert actions[0].params["intent"] == "save_document"

    def test_press_keys_still_works(self) -> None:
        """press_keys remains functional as escape hatch."""
        adapter = _make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps({"action": "press_keys", "value": "command+z"})
                )
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.PRESS
        assert actions[0].params["key"] == "command+z"


class TestAdapterPrompt:
    def test_prompt_lists_semantic_intents(self) -> None:
        adapter = _make_adapter()
        task = _make_task()

        prompt = adapter._build_prompt(
            task=task,
            focused_app="TextEdit",
            window_title="Untitled",
            elements_text='[ax_1] AXButton "Save"',
        )
        assert "save_document" in prompt
        assert "new_document" in prompt
        assert "close_window" in prompt
        assert "PREFERRED" in prompt
        assert "escape hatch" in prompt


# ---------------------------------------------------------------------------
# Transport builders
# ---------------------------------------------------------------------------


class TestTransportBuilders:
    def test_save_transports_with_app(self) -> None:
        transports = _save_document_transports("TextEdit")
        assert len(transports) == 2
        assert transports[0][0] == "osascript"
        assert "save document 1" in transports[0][1][-1]
        assert "TextEdit" in transports[0][1][-1]
        assert transports[1] == ("keyboard", ["command", "s"])

    def test_save_transports_without_app(self) -> None:
        transports = _save_document_transports("")
        assert len(transports) == 1
        assert transports[0] == ("keyboard", ["command", "s"])

    def test_new_document_transports(self) -> None:
        transports = _new_document_transports("TextEdit")
        assert transports[0][0] == "osascript"
        assert "make new document" in transports[0][1][-1]
        assert transports[1] == ("keyboard", ["command", "n"])

    def test_close_window_transports(self) -> None:
        transports = _close_window_transports("TextEdit")
        assert transports[0][0] == "osascript"
        assert "close front window" in transports[0][1][-1]
        assert transports[1] == ("keyboard", ["command", "w"])

    def test_safe_app_name_strips_quotes(self) -> None:
        assert _safe_app_name('My "App"') == "My App"
        assert _safe_app_name("Normal") == "Normal"
        assert _safe_app_name("") == ""


# ---------------------------------------------------------------------------
# Postcondition checkers
# ---------------------------------------------------------------------------


class TestPostconditionChecks:
    # -- save_document --
    def test_save_title_changed(self) -> None:
        assert _check_save_postcondition("Untitled", "report.txt", None) is True

    def test_save_state_changed(self) -> None:
        assert _check_save_postcondition("Untitled", "Untitled", True) is True

    def test_save_no_signal(self) -> None:
        assert _check_save_postcondition("Untitled", "Untitled", False) is None

    def test_save_no_signal_none(self) -> None:
        assert _check_save_postcondition("Untitled", "Untitled", None) is None

    def test_save_both_none_titles(self) -> None:
        assert _check_save_postcondition(None, None, False) is None

    # -- new_document --
    def test_new_doc_untitled_appears(self) -> None:
        assert _check_new_document_postcondition("report.txt", "Untitled", True) is True

    def test_new_doc_state_changed(self) -> None:
        assert _check_new_document_postcondition("report.txt", "report.txt", True) is True

    def test_new_doc_no_signal(self) -> None:
        assert _check_new_document_postcondition("report.txt", "report.txt", False) is None

    # -- close_window --
    def test_close_title_disappeared(self) -> None:
        assert _check_close_window_postcondition("Untitled", None, None) is True

    def test_close_title_changed(self) -> None:
        assert _check_close_window_postcondition("Doc1", "Doc2", None) is True

    def test_close_state_changed(self) -> None:
        assert _check_close_window_postcondition("Doc1", "Doc1", True) is True

    def test_close_no_signal(self) -> None:
        assert _check_close_window_postcondition("Doc1", "Doc1", False) is None


# ---------------------------------------------------------------------------
# Environment: semantic intent execution
# ---------------------------------------------------------------------------


class TestSemanticIntentExecution:
    def _make_env(self) -> MacOSDesktopEnvironment:
        env = MacOSDesktopEnvironment()

        async def _noop_readiness(_pre_ids: Any) -> None:
            return None  # type: ignore[return-value]

        env._wait_for_readiness = _noop_readiness  # type: ignore[assignment]
        return env

    def _run_action(self, env: MacOSDesktopEnvironment, action: Action) -> Any:
        return asyncio.run(env.execute_action(action))

    def test_unknown_intent_returns_error(self) -> None:
        env = self._make_env()
        action = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "nonexistent_intent"},
        )
        result = self._run_action(env, action)
        assert result.summary.startswith("error")
        assert "Unknown semantic intent" in result.message

    def test_save_document_via_osascript(self) -> None:
        """save_document should try osascript first and succeed when it returns 0."""
        env = self._make_env()
        action = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "save_document"},
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch("subprocess.run", return_value=mock_proc) as mock_run,
            patch(
                "harness.environments.macos._get_window_info",
                return_value={
                    "focused_app": "TextEdit",
                    "focused_window_title": "Untitled",
                },
            ),
        ):
            result = self._run_action(env, action)

        assert result.summary == "ok"
        assert result.execution_method == ExecutionMethod.SHELL
        assert result.metadata["intent"] == "save_document"
        assert result.metadata["transport"] == "osascript"
        # osascript was called with the save command
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "osascript"
        assert "save document 1" in call_args[-1]

    @patch("pyautogui.hotkey")
    def test_save_document_falls_back_to_keyboard(self, mock_hotkey: MagicMock) -> None:
        """When osascript fails, should fall back to CMD+S."""
        env = self._make_env()
        action = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "save_document"},
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 1  # osascript fails

        with (
            patch("subprocess.run", return_value=mock_proc),
            patch(
                "harness.environments.macos._get_window_info",
                return_value={
                    "focused_app": "TextEdit",
                    "focused_window_title": "Untitled",
                },
            ),
        ):
            result = self._run_action(env, action)

        assert result.summary == "ok"
        assert result.execution_method == ExecutionMethod.KEYBOARD
        assert result.metadata["transport"] == "keyboard"
        mock_hotkey.assert_called_once_with("command", "s")

    @patch("pyautogui.hotkey")
    def test_save_keyboard_only_when_no_app(self, mock_hotkey: MagicMock) -> None:
        """When no focused app, skip osascript and go straight to keyboard."""
        env = self._make_env()
        action = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "save_document"},
        )

        with patch(
            "harness.environments.macos._get_window_info",
            return_value={"focused_app": "", "focused_window_title": "Untitled"},
        ):
            result = self._run_action(env, action)

        assert result.summary == "ok"
        assert result.execution_method == ExecutionMethod.KEYBOARD
        mock_hotkey.assert_called_once_with("command", "s")

    def test_new_document_via_osascript(self) -> None:
        env = self._make_env()
        action = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "new_document"},
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch("subprocess.run", return_value=mock_proc),
            patch(
                "harness.environments.macos._get_window_info",
                return_value={
                    "focused_app": "TextEdit",
                    "focused_window_title": "report.txt",
                },
            ),
        ):
            result = self._run_action(env, action)

        assert result.summary == "ok"
        assert result.metadata["intent"] == "new_document"

    def test_close_window_via_osascript(self) -> None:
        env = self._make_env()
        action = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "close_window"},
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch("subprocess.run", return_value=mock_proc),
            patch(
                "harness.environments.macos._get_window_info",
                return_value={
                    "focused_app": "TextEdit",
                    "focused_window_title": "Untitled",
                },
            ),
        ):
            result = self._run_action(env, action)

        assert result.summary == "ok"
        assert result.metadata["intent"] == "close_window"


# ---------------------------------------------------------------------------
# expected_change_observed wiring
# ---------------------------------------------------------------------------


class TestExpectedChangeObserved:
    def _make_env(self) -> MacOSDesktopEnvironment:
        env = MacOSDesktopEnvironment()

        async def _noop_readiness(_pre_ids: Any) -> None:
            return None  # type: ignore[return-value]

        env._wait_for_readiness = _noop_readiness  # type: ignore[assignment]
        return env

    def test_save_populates_expected_change_on_title_change(self) -> None:
        """save_document should set expected_change_observed=True when title changes."""
        env = self._make_env()
        action = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "save_document"},
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        call_count = 0

        def mock_window_info() -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"focused_app": "TextEdit", "focused_window_title": "Untitled"}
            return {"focused_app": "TextEdit", "focused_window_title": "report.txt"}

        with (
            patch("subprocess.run", return_value=mock_proc),
            patch("harness.environments.macos._get_window_info", side_effect=mock_window_info),
        ):
            result = asyncio.run(env.execute_action(action))

        assert result.expected_change_observed is True

    def test_save_expected_change_none_when_uncertain(self) -> None:
        """save_document should set expected_change_observed=None when no strong signal."""
        env = self._make_env()
        action = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "save_document"},
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch("subprocess.run", return_value=mock_proc),
            patch(
                "harness.environments.macos._get_window_info",
                return_value={
                    "focused_app": "TextEdit",
                    "focused_window_title": "Untitled",
                },
            ),
        ):
            result = asyncio.run(env.execute_action(action))

        # state_changed is None (from noop readiness), title unchanged → None
        assert result.expected_change_observed is None

    def test_legacy_press_keys_no_expected_change(self) -> None:
        """Legacy press_keys should not populate expected_change_observed."""
        env = self._make_env()
        action = Action(action_type=ActionType.PRESS, params={"key": "command+s"})

        with patch("pyautogui.hotkey"):
            result = asyncio.run(env.execute_action(action))

        assert result.expected_change_observed is None

    def test_ok_helper_accepts_expected_change(self) -> None:
        """ok() helper now supports expected_change_observed parameter."""
        result = ok(
            method=ExecutionMethod.KEYBOARD,
            state_changed=True,
            expected_change_observed=True,
            metadata={"intent": "save_document"},
        )
        assert result.expected_change_observed is True
        assert result.state_changed is True


# ---------------------------------------------------------------------------
# Fallback behavior
# ---------------------------------------------------------------------------


class TestTransportFallback:
    def _make_env(self) -> MacOSDesktopEnvironment:
        env = MacOSDesktopEnvironment()

        async def _readiness_true(_pre_ids: Any) -> bool:
            return True

        env._wait_for_readiness = _readiness_true  # type: ignore[assignment]
        return env

    @patch("pyautogui.hotkey")
    def test_osascript_timeout_falls_back(self, mock_hotkey: MagicMock) -> None:
        """If osascript times out, should fall back to keyboard."""
        env = self._make_env()
        action = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "save_document"},
        )

        import subprocess

        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 10)),
            patch(
                "harness.environments.macos._get_window_info",
                return_value={
                    "focused_app": "TextEdit",
                    "focused_window_title": "Untitled",
                },
            ),
        ):
            result = asyncio.run(env.execute_action(action))

        assert result.summary == "ok"
        assert result.metadata["transport"] == "keyboard"
        mock_hotkey.assert_called_once_with("command", "s")

    @patch("pyautogui.hotkey")
    def test_osascript_oserror_falls_back(self, mock_hotkey: MagicMock) -> None:
        """If osascript raises OSError, should fall back to keyboard."""
        env = self._make_env()
        action = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "close_window"},
        )

        with (
            patch("subprocess.run", side_effect=OSError("No such file")),
            patch(
                "harness.environments.macos._get_window_info",
                return_value={
                    "focused_app": "TextEdit",
                    "focused_window_title": "Doc1",
                },
            ),
        ):
            result = asyncio.run(env.execute_action(action))

        assert result.summary == "ok"
        assert result.metadata["transport"] == "keyboard"
        mock_hotkey.assert_called_once_with("command", "w")


# ---------------------------------------------------------------------------
# Stagnation detection with semantic intents
# ---------------------------------------------------------------------------


class TestStagnationSignature:
    def test_semantic_intent_signature_includes_intent(self) -> None:
        """Action signature for stagnation should differentiate intents."""
        from harness.runner import _action_signature

        save = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "save_document"},
        )
        close = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "close_window"},
        )
        assert _action_signature(save) != _action_signature(close)

    def test_semantic_intent_vs_press_keys_signature(self) -> None:
        """Semantic intent and press_keys should have different signatures."""
        from harness.runner import _action_signature

        intent = Action(
            action_type=ActionType.SEMANTIC_INTENT,
            params={"intent": "save_document"},
        )
        press = Action(
            action_type=ActionType.PRESS,
            params={"key": "command+s"},
        )
        assert _action_signature(intent) != _action_signature(press)
