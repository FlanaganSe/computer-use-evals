"""Tests for runner → adapter result feedback (Milestone 2).

Verifies that the runner passes actual action outcomes back to adapters
via notify_result, and that the structured-state adapter updates its
action history accordingly.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from harness.runtime_results import ExecutionMethod, ResultStatus, RuntimeResult, done, fail, ok
from harness.types import Action, ActionType, Observation, ObservationType, Task


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


def _make_adapter():
    """Create adapter with mocked OpenAI client."""
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
# notify_result updates action history
# ---------------------------------------------------------------------------


class TestNotifyResult:
    def test_updates_last_history_entry(self) -> None:
        adapter = _make_adapter()
        adapter._action_history = [
            {"action": "click", "target": "ax_123", "result": "pending"},
        ]

        action = Action(action_type=ActionType.CLICK, params={"x": 100, "y": 200})
        result = ok(method=ExecutionMethod.COORDINATES)
        adapter.notify_result(action, result)

        assert adapter._action_history[-1]["result"] == "ok"

    def test_updates_to_error(self) -> None:
        adapter = _make_adapter()
        adapter._action_history = [
            {"action": "click", "target": "ax_456", "result": "pending"},
        ]

        action = Action(action_type=ActionType.CLICK, params={})
        result = RuntimeResult(
            status=ResultStatus.ERROR,
            message="click requires coordinates",
        )
        adapter.notify_result(action, result)

        assert adapter._action_history[-1]["result"] == "error:click requires coordinates"

    def test_updates_to_done(self) -> None:
        adapter = _make_adapter()
        adapter._action_history = [
            {"action": "done", "target": "", "result": "pending"},
        ]

        action = Action(action_type=ActionType.DONE)
        adapter.notify_result(action, done())

        assert adapter._action_history[-1]["result"] == "done"

    def test_updates_to_fail(self) -> None:
        adapter = _make_adapter()
        adapter._action_history = [
            {"action": "fail", "target": "", "result": "pending"},
        ]

        action = Action(action_type=ActionType.FAIL, params={"reason": "test"})
        adapter.notify_result(action, fail("test"))

        assert adapter._action_history[-1]["result"] == "fail:test"

    def test_noop_on_empty_history(self) -> None:
        """notify_result should not crash when history is empty."""
        adapter = _make_adapter()
        assert adapter._action_history == []

        action = Action(action_type=ActionType.CLICK, params={"x": 100, "y": 200})
        adapter.notify_result(action, ok())
        # Should not raise; history stays empty
        assert adapter._action_history == []


# ---------------------------------------------------------------------------
# Prompt shows real results after notify_result
# ---------------------------------------------------------------------------


class TestPromptReflectsResults:
    def test_prompt_shows_ok_not_pending(self) -> None:
        adapter = _make_adapter()
        adapter._action_history = [
            {"action": "click", "target": "ax_123", "result": "ok"},
        ]
        task = _make_task()

        prompt = adapter._build_prompt(
            task=task,
            focused_app="TextEdit",
            window_title="Untitled",
            elements_text='[ax_1] AXButton "Save"',
        )
        assert "ok" in prompt
        assert "pending" not in prompt

    def test_prompt_shows_error_not_pending(self) -> None:
        adapter = _make_adapter()
        adapter._action_history = [
            {"action": "click", "target": "ax_456", "result": "error"},
        ]
        task = _make_task()

        prompt = adapter._build_prompt(
            task=task,
            focused_app="TextEdit",
            window_title="Untitled",
            elements_text='[ax_1] AXButton "Save"',
        )
        assert "error" in prompt
        assert "pending" not in prompt

    def test_prompt_multi_step_history(self) -> None:
        """Multiple steps should all show real results."""
        adapter = _make_adapter()
        adapter._action_history = [
            {"action": "open_app", "target": "", "result": "ok"},
            {"action": "click", "target": "ax_1", "result": "ok"},
            {"action": "type_text", "target": "ax_2", "result": "ok"},
        ]
        task = _make_task()

        prompt = adapter._build_prompt(
            task=task,
            focused_app="TextEdit",
            window_title="Untitled",
            elements_text='[ax_1] AXButton "Save"',
        )
        assert prompt.count("pending") == 0
        assert prompt.count("→ ok") == 3


# ---------------------------------------------------------------------------
# Non-primary adapters accept notify_result without error
# ---------------------------------------------------------------------------


class TestNonPrimaryAdaptersAcceptNotify:
    def test_deterministic_noop(self) -> None:
        from harness.adapters.deterministic import DeterministicAdapter

        adapter = DeterministicAdapter()
        action = Action(action_type=ActionType.CLICK, params={"x": 100, "y": 200})
        adapter.notify_result(action, ok())  # should not raise

    def test_openai_cu_noop(self) -> None:
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}),
            patch("harness.adapters.openai_cu.OpenAI"),
        ):
            from harness.adapters.openai_cu import OpenAIComputerUseAdapter

            adapter = OpenAIComputerUseAdapter()
            action = Action(action_type=ActionType.CLICK, params={"x": 100, "y": 200})
            adapter.notify_result(action, ok())  # should not raise

    def test_codex_subscription_noop(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            from harness.adapters.codex_subscription import CodexSubscriptionAdapter

            adapter = CodexSubscriptionAdapter()
            action = Action(action_type=ActionType.CLICK, params={"selector": "#btn"})
            adapter.notify_result(action, ok())  # should not raise


# ---------------------------------------------------------------------------
# End-to-end: decide then notify shows real result in next prompt
# ---------------------------------------------------------------------------


class TestDecideThenNotify:
    def test_full_cycle(self) -> None:
        """Simulate: decide → notify → decide again, checking history is updated."""
        from harness.ax_state import build_ax_tree_from_dict

        adapter = _make_adapter()
        tree_data = {
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
        tree = build_ax_tree_from_dict(tree_data)
        assert tree is not None

        obs = Observation(
            observation_type=ObservationType.ARIA_STATE,
            aria_snapshot='AXApplication "TextEdit"',
            focused_app="TextEdit",
            page_title="Untitled",
            a11y_available=True,
        )
        obs._ax_tree = tree  # type: ignore[attr-defined]

        task = _make_task()

        # First decide: click the save button
        save_btn = tree.children[0].children[0]
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps({"action": "click", "target": save_btn.node_id})
                )
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        # History should have pending entry
        assert adapter._action_history[-1]["result"] == "pending"

        # Simulate runner calling notify_result
        adapter.notify_result(actions[0], ok(method=ExecutionMethod.COORDINATES))

        # History should now show "ok"
        assert adapter._action_history[-1]["result"] == "ok"

        # Second decide — capture the prompt to verify it contains "ok", not "pending"
        captured_prompts = []
        original_build = adapter._build_prompt

        def capture_prompt(**kwargs: Any) -> str:
            result = original_build(**kwargs)
            captured_prompts.append(result)
            return result

        adapter._build_prompt = capture_prompt  # type: ignore[method-assign]

        mock_response2 = MagicMock()
        mock_response2.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"action": "done"})))
        ]
        mock_response2.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response2):
            adapter.decide(obs, task)

        assert len(captured_prompts) == 1
        assert "→ ok" in captured_prompts[0]
        assert "pending" not in captured_prompts[0]
