"""Structured-state desktop adapter: AX tree → LLM → semantic actions.

Reads pruned accessibility state, sends it to an LLM as structured text,
and returns semantic actions with AX node targets and coordinate fallback.
Uses the existing Adapter protocol — no protocol changes.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

from harness.ax_state import AXNode, find_node_by_id, format_for_prompt, prune_interactive
from harness.types import Action, ActionType, Observation, ObservationType, Task

logger = logging.getLogger(__name__)

# Default models for structured-state planning.
# Using OpenAI to avoid a new dependency; the adapter is model-agnostic.
_DEFAULT_MODEL = "gpt-4.1"
_DEFAULT_CHEAP_MODEL = "gpt-4.1-mini"

# Pricing per 1M tokens (April 2026)
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
}
_DEFAULT_INPUT_PRICE = 2.00
_DEFAULT_OUTPUT_PRICE = 8.00

# Routing thresholds
_SPARSE_TREE_THRESHOLD = 3  # escalate if fewer interactive elements than this

# Maximum recent actions to include in prompt context.
_MAX_HISTORY = 5

# ActionType mapping from semantic action strings
_SEMANTIC_ACTION_MAP: dict[str, ActionType] = {
    "click": ActionType.CLICK,
    "double_click": ActionType.DOUBLE_CLICK,
    "type_text": ActionType.TYPE,
    "press_keys": ActionType.PRESS,
    "scroll": ActionType.SCROLL,
    "wait_for": ActionType.WAIT,
    "open_app": ActionType.SHELL,
    "focus_window": ActionType.SHELL,
    "select_menu_item": ActionType.CLICK,
    "set_value": ActionType.TYPE,
    "done": ActionType.DONE,
    "fail": ActionType.FAIL,
}


def _sanitize_app_name(name: str) -> str:
    """Sanitize an app name from LLM output to prevent injection.

    Strips quotes, newlines, and non-alphanumeric chars except spaces,
    hyphens, and dots (which are valid in macOS app names).
    """
    import re as _re

    # Remove quotes and newlines that could break AppleScript strings
    sanitized = name.replace('"', "").replace("'", "").replace("\n", "").replace("\r", "")
    # Only allow alphanumeric, spaces, hyphens, dots, and underscores
    sanitized = _re.sub(r"[^\w\s.\-]", "", sanitized)
    return sanitized.strip()[:100]  # Cap length


class StructuredStateDesktopAdapter:
    """AX-first desktop adapter that plans via structured accessibility state.

    Requests ARIA_STATE observations, prunes to interactive elements,
    formats a structured prompt, calls an LLM, and returns semantic
    actions that the macOS environment resolves to coordinates.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        cheap_model: str | None = None,
        routing_enabled: bool = False,
    ) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            msg = "OPENAI_API_KEY is required for the structured_state_desktop adapter"
            raise RuntimeError(msg)

        self._client = OpenAI(api_key=api_key)
        self._model = model or _DEFAULT_MODEL
        self._cheap_model = cheap_model or _DEFAULT_CHEAP_MODEL
        self._routing_enabled = routing_enabled
        self._action_history: list[dict[str, str]] = []
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._api_calls: int = 0
        self._step_evidence: list[dict[str, Any]] = []
        # Routing tracking
        self._cheap_steps: int = 0
        self._strong_steps: int = 0
        self._escalations: int = 0
        self._last_step_failed: bool = False

    @property
    def name(self) -> str:
        if self._routing_enabled:
            return "structured_state_desktop_routed"
        return "structured_state_desktop"

    def observation_request(self) -> ObservationType:
        return ObservationType.ARIA_STATE

    def decide(self, observation: Observation, task: Task) -> list[Action]:
        if observation.aria_snapshot is None and observation.a11y_available is False:
            return [
                Action(
                    action_type=ActionType.FAIL,
                    params={"reason": "No accessibility state available"},
                )
            ]

        # Parse the AX tree if we have a structured tree attached
        ax_tree = getattr(observation, "_ax_tree", None)
        if ax_tree is None:
            # Fall back to text-only mode — still usable but less precise
            return self._decide_from_text(observation, task)

        interactive = prune_interactive(ax_tree)
        elements_text = format_for_prompt(interactive)

        prompt = self._build_prompt(
            task=task,
            focused_app=observation.focused_app or "Unknown",
            window_title=observation.page_title or "",
            elements_text=elements_text,
        )

        # Choose model tier via routing heuristic
        model_used = self._route_model(len(interactive))
        response = self._call_llm(prompt, model=model_used)
        semantic = self._parse_response(response)

        # If routing produced a parse failure on cheap model, retry with strong
        if (
            self._routing_enabled
            and model_used == self._cheap_model
            and semantic.get("action") == "fail"
            and "Unparseable" in semantic.get("value", "")
        ):
            # Reclassify this step: undo the cheap count, add a strong count
            self._cheap_steps -= 1
            self._strong_steps += 1
            self._escalations += 1
            model_used = self._model
            response = self._call_llm(prompt, model=model_used)
            semantic = self._parse_response(response)

        # Track whether this step resulted in a fail action (for next-step routing)
        self._last_step_failed = semantic.get("action") == "fail"

        # Record evidence for this decision
        evidence: dict[str, Any] = {
            "focused_app": observation.focused_app,
            "window_title": observation.page_title,
            "interactive_count": len(interactive),
            "elements_text": elements_text[:500],
            "raw_response": response[:500],
            "parsed_action": semantic,
        }
        if self._routing_enabled:
            evidence["model_used"] = model_used
            evidence["routing_tier"] = "cheap" if model_used == self._cheap_model else "strong"
        self._step_evidence.append(evidence)

        actions = self._semantic_to_actions(semantic, ax_tree)

        # Update history
        action_desc = semantic.get("action", "unknown")
        target = semantic.get("target", "")
        self._action_history.append(
            {
                "action": action_desc,
                "target": target,
                "result": "pending",
            }
        )
        if len(self._action_history) > _MAX_HISTORY:
            self._action_history = self._action_history[-_MAX_HISTORY:]

        return actions

    def reset(self) -> None:
        self._action_history = []
        self._input_tokens = 0
        self._output_tokens = 0
        self._api_calls = 0
        self._step_evidence = []
        self._cheap_steps = 0
        self._strong_steps = 0
        self._escalations = 0
        self._last_step_failed = False

    def get_cost_metadata(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "total_tokens": self._input_tokens + self._output_tokens,
            "estimated_cost_usd": self._estimate_cost(),
            "model": self._model,
            "api_calls": self._api_calls,
        }
        if self._routing_enabled:
            meta["routing_enabled"] = True
            meta["cheap_model"] = self._cheap_model
            meta["cheap_steps"] = self._cheap_steps
            meta["strong_steps"] = self._strong_steps
            meta["escalations"] = self._escalations
        return meta

    def get_step_evidence(self) -> list[dict[str, Any]]:
        """Return decision-point evidence collected during the run."""
        return list(self._step_evidence)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _estimate_cost(self) -> float:
        if self._routing_enabled and (self._cheap_steps + self._strong_steps) > 0:
            total_steps = self._cheap_steps + self._strong_steps
            cheap_frac = self._cheap_steps / total_steps
            strong_frac = self._strong_steps / total_steps
            cheap_pricing = _MODEL_PRICING.get(
                self._cheap_model, (_DEFAULT_INPUT_PRICE, _DEFAULT_OUTPUT_PRICE)
            )
            strong_pricing = _MODEL_PRICING.get(
                self._model, (_DEFAULT_INPUT_PRICE, _DEFAULT_OUTPUT_PRICE)
            )
            blended_input = cheap_frac * cheap_pricing[0] + strong_frac * strong_pricing[0]
            blended_output = cheap_frac * cheap_pricing[1] + strong_frac * strong_pricing[1]
            return (self._input_tokens / 1_000_000) * blended_input + (
                self._output_tokens / 1_000_000
            ) * blended_output

        pricing = _MODEL_PRICING.get(self._model, (_DEFAULT_INPUT_PRICE, _DEFAULT_OUTPUT_PRICE))
        return (self._input_tokens / 1_000_000) * pricing[0] + (
            self._output_tokens / 1_000_000
        ) * pricing[1]

    def _route_model(self, interactive_count: int) -> str:
        """Choose cheap or strong model based on routing heuristic.

        Escalation triggers:
        - AX tree is sparse (fewer than _SPARSE_TREE_THRESHOLD interactive elements)
        - Previous step resulted in a fail action
        - Routing is disabled (always use strong model)
        """
        if not self._routing_enabled:
            return self._model

        # Escalate to strong model on sparse tree or post-failure
        if interactive_count < _SPARSE_TREE_THRESHOLD:
            self._strong_steps += 1
            self._escalations += 1
            return self._model

        if self._last_step_failed:
            self._strong_steps += 1
            self._escalations += 1
            return self._model

        self._cheap_steps += 1
        return self._cheap_model

    def _build_prompt(
        self,
        *,
        task: Task,
        focused_app: str,
        window_title: str,
        elements_text: str,
    ) -> str:
        parts = [
            f"TASK: {task.goal.description}",
            "",
            f"FOCUSED APP: {focused_app}",
            f"FOCUSED WINDOW: {window_title}",
            "",
        ]

        if self._action_history:
            parts.append(f"ACTION HISTORY (last {len(self._action_history)}):")
            for i, h in enumerate(self._action_history):
                parts.append(f"  - step {i + 1}: {h['action']} {h['target']} → {h['result']}")
            parts.append("")

        parts.append("INTERACTIVE ELEMENTS:")
        parts.append(elements_text)
        parts.append("")
        parts.append(
            "Return a single JSON action. "
            'Fields: "action" (one of: click, double_click, type_text, press_keys, '
            "scroll, select_menu_item, set_value, open_app, focus_window, wait_for, done, fail), "
            '"target" (element ID like ax_abc12345, or null), '
            '"value" (text to type or menu item, or null), '
            '"fallback_x" and "fallback_y" (pixel coordinates if target unresolvable, or null), '
            '"expected_change" (what should happen, or null).'
        )
        parts.append("")
        parts.append("Respond with ONLY the JSON object, no other text.")

        return "\n".join(parts)

    def _call_llm(self, prompt: str, *, model: str | None = None) -> str:
        """Call the LLM with the structured prompt. Returns raw text response."""
        use_model = model or self._model
        try:
            response = self._client.chat.completions.create(
                model=use_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a desktop automation agent. You observe the current "
                            "accessibility state of a macOS app and return a single JSON "
                            "action to make progress on the task. Be precise with element "
                            "targeting. Prefer semantic targets over coordinates."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_completion_tokens=256,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            return json.dumps({"action": "fail", "value": f"LLM error: {exc}"})

        if response.usage is not None:
            self._input_tokens += response.usage.prompt_tokens
            self._output_tokens += response.usage.completion_tokens
        self._api_calls += 1

        return response.choices[0].message.content or "{}"

    def _parse_response(self, raw: str) -> dict[str, Any]:
        """Parse the LLM's JSON response into a semantic action dict."""
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass

        # Try to extract JSON from markdown code block
        if "```" in raw:
            try:
                json_str = raw.split("```")[1]
                if json_str.startswith("json"):
                    json_str = json_str[4:]
                data = json.loads(json_str.strip())
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, IndexError, TypeError):
                pass

        logger.warning("Could not parse LLM response: %s", raw[:200])
        return {"action": "fail", "value": f"Unparseable response: {raw[:100]}"}

    def _semantic_to_actions(self, semantic: dict[str, Any], ax_tree: AXNode) -> list[Action]:
        """Convert a semantic action dict to harness Action(s)."""
        action_name = semantic.get("action", "fail")
        target_id = semantic.get("target")
        value = semantic.get("value")
        fallback_x = semantic.get("fallback_x")
        fallback_y = semantic.get("fallback_y")

        action_type = _SEMANTIC_ACTION_MAP.get(action_name)
        if action_type is None:
            return [
                Action(
                    action_type=ActionType.FAIL,
                    params={"reason": f"Unknown semantic action: {action_name}"},
                )
            ]

        if action_type == ActionType.DONE:
            return [Action(action_type=ActionType.DONE)]

        if action_type == ActionType.FAIL:
            return [
                Action(
                    action_type=ActionType.FAIL,
                    params={"reason": value or "Agent declared failure"},
                )
            ]

        params: dict[str, Any] = {}

        # Resolve target to coordinates
        if target_id:
            node = find_node_by_id(ax_tree, target_id)
            if node is not None and node.center is not None:
                params["x"], params["y"] = node.center
                params["semantic_target"] = target_id
            elif fallback_x is not None and fallback_y is not None:
                params["x"] = int(fallback_x)
                params["y"] = int(fallback_y)
                params["semantic_target"] = target_id
                params["target_fallback"] = True
            else:
                return [
                    Action(
                        action_type=ActionType.FAIL,
                        params={"reason": f"Target {target_id} not found and no fallback coords"},
                    )
                ]
        elif fallback_x is not None and fallback_y is not None:
            params["x"] = int(fallback_x)
            params["y"] = int(fallback_y)

        # Add action-specific params
        if action_name == "type_text" and value:
            params["text"] = value
            action_type = ActionType.TYPE
        elif action_name == "set_value" and value:
            params["text"] = value
            action_type = ActionType.TYPE
        elif action_name == "press_keys" and value:
            params["key"] = value
        elif action_name == "scroll":
            params["delta_y"] = semantic.get("delta_y", -3)
        elif action_name == "wait_for":
            params["ms"] = semantic.get("ms", 1000)
        elif action_name == "open_app" and value:
            sanitized = _sanitize_app_name(value)
            params["command"] = "open"
            params["args"] = ["-a", sanitized]
            action_type = ActionType.SHELL
        elif action_name == "focus_window":
            app = _sanitize_app_name(value or "")
            if not app:
                return [
                    Action(
                        action_type=ActionType.FAIL,
                        params={"reason": "focus_window requires an app name"},
                    )
                ]
            params["command"] = "osascript"
            params["args"] = ["-e", f'tell application "{app}" to activate']
            action_type = ActionType.SHELL

        return [Action(action_type=action_type, params=params)]

    def _decide_from_text(self, observation: Observation, task: Task) -> list[Action]:
        """Fallback: use raw aria_snapshot text when no structured tree is available."""
        if observation.aria_snapshot is None:
            return [
                Action(
                    action_type=ActionType.FAIL,
                    params={"reason": "No accessibility state available"},
                )
            ]

        prompt = self._build_prompt(
            task=task,
            focused_app=observation.focused_app or "Unknown",
            window_title=observation.page_title or "",
            elements_text=observation.aria_snapshot[:3000],
        )

        response = self._call_llm(prompt)
        semantic = self._parse_response(response)

        # Track failure state for routing (same as structured path)
        self._last_step_failed = semantic.get("action") == "fail"

        evidence = {
            "focused_app": observation.focused_app,
            "window_title": observation.page_title,
            "raw_aria_text": observation.aria_snapshot[:500],
            "raw_response": response[:500],
            "parsed_action": semantic,
        }
        self._step_evidence.append(evidence)

        # Update action history (same as structured path)
        action_desc = semantic.get("action", "unknown")
        target = semantic.get("target", "")
        self._action_history.append({"action": action_desc, "target": target, "result": "pending"})
        if len(self._action_history) > _MAX_HISTORY:
            self._action_history = self._action_history[-_MAX_HISTORY:]

        # Without structured tree, use fallback coordinates directly
        action_name = semantic.get("action", "fail")
        action_type = _SEMANTIC_ACTION_MAP.get(action_name)

        if action_type is None:
            return [
                Action(
                    action_type=ActionType.FAIL,
                    params={"reason": f"Unknown action: {action_name}"},
                )
            ]

        if action_type in (ActionType.DONE, ActionType.FAIL):
            params: dict[str, Any] = {}
            if action_type == ActionType.FAIL:
                params["reason"] = semantic.get("value", "Agent declared failure")
            return [Action(action_type=action_type, params=params)]

        params = {}
        fx = semantic.get("fallback_x")
        fy = semantic.get("fallback_y")
        if fx is not None and fy is not None:
            params["x"] = int(fx)
            params["y"] = int(fy)

        value = semantic.get("value")
        if action_name == "type_text" and value:
            params["text"] = value
        elif action_name == "press_keys" and value:
            params["key"] = value

        return [Action(action_type=action_type, params=params)]
