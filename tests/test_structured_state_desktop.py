"""Tests for StructuredStateDesktopAdapter and semantic target resolution."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from harness.ax_state import AXNode, build_ax_tree_from_dict
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
                    {
                        "role": "AXTextField",
                        "title": "File name",
                        "value": "Untitled",
                        "focused": True,
                        "bounds": [200, 320, 240, 24],
                    },
                    {
                        "role": "AXButton",
                        "title": "Cancel",
                        "enabled": True,
                        "bounds": [540, 320, 80, 24],
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


# ---------------------------------------------------------------------------
# Adapter: observation request
# ---------------------------------------------------------------------------


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


class TestObservationRequest:
    def test_requests_aria_state(self) -> None:
        adapter = _make_adapter()
        assert adapter.observation_request() == ObservationType.ARIA_STATE

    def test_name(self) -> None:
        adapter = _make_adapter()
        assert adapter.name == "structured_state_desktop"


# ---------------------------------------------------------------------------
# Adapter: decide with structured tree
# ---------------------------------------------------------------------------


class TestDecideWithTree:
    def _make_adapter(self):
        return _make_adapter()

    def test_click_action(self) -> None:
        adapter = self._make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        # Find the Save button's ID
        save_btn = tree.children[0].children[0]

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {
                            "action": "click",
                            "target": save_btn.node_id,
                            "expected_change": "File save dialog opens",
                        }
                    )
                )
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.CLICK
        assert "x" in actions[0].params
        assert "y" in actions[0].params
        assert actions[0].params["semantic_target"] == save_btn.node_id

    def test_type_action(self) -> None:
        adapter = self._make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        text_field = tree.children[0].children[1]

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {
                            "action": "type_text",
                            "target": text_field.node_id,
                            "value": "report.txt",
                        }
                    )
                )
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.TYPE
        assert actions[0].params["text"] == "report.txt"

    def test_done_action(self) -> None:
        adapter = self._make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"action": "done"})))
        ]
        mock_response.usage = MagicMock(prompt_tokens=50, completion_tokens=20)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.DONE

    def test_fail_when_target_not_found(self) -> None:
        adapter = self._make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {
                            "action": "click",
                            "target": "ax_nonexistent",
                        }
                    )
                )
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=50, completion_tokens=20)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.FAIL
        assert "not found" in actions[0].params["reason"]

    def test_fallback_coordinates_when_target_missing(self) -> None:
        adapter = self._make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {
                            "action": "click",
                            "target": "ax_nonexistent",
                            "fallback_x": 500,
                            "fallback_y": 300,
                        }
                    )
                )
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=50, completion_tokens=20)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.CLICK
        assert actions[0].params["x"] == 500
        assert actions[0].params["y"] == 300
        assert actions[0].params.get("target_fallback") is True

    def test_press_keys_action(self) -> None:
        adapter = self._make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {
                            "action": "press_keys",
                            "value": "command+s",
                        }
                    )
                )
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=50, completion_tokens=20)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.PRESS
        assert actions[0].params["key"] == "command+s"


# ---------------------------------------------------------------------------
# Adapter: decide without structured tree (text fallback)
# ---------------------------------------------------------------------------


class TestDecideFromText:
    def _make_adapter(self):
        return _make_adapter()

    def test_text_fallback_done(self) -> None:
        adapter = self._make_adapter()
        obs = _make_observation(ax_tree=None)  # no structured tree
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"action": "done"})))
        ]
        mock_response.usage = MagicMock(prompt_tokens=50, completion_tokens=20)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            actions = adapter.decide(obs, task)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.DONE

    def test_no_aria_returns_fail(self) -> None:
        adapter = self._make_adapter()
        obs = Observation(
            observation_type=ObservationType.ARIA_STATE,
            a11y_available=False,
        )
        task = _make_task()
        actions = adapter.decide(obs, task)
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.FAIL


# ---------------------------------------------------------------------------
# Adapter: evidence collection
# ---------------------------------------------------------------------------


class TestEvidenceCollection:
    def _make_adapter(self):
        return _make_adapter()

    def test_evidence_recorded(self) -> None:
        adapter = self._make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"action": "done"})))
        ]
        mock_response.usage = MagicMock(prompt_tokens=50, completion_tokens=20)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            adapter.decide(obs, task)

        evidence = adapter.get_step_evidence()
        assert len(evidence) == 1
        assert evidence[0]["focused_app"] == "TextEdit"
        assert evidence[0]["parsed_action"]["action"] == "done"

    def test_cost_metadata(self) -> None:
        adapter = self._make_adapter()
        tree = _sample_tree()
        obs = _make_observation(tree)
        task = _make_task()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"action": "done"})))
        ]
        mock_response.usage = MagicMock(prompt_tokens=1000, completion_tokens=100)

        with patch.object(adapter._client.chat.completions, "create", return_value=mock_response):
            adapter.decide(obs, task)

        meta = adapter.get_cost_metadata()
        assert meta["input_tokens"] == 1000
        assert meta["output_tokens"] == 100
        assert meta["api_calls"] == 1
        assert meta["estimated_cost_usd"] > 0


# ---------------------------------------------------------------------------
# Adapter: reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_state(self) -> None:
        adapter = _make_adapter()
        adapter._input_tokens = 500
        adapter._action_history = [{"action": "click", "target": "ax_1", "result": "ok"}]

        adapter.reset()
        assert adapter._input_tokens == 0
        assert adapter._action_history == []
        assert adapter.get_step_evidence() == []


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


class TestPromptBuilding:
    def _make_adapter(self):
        return _make_adapter()

    def test_prompt_includes_task(self) -> None:
        adapter = self._make_adapter()
        task = _make_task()

        prompt = adapter._build_prompt(
            task=task,
            focused_app="TextEdit",
            window_title="Untitled",
            elements_text='[ax_1] AXButton "Save"',
        )
        assert "Open TextEdit" in prompt
        assert "TextEdit" in prompt
        assert "ax_1" in prompt

    def test_prompt_includes_history(self) -> None:
        adapter = self._make_adapter()
        adapter._action_history = [
            {"action": "click", "target": "ax_1", "result": "ok"},
        ]
        task = _make_task()

        prompt = adapter._build_prompt(
            task=task,
            focused_app="TextEdit",
            window_title="Untitled",
            elements_text="",
        )
        assert "ACTION HISTORY" in prompt
        assert "click" in prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestResponseParsing:
    def _make_adapter(self):
        return _make_adapter()

    def test_parses_json(self) -> None:
        adapter = self._make_adapter()
        result = adapter._parse_response('{"action": "click", "target": "ax_1"}')
        assert result["action"] == "click"
        assert result["target"] == "ax_1"

    def test_extracts_from_code_block(self) -> None:
        adapter = self._make_adapter()
        raw = 'Here is the action:\n```json\n{"action": "done"}\n```\nDone.'
        result = adapter._parse_response(raw)
        assert result["action"] == "done"

    def test_unparseable_returns_fail(self) -> None:
        adapter = self._make_adapter()
        result = adapter._parse_response("this is not json at all")
        assert result["action"] == "fail"


# ---------------------------------------------------------------------------
# Semantic target resolution in macOS environment
# ---------------------------------------------------------------------------


class TestSemanticTargetResolution:
    def test_resolves_target_from_tree(self) -> None:
        from harness.environments.macos import MacOSDesktopEnvironment

        env = MacOSDesktopEnvironment()
        tree = _sample_tree()
        env._last_ax_tree = tree

        save_btn = tree.children[0].children[0]
        action = Action(
            action_type=ActionType.CLICK,
            params={"semantic_target": save_btn.node_id},
        )

        resolved = env.resolve_semantic_target(action)
        assert "x" in resolved.params
        assert "y" in resolved.params
        # Center of (450, 320, 80, 24) = (490, 332)
        assert resolved.params["x"] == 490
        assert resolved.params["y"] == 332

    def test_no_resolution_when_coords_present(self) -> None:
        from harness.environments.macos import MacOSDesktopEnvironment

        env = MacOSDesktopEnvironment()
        tree = _sample_tree()
        env._last_ax_tree = tree

        action = Action(
            action_type=ActionType.CLICK,
            params={"x": 100, "y": 200, "semantic_target": "ax_something"},
        )

        resolved = env.resolve_semantic_target(action)
        assert resolved.params["x"] == 100
        assert resolved.params["y"] == 200

    def test_no_resolution_without_tree(self) -> None:
        from harness.environments.macos import MacOSDesktopEnvironment

        env = MacOSDesktopEnvironment()
        env._last_ax_tree = None

        action = Action(
            action_type=ActionType.CLICK,
            params={"semantic_target": "ax_test"},
        )

        resolved = env.resolve_semantic_target(action)
        # Returns unchanged since no tree available
        assert "x" not in resolved.params

    def test_no_resolution_for_missing_target(self) -> None:
        from harness.environments.macos import MacOSDesktopEnvironment

        env = MacOSDesktopEnvironment()
        tree = _sample_tree()
        env._last_ax_tree = tree

        action = Action(
            action_type=ActionType.CLICK,
            params={"semantic_target": "ax_nonexistent"},
        )

        resolved = env.resolve_semantic_target(action)
        assert "x" not in resolved.params


# ---------------------------------------------------------------------------
# Adapter registration
# ---------------------------------------------------------------------------


class TestAdapterRegistration:
    def test_registered_in_runner(self) -> None:
        from harness.runner import ADAPTERS

        assert "structured_state_desktop" in ADAPTERS
