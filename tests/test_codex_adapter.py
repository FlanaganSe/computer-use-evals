"""Unit tests for the Codex subscription adapter (mocked subprocess)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from harness.types import (
    ActionType,
    Observation,
    ObservationType,
    Task,
    TaskGoal,
    TaskVerification,
    VerificationCheck,
)

_TASK = Task(
    task_id="test-task",
    version="1.0",
    goal=TaskGoal(description="Click the download link"),
    verification=TaskVerification(
        primary=VerificationCheck(method="programmatic", check="file_exists('test.pdf')")
    ),
)


def _make_observation(
    aria: str = "- link 'Download'",
    url: str = "http://localhost:8765",
) -> Observation:
    return Observation(
        observation_type=ObservationType.ARIA_STATE,
        aria_snapshot=aria,
        url=url,
        page_title="Test Page",
    )


@pytest.fixture()
def adapter():
    """Create adapter with mocked codex CLI availability."""
    with patch("shutil.which", return_value="/usr/local/bin/codex"):
        from harness.adapters.codex_subscription import CodexSubscriptionAdapter

        return CodexSubscriptionAdapter()


def _mock_codex_output(adapter, output_text: str) -> None:
    """Patch _invoke_codex to return the given text."""
    adapter._invoke_codex = MagicMock(return_value=output_text)


class TestObservationRequest:
    def test_requests_aria_state(self, adapter):
        assert adapter.observation_request() == ObservationType.ARIA_STATE


class TestDecideGotoAction:
    def test_maps_goto_action(self, adapter):
        _mock_codex_output(adapter, '{"action": "goto", "url": "http://localhost:8765"}')

        actions = adapter.decide(_make_observation(), _TASK)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.GOTO
        assert actions[0].params["url"] == "http://localhost:8765"


class TestDecideClickFromCleanJson:
    def test_maps_click_action(self, adapter):
        _mock_codex_output(adapter, '{"action": "click", "selector": "#download-link"}')

        actions = adapter.decide(_make_observation(), _TASK)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.CLICK
        assert actions[0].params["selector"] == "#download-link"

    def test_text_selector(self, adapter):
        _mock_codex_output(adapter, '{"action": "click", "selector": "text=Download Test PDF"}')

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.CLICK
        assert actions[0].params["selector"] == "text=Download Test PDF"


class TestDecideTypeAction:
    def test_maps_type_action(self, adapter):
        _mock_codex_output(adapter, '{"action": "type", "selector": "#name", "text": "Jane Doe"}')

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.TYPE
        assert actions[0].params["selector"] == "#name"
        assert actions[0].params["text"] == "Jane Doe"


class TestDecidePressAction:
    def test_maps_press_action(self, adapter):
        _mock_codex_output(adapter, '{"action": "press", "key": "Enter"}')

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.PRESS
        assert actions[0].params["key"] == "Enter"


class TestDecideWaitAction:
    def test_maps_wait_action(self, adapter):
        _mock_codex_output(adapter, '{"action": "wait", "ms": 2000}')

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.WAIT
        assert actions[0].params["ms"] == 2000


class TestDecideDone:
    def test_maps_done_action(self, adapter):
        _mock_codex_output(adapter, '{"action": "done"}')

        actions = adapter.decide(_make_observation(), _TASK)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.DONE


class TestDecideFail:
    def test_maps_fail_action(self, adapter):
        _mock_codex_output(adapter, '{"action": "fail", "reason": "Cannot find element"}')

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.FAIL
        assert "Cannot find element" in actions[0].params["reason"]

    def test_unknown_action_returns_fail(self, adapter):
        _mock_codex_output(adapter, '{"action": "hover", "selector": "#btn"}')

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.FAIL
        assert "Unknown action" in actions[0].params["reason"]

    def test_malformed_click_missing_selector(self, adapter):
        _mock_codex_output(adapter, '{"action": "click"}')

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.FAIL
        assert "missing key" in actions[0].params["reason"].lower()


class TestNoAriaSnapshot:
    def test_missing_aria_returns_fail(self, adapter):
        obs = Observation(observation_type=ObservationType.ARIA_STATE, aria_snapshot=None)
        actions = adapter.decide(obs, _TASK)

        assert actions[0].action_type == ActionType.FAIL
        assert "No ARIA snapshot" in actions[0].params["reason"]


class TestJsonExtraction:
    def test_extracts_from_markdown_code_block(self, adapter):
        _mock_codex_output(
            adapter,
            'Here is the action:\n```json\n{"action": "click", "selector": "#link"}\n```',
        )

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.CLICK
        assert actions[0].params["selector"] == "#link"

    def test_extracts_from_surrounding_text(self, adapter):
        _mock_codex_output(
            adapter,
            'I will click the link. {"action": "click", "selector": "#btn"} That should work.',
        )

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.CLICK
        assert actions[0].params["selector"] == "#btn"

    def test_unparseable_output_returns_fail(self, adapter):
        _mock_codex_output(adapter, "I cannot determine the correct action to take.")

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.FAIL
        assert "Could not parse" in actions[0].params["reason"]


class TestCodexCliFailure:
    def test_cli_error_returns_fail(self, adapter):
        adapter._invoke_codex = MagicMock(side_effect=RuntimeError("Codex CLI exited 1"))

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.FAIL
        assert "Codex CLI error" in actions[0].params["reason"]

    def test_cli_timeout_returns_fail(self, adapter):
        import subprocess

        adapter._invoke_codex = MagicMock(
            side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=120)
        )

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.FAIL
        assert "Codex CLI error" in actions[0].params["reason"]


class TestCostMetadata:
    def test_tracks_invocations_and_latency(self, adapter):
        _mock_codex_output(adapter, '{"action": "click", "selector": "#a"}')

        adapter.decide(_make_observation(), _TASK)
        adapter.decide(_make_observation(), _TASK)

        meta = adapter.get_cost_metadata()
        assert meta["invocations"] == 2
        assert meta["total_latency_ms"] >= 0
        assert meta["model"] == "codex-subscription"
        assert meta["billing"] == "subscription"
        assert meta["estimated_cost_usd"] == 0.0

    def test_metadata_after_no_calls(self, adapter):
        meta = adapter.get_cost_metadata()
        assert meta["invocations"] == 0
        assert meta["total_latency_ms"] == 0


class TestReset:
    def test_reset_clears_state(self, adapter):
        _mock_codex_output(adapter, '{"action": "click", "selector": "#a"}')
        adapter.decide(_make_observation(), _TASK)
        assert adapter._invocation_count == 1

        adapter.reset()

        assert adapter._invocation_count == 0
        assert adapter._total_latency_ms == 0
        assert adapter._action_history == []
        assert adapter.get_cost_metadata()["invocations"] == 0


class TestActionHistory:
    def test_history_grows_with_decisions(self, adapter):
        _mock_codex_output(adapter, '{"action": "click", "selector": "#a"}')
        adapter.decide(_make_observation(), _TASK)

        assert len(adapter._action_history) == 1
        assert "click" in adapter._action_history[0]

    def test_history_included_in_prompt(self, adapter):
        _mock_codex_output(adapter, '{"action": "click", "selector": "#a"}')
        adapter.decide(_make_observation(), _TASK)

        # Second call — prompt should include history
        _mock_codex_output(adapter, '{"action": "done"}')
        adapter.decide(_make_observation(), _TASK)

        prompt = adapter._build_prompt(_make_observation(), _TASK)
        assert "click" in prompt
        assert "(none yet)" not in prompt


class TestPromptContent:
    def test_prompt_includes_task_and_aria(self, adapter):
        obs = _make_observation(
            aria='- heading "My Page" [level=1]\n- link "Go"',
            url="http://localhost:9999",
        )
        prompt = adapter._build_prompt(obs, _TASK)

        assert "Click the download link" in prompt
        assert "http://localhost:9999" in prompt
        assert '- heading "My Page"' in prompt
        assert '- link "Go"' in prompt
        assert "(none yet)" in prompt


class TestMissingCodexCli:
    def test_raises_without_codex(self):
        with patch("shutil.which", return_value=None):
            from harness.adapters.codex_subscription import CodexSubscriptionAdapter

            with pytest.raises(RuntimeError, match="Codex CLI not found"):
                CodexSubscriptionAdapter()
