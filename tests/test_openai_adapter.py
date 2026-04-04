"""Unit tests for the OpenAI computer-use adapter (mocked API)."""

from __future__ import annotations

import base64
import os
from types import SimpleNamespace
from typing import Any
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

# A small 1x1 transparent PNG for test screenshots
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQABNl7BcQAAAABJRU5ErkJggg=="
)

_TASK = Task(
    task_id="test-task",
    version="1.0",
    goal=TaskGoal(description="Click the button"),
    verification=TaskVerification(
        primary=VerificationCheck(method="programmatic", check="file_exists('out.txt')")
    ),
)


def _make_observation(screenshot: bytes = _TINY_PNG) -> Observation:
    return Observation(
        observation_type=ObservationType.SCREENSHOT,
        screenshot=screenshot,
        url="http://localhost:8765",
        page_title="Test Page",
    )


def _mock_response(
    response_id: str = "resp_001",
    computer_call: Any | None = None,
    text: str | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> SimpleNamespace:
    """Build a mock OpenAI Responses API response."""
    output_items: list[Any] = []

    if computer_call is not None:
        output_items.append(computer_call)

    if text is not None:
        msg_content = SimpleNamespace(type="output_text", text=text)
        output_items.append(SimpleNamespace(type="message", content=[msg_content]))

    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )

    return SimpleNamespace(id=response_id, output=output_items, usage=usage)


def _make_computer_call(
    call_id: str = "call_001",
    actions: list[Any] | None = None,
    pending_safety_checks: list[Any] | None = None,
) -> SimpleNamespace:
    """Build a mock computer_call output item."""
    if actions is None:
        actions = [SimpleNamespace(type="click", x=100, y=200, button="left")]
    return SimpleNamespace(
        type="computer_call",
        call_id=call_id,
        actions=actions,
        pending_safety_checks=pending_safety_checks,
        status="completed",
    )


@pytest.fixture()
def adapter():
    """Create an adapter with mocked OpenAI client."""
    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
        from harness.adapters.openai_cu import OpenAIComputerUseAdapter

        a = OpenAIComputerUseAdapter()
        a._client = MagicMock()
        return a


class TestObservationRequest:
    def test_requests_screenshot(self, adapter):
        assert adapter.observation_request() == ObservationType.SCREENSHOT


class TestDecideClickAction:
    def test_maps_click_action(self, adapter):
        click_action = SimpleNamespace(type="click", x=100, y=200, button="left")
        cc = _make_computer_call(actions=[click_action])
        adapter._client.responses.create.return_value = _mock_response(computer_call=cc)

        actions = adapter.decide(_make_observation(), _TASK)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.CLICK
        assert actions[0].params["x"] == 100
        assert actions[0].params["y"] == 200

    def test_right_click_includes_button(self, adapter):
        click_action = SimpleNamespace(type="click", x=50, y=60, button="right")
        cc = _make_computer_call(actions=[click_action])
        adapter._client.responses.create.return_value = _mock_response(computer_call=cc)

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].params["button"] == "right"


class TestDecideTypeAction:
    def test_maps_type_action(self, adapter):
        type_action = SimpleNamespace(type="type", text="hello world")
        cc = _make_computer_call(actions=[type_action])
        adapter._client.responses.create.return_value = _mock_response(computer_call=cc)

        actions = adapter.decide(_make_observation(), _TASK)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.TYPE
        assert actions[0].params["text"] == "hello world"


class TestDecideKeypressAction:
    def test_maps_keypress_with_combo(self, adapter):
        key_action = SimpleNamespace(type="keypress", keys=["ctrl", "a"])
        cc = _make_computer_call(actions=[key_action])
        adapter._client.responses.create.return_value = _mock_response(computer_call=cc)

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.PRESS
        assert actions[0].params["key"] == "ctrl+a"


class TestDecideScrollAction:
    def test_maps_scroll_action(self, adapter):
        scroll_action = SimpleNamespace(type="scroll", x=640, y=360, scroll_x=0, scroll_y=3)
        cc = _make_computer_call(actions=[scroll_action])
        adapter._client.responses.create.return_value = _mock_response(computer_call=cc)

        actions = adapter.decide(_make_observation(), _TASK)

        assert actions[0].action_type == ActionType.SCROLL
        assert actions[0].params["delta_y"] == 3


class TestDecideBatchedActions:
    def test_multiple_actions_in_one_call(self, adapter):
        openai_actions = [
            SimpleNamespace(type="click", x=100, y=200, button="left"),
            SimpleNamespace(type="type", text="test input"),
            SimpleNamespace(type="click", x=300, y=400, button="left"),
        ]
        cc = _make_computer_call(actions=openai_actions)
        adapter._client.responses.create.return_value = _mock_response(computer_call=cc)

        actions = adapter.decide(_make_observation(), _TASK)

        assert len(actions) == 3
        assert actions[0].action_type == ActionType.CLICK
        assert actions[1].action_type == ActionType.TYPE
        assert actions[2].action_type == ActionType.CLICK


class TestDecideScreenshotAction:
    def test_screenshot_action_is_skipped(self, adapter):
        openai_actions = [
            SimpleNamespace(type="click", x=100, y=200, button="left"),
            SimpleNamespace(type="screenshot"),
        ]
        cc = _make_computer_call(actions=openai_actions)
        adapter._client.responses.create.return_value = _mock_response(computer_call=cc)

        actions = adapter.decide(_make_observation(), _TASK)

        # Screenshot is skipped, only click remains
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.CLICK

    def test_only_screenshot_action_returns_empty(self, adapter):
        openai_actions = [SimpleNamespace(type="screenshot")]
        cc = _make_computer_call(actions=openai_actions)
        adapter._client.responses.create.return_value = _mock_response(computer_call=cc)

        actions = adapter.decide(_make_observation(), _TASK)

        # Screenshot-only: return empty list so the runner loops and takes a fresh screenshot
        assert len(actions) == 0


class TestDecideDone:
    def test_no_computer_call_returns_done(self, adapter):
        response = _mock_response(text="Task completed successfully.")
        adapter._client.responses.create.return_value = response

        actions = adapter.decide(_make_observation(), _TASK)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.DONE


class TestDecideFail:
    def test_failure_text_returns_fail(self, adapter):
        response = _mock_response(text="Sorry, I am unable to complete this task.")
        adapter._client.responses.create.return_value = response

        actions = adapter.decide(_make_observation(), _TASK)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.FAIL
        assert "unable" in actions[0].params["reason"].lower()

    def test_no_screenshot_returns_fail(self, adapter):
        obs = Observation(observation_type=ObservationType.SCREENSHOT, screenshot=None)
        actions = adapter.decide(obs, _TASK)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.FAIL


class TestSafetyChecks:
    def test_acknowledges_safety_checks(self, adapter):
        safety_check = SimpleNamespace(
            id="sc_001", code="malicious_instructions", message="Warning!"
        )
        cc = _make_computer_call(
            call_id="call_safe",
            pending_safety_checks=[safety_check],
        )
        # First call returns safety check
        first_response = _mock_response(response_id="resp_1", computer_call=cc)
        # Second call after acknowledgment
        cc2 = _make_computer_call(call_id="call_002")
        second_response = _mock_response(response_id="resp_2", computer_call=cc2)

        adapter._client.responses.create.side_effect = [first_response, second_response]

        # First decide — triggers safety check
        actions1 = adapter.decide(_make_observation(), _TASK)
        assert len(actions1) >= 1

        # Second decide — should include acknowledged_safety_checks in API call
        actions2 = adapter.decide(_make_observation(), _TASK)
        assert len(actions2) >= 1

        # Verify second call included acknowledged_safety_checks
        second_call_args = adapter._client.responses.create.call_args_list[1]
        input_items = second_call_args.kwargs.get("input", [])
        assert len(input_items) == 1
        assert "acknowledged_safety_checks" in input_items[0]
        acks = input_items[0]["acknowledged_safety_checks"]
        assert acks[0]["id"] == "sc_001"


class TestCostMetadata:
    def test_accumulates_tokens(self, adapter):
        cc = _make_computer_call()
        resp1 = _mock_response(input_tokens=100, output_tokens=50, computer_call=cc)
        resp2 = _mock_response(
            response_id="resp_2", input_tokens=200, output_tokens=80, computer_call=cc
        )
        adapter._client.responses.create.side_effect = [resp1, resp2]

        adapter.decide(_make_observation(), _TASK)
        adapter.decide(_make_observation(), _TASK)

        meta = adapter.get_cost_metadata()
        assert meta["input_tokens"] == 300
        assert meta["output_tokens"] == 130
        assert meta["total_tokens"] == 430
        assert meta["api_calls"] == 2
        assert meta["model"] == "computer-use-preview"
        assert meta["estimated_cost_usd"] > 0

    def test_cost_formula(self, adapter):
        cc = _make_computer_call()
        resp = _mock_response(input_tokens=1_000_000, output_tokens=1_000_000, computer_call=cc)
        adapter._client.responses.create.return_value = resp

        adapter.decide(_make_observation(), _TASK)

        meta = adapter.get_cost_metadata()
        # 1M input * $3 + 1M output * $12 = $15
        assert meta["estimated_cost_usd"] == pytest.approx(15.0)


class TestReset:
    def test_reset_clears_state(self, adapter):
        cc = _make_computer_call()
        resp = _mock_response(input_tokens=500, output_tokens=200, computer_call=cc)
        adapter._client.responses.create.return_value = resp

        adapter.decide(_make_observation(), _TASK)
        assert adapter._api_calls == 1

        adapter.reset()

        assert adapter._previous_response_id is None
        assert adapter._last_call_id is None
        assert adapter._pending_safety_checks == []
        assert adapter._input_tokens == 0
        assert adapter._output_tokens == 0
        assert adapter._api_calls == 0
        assert adapter.get_cost_metadata()["total_tokens"] == 0


class TestFirstVsContinuationCall:
    def test_first_call_sends_user_message(self, adapter):
        cc = _make_computer_call()
        resp = _mock_response(computer_call=cc)
        adapter._client.responses.create.return_value = resp

        adapter.decide(_make_observation(), _TASK)

        call_args = adapter._client.responses.create.call_args
        assert call_args.kwargs.get("previous_response_id") is None
        input_msg = call_args.kwargs["input"][0]
        assert input_msg["role"] == "user"
        # Should contain the screenshot as base64
        image_content = [c for c in input_msg["content"] if c["type"] == "input_image"]
        assert len(image_content) == 1
        assert image_content[0]["image_url"].startswith("data:image/png;base64,")

    def test_continuation_sends_computer_call_output(self, adapter):
        cc = _make_computer_call(call_id="call_first")
        resp1 = _mock_response(response_id="resp_1", computer_call=cc)
        cc2 = _make_computer_call(call_id="call_second")
        resp2 = _mock_response(response_id="resp_2", computer_call=cc2)
        adapter._client.responses.create.side_effect = [resp1, resp2]

        adapter.decide(_make_observation(), _TASK)
        adapter.decide(_make_observation(), _TASK)

        second_call = adapter._client.responses.create.call_args_list[1]
        assert second_call.kwargs["previous_response_id"] == "resp_1"
        input_item = second_call.kwargs["input"][0]
        assert input_item["type"] == "computer_call_output"
        assert input_item["call_id"] == "call_first"


class TestMissingApiKey:
    def test_raises_without_api_key(self):
        env_patch = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict("os.environ", env_patch, clear=True):
            from harness.adapters.openai_cu import OpenAIComputerUseAdapter

            with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
                OpenAIComputerUseAdapter()
