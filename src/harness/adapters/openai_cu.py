"""OpenAI computer-use-preview adapter via the Responses API."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

from openai import OpenAI

from harness.types import Action, ActionType, Observation, ObservationType, Task

logger = logging.getLogger(__name__)

_MODEL = "computer-use-preview"
_DISPLAY_WIDTH = 1280
_DISPLAY_HEIGHT = 720

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "computer_use_preview",
        "display_width": _DISPLAY_WIDTH,
        "display_height": _DISPLAY_HEIGHT,
        "environment": "browser",
    }
]

# Mapping from OpenAI action type → our ActionType
_ACTION_MAP: dict[str, ActionType] = {
    "click": ActionType.CLICK,
    "double_click": ActionType.DOUBLE_CLICK,
    "type": ActionType.TYPE,
    "keypress": ActionType.PRESS,
    "scroll": ActionType.SCROLL,
    "wait": ActionType.WAIT,
    "drag": ActionType.DRAG,
    "move": ActionType.MOVE,
    "screenshot": ActionType.SCREENSHOT,
}

# Pricing per 1M tokens (standard API, April 2026)
_INPUT_PRICE_PER_M = 3.00
_OUTPUT_PRICE_PER_M = 12.00


class OpenAIComputerUseAdapter:
    """OpenAI computer-use-preview adapter via the Responses API.

    Uses screenshot-only observation by default. When ``hybrid=True``,
    requests both screenshot and ARIA state and includes the accessibility
    tree as text context in the API call.

    Manages conversation continuity via ``previous_response_id``
    (server-side history).
    """

    def __init__(self, *, hybrid: bool = False) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            msg = "OPENAI_API_KEY environment variable is required for the openai_cu adapter"
            raise RuntimeError(msg)

        self._client = OpenAI(api_key=api_key)
        self._hybrid = hybrid
        self._previous_response_id: str | None = None
        self._last_call_id: str | None = None
        self._pending_safety_checks: list[dict[str, str]] = []
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._api_calls: int = 0

    @property
    def name(self) -> str:
        return "openai_cu_hybrid" if self._hybrid else "openai_cu"

    def observation_request(self) -> ObservationType:
        return ObservationType.SCREENSHOT_AND_ARIA if self._hybrid else ObservationType.SCREENSHOT

    def decide(self, observation: Observation, task: Task) -> list[Action]:
        screenshot = observation.screenshot
        if screenshot is None:
            return [
                Action(
                    action_type=ActionType.FAIL,
                    params={"reason": "No screenshot provided"},
                )
            ]

        screenshot_b64 = base64.b64encode(screenshot).decode("utf-8")
        aria_text = observation.aria_snapshot if self._hybrid else None
        response = self._call_api(screenshot_b64, task, aria_text=aria_text)

        # Accumulate usage
        if hasattr(response, "usage") and response.usage is not None:
            self._input_tokens += response.usage.input_tokens
            self._output_tokens += response.usage.output_tokens

        self._previous_response_id = response.id
        self._api_calls += 1

        # Find computer_call in output
        computer_call = None
        text_output: list[str] = []
        for item in response.output:
            if item.type == "computer_call":
                computer_call = item
            elif item.type == "message":
                for content in item.content:
                    if hasattr(content, "text"):
                        text_output.append(content.text)

        if computer_call is None:
            # Model is done — no more actions requested
            combined_text = " ".join(text_output)
            if any(
                kw in combined_text.lower()
                for kw in ["cannot", "unable", "failed", "error", "sorry"]
            ):
                return [
                    Action(
                        action_type=ActionType.FAIL,
                        params={"reason": combined_text[:500]},
                    )
                ]
            return [Action(action_type=ActionType.DONE)]

        # Store call_id for the next continuation call
        self._last_call_id = computer_call.call_id

        # Handle safety checks
        pending = getattr(computer_call, "pending_safety_checks", None)
        if pending:
            self._pending_safety_checks = [
                {"id": sc.id, "code": sc.code, "message": sc.message} for sc in pending
            ]
            for sc in pending:
                logger.warning(
                    "Safety check [%s]: %s — %s",
                    sc.code,
                    sc.id,
                    sc.message,
                )
        else:
            self._pending_safety_checks = []

        # Map actions
        return _map_actions(computer_call.actions)

    def reset(self) -> None:
        self._previous_response_id = None
        self._last_call_id = None
        self._pending_safety_checks = []
        self._input_tokens = 0
        self._output_tokens = 0
        self._api_calls = 0

    def get_cost_metadata(self) -> dict[str, Any]:
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "total_tokens": self._input_tokens + self._output_tokens,
            "estimated_cost_usd": self._estimate_cost(),
            "model": _MODEL,
            "api_calls": self._api_calls,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _estimate_cost(self) -> float:
        return (self._input_tokens / 1_000_000) * _INPUT_PRICE_PER_M + (
            self._output_tokens / 1_000_000
        ) * _OUTPUT_PRICE_PER_M

    def _call_api(self, screenshot_b64: str, task: Task, *, aria_text: str | None = None) -> Any:
        tools: Any = _TOOLS

        if self._previous_response_id is None:
            # First call: send task description + optional ARIA + screenshot
            content: list[dict[str, Any]] = [
                {"type": "input_text", "text": task.goal.description},
            ]
            if aria_text:
                content.append(
                    {"type": "input_text", "text": f"Page accessibility tree:\n{aria_text}"}
                )
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{screenshot_b64}",
                }
            )
            input_msg: Any = [{"role": "user", "content": content}]
            return self._client.responses.create(
                model=_MODEL,
                tools=tools,
                input=input_msg,
                truncation="auto",
            )

        # Continuation: send computer_call_output with new screenshot
        input_item: dict[str, Any] = {
            "call_id": self._last_call_id,
            "type": "computer_call_output",
            "output": {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{screenshot_b64}",
            },
        }

        if self._pending_safety_checks:
            input_item["acknowledged_safety_checks"] = self._pending_safety_checks
            self._pending_safety_checks = []

        continuation_input: list[Any] = [input_item]
        if aria_text:
            # NOTE: verify that the Responses API accepts mixed input types
            # (computer_call_output + user message) in Phase 2 live testing.
            # If not, fall back to including ARIA only in the first call.
            continuation_input.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Page accessibility tree:\n{aria_text}",
                        }
                    ],
                }
            )
        return self._client.responses.create(
            model=_MODEL,
            previous_response_id=self._previous_response_id,
            tools=tools,
            input=continuation_input,
            truncation="auto",
        )


def _map_actions(openai_actions: list[Any]) -> list[Action]:
    """Convert OpenAI computer_call actions to harness Actions."""
    result: list[Action] = []
    for raw in openai_actions:
        action_type_str = raw.type if hasattr(raw, "type") else raw.get("type")
        our_type = _ACTION_MAP.get(action_type_str)

        if our_type is None:
            logger.warning("Unknown OpenAI action type: %s", action_type_str)
            continue

        # Skip screenshot actions — the runner loop handles fresh screenshots
        if our_type == ActionType.SCREENSHOT:
            continue

        params = _extract_params(action_type_str, raw)
        result.append(Action(action_type=our_type, params=params))

    return result


def _extract_params(action_type: str, raw: Any) -> dict[str, Any]:
    """Extract parameters from an OpenAI action into our flat params dict."""

    # Support both attribute access (SDK objects) and dict access
    def _get(key: str, default: Any = None) -> Any:
        if hasattr(raw, key):
            return getattr(raw, key, default)
        if isinstance(raw, dict):
            return raw.get(key, default)
        return default

    match action_type:
        case "click":
            params: dict[str, Any] = {"x": _get("x"), "y": _get("y")}
            button = _get("button")
            if button and button != "left":
                params["button"] = button
            return params

        case "double_click":
            return {"x": _get("x"), "y": _get("y")}

        case "type":
            return {"text": _get("text", "")}

        case "keypress":
            keys = _get("keys", [])
            # Join multiple keys with + for Playwright's press()
            return {"key": "+".join(keys) if isinstance(keys, list) else str(keys)}

        case "scroll":
            return {
                "x": _get("x", 0),
                "y": _get("y", 0),
                "delta_x": _get("scroll_x", 0),
                "delta_y": _get("scroll_y", 0),
            }

        case "wait":
            return {"ms": _get("ms", 1000)}

        case "drag":
            path = _get("path", [])
            if len(path) >= 2:
                start = path[0]
                end = path[-1]
                sx = start.get("x", 0) if isinstance(start, dict) else getattr(start, "x", 0)
                sy = start.get("y", 0) if isinstance(start, dict) else getattr(start, "y", 0)
                ex = end.get("x", 0) if isinstance(end, dict) else getattr(end, "x", 0)
                ey = end.get("y", 0) if isinstance(end, dict) else getattr(end, "y", 0)
                return {
                    "start_x": sx,
                    "start_y": sy,
                    "end_x": ex,
                    "end_y": ey,
                }
            return {}

        case "move":
            return {"x": _get("x"), "y": _get("y")}

        case _:
            return {}
