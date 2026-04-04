"""Codex subscription-backed adapter: ARIA-state-first, semantic-action-only."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from harness.types import Action, ActionType, Observation, ObservationType, Task

logger = logging.getLogger(__name__)

_CODEX_TIMEOUT = 120  # seconds per invocation


class CodexSubscriptionAdapter:
    """Codex CLI adapter using ARIA state and semantic selectors.

    Each ``decide()`` call is a fresh ``codex exec`` invocation with full
    action history in the prompt.  Subscription-billed — no per-call API cost,
    but invocations consume the ChatGPT message quota.
    """

    def __init__(self) -> None:
        if shutil.which("codex") is None:
            msg = (
                "Codex CLI not found on PATH. "
                "Install it and run `codex login` before using this adapter."
            )
            raise RuntimeError(msg)

        self._action_history: list[str] = []
        self._invocation_count: int = 0
        self._total_latency_ms: int = 0

    @property
    def name(self) -> str:
        return "codex_subscription"

    def observation_request(self) -> ObservationType:
        return ObservationType.ARIA_STATE

    def decide(self, observation: Observation, task: Task) -> list[Action]:
        if observation.aria_snapshot is None:
            return [
                Action(
                    action_type=ActionType.FAIL,
                    params={"reason": "No ARIA snapshot provided"},
                )
            ]

        prompt = self._build_prompt(observation, task)

        start = time.monotonic()
        try:
            raw_output = self._invoke_codex(prompt)
        except (subprocess.TimeoutExpired, RuntimeError) as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._total_latency_ms += elapsed_ms
            self._invocation_count += 1
            logger.error("Codex CLI error: %s", exc)
            return [
                Action(
                    action_type=ActionType.FAIL,
                    params={"reason": f"Codex CLI error: {exc}"},
                )
            ]

        elapsed_ms = int((time.monotonic() - start) * 1000)
        self._total_latency_ms += elapsed_ms
        self._invocation_count += 1

        logger.info(
            "Codex invocation %d: %dms, output=%r",
            self._invocation_count,
            elapsed_ms,
            raw_output[:200],
        )

        try:
            parsed = _extract_json(raw_output)
        except ValueError as exc:
            logger.error("JSON extraction failed: %s", exc)
            return [
                Action(
                    action_type=ActionType.FAIL,
                    params={"reason": f"Could not parse Codex output: {raw_output[:200]}"},
                )
            ]

        action = _map_codex_action(parsed)
        self._action_history.append(f"{parsed.get('action', '?')}: {json.dumps(parsed)}")
        return [action]

    def reset(self) -> None:
        self._action_history = []
        self._invocation_count = 0
        self._total_latency_ms = 0

    def get_cost_metadata(self) -> dict[str, Any]:
        return {
            "invocations": self._invocation_count,
            "total_latency_ms": self._total_latency_ms,
            "avg_latency_ms": self._total_latency_ms // max(self._invocation_count, 1),
            "model": "codex-subscription",
            "billing": "subscription",
            "estimated_cost_usd": 0.0,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_prompt(self, observation: Observation, task: Task) -> str:
        history_lines = (
            "\n".join(f"  {i + 1}. {a}" for i, a in enumerate(self._action_history))
            or "  (none yet)"
        )

        return (
            "You are a browser automation agent. You read the accessibility "
            "tree of a web page and decide what action to take next.\n\n"
            f"Task: {task.goal.description}\n\n"
            f"Page URL: {observation.url}\n"
            f"Page title: {observation.page_title}\n\n"
            f"Accessibility tree:\n{observation.aria_snapshot}\n\n"
            f"Actions taken so far:\n{history_lines}\n\n"
            "Return ONLY a single JSON object for the next action. Valid formats:\n"
            '{"action": "click", "selector": "#element-id"}\n'
            '{"action": "click", "selector": "text=Link Text"}\n'
            '{"action": "type", "selector": "#element-id", "text": "value to type"}\n'
            '{"action": "press", "key": "Enter"}\n'
            '{"action": "wait", "ms": 1000}\n'
            '{"action": "done"}\n'
            '{"action": "fail", "reason": "explanation"}\n\n'
            "Use CSS selectors (#id, .class, tag) or Playwright text selectors "
            "(text=Visible Text).\n"
            "Return ONLY the JSON. No explanation, no markdown, no code blocks."
        )

    def _invoke_codex(self, prompt: str) -> str:
        """Invoke ``codex exec`` and return the agent's final message."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as out_file:
            out_path = out_file.name

        output_path = Path(out_path)
        try:
            result = subprocess.run(
                [
                    "codex",
                    "exec",
                    "--full-auto",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "-o",
                    out_path,
                    prompt,
                ],
                input="",
                capture_output=True,
                text=True,
                timeout=_CODEX_TIMEOUT,
            )

            if result.returncode != 0:
                msg = f"Codex CLI exited {result.returncode}: {result.stderr[:500]}"
                raise RuntimeError(msg)

            return output_path.read_text().strip()
        finally:
            output_path.unlink(missing_ok=True)


def _extract_json(raw_output: str) -> dict[str, Any]:
    """Extract first JSON object from Codex CLI output."""
    stripped = raw_output.strip()

    # Direct parse
    try:
        return json.loads(stripped)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        pass

    # Markdown code block
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", stripped, re.DOTALL)
    if md_match:
        try:
            return json.loads(md_match.group(1).strip())  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

    # First { ... } in output
    brace_match = re.search(r"\{[^{}]*\}", stripped)
    if brace_match:
        try:
            return json.loads(brace_match.group())  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

    msg = f"Could not extract JSON from Codex output: {stripped[:200]}"
    raise ValueError(msg)


def _map_codex_action(parsed: dict[str, Any]) -> Action:
    """Map parsed Codex JSON to a harness Action."""
    action_str = parsed.get("action", "")

    try:
        match action_str:
            case "click":
                return Action(
                    action_type=ActionType.CLICK,
                    params={"selector": parsed["selector"]},
                )
            case "type":
                return Action(
                    action_type=ActionType.TYPE,
                    params={"selector": parsed["selector"], "text": parsed["text"]},
                )
            case "press":
                return Action(
                    action_type=ActionType.PRESS,
                    params={"key": parsed["key"]},
                )
            case "wait":
                return Action(
                    action_type=ActionType.WAIT,
                    params={"ms": parsed.get("ms", 1000)},
                )
            case "done":
                return Action(action_type=ActionType.DONE)
            case "fail":
                return Action(
                    action_type=ActionType.FAIL,
                    params={"reason": parsed.get("reason", "Agent declared failure")},
                )
            case _:
                return Action(
                    action_type=ActionType.FAIL,
                    params={"reason": f"Unknown action: {action_str}"},
                )
    except KeyError as exc:
        return Action(
            action_type=ActionType.FAIL,
            params={"reason": f"Malformed action — missing key {exc}: {parsed}"},
        )
