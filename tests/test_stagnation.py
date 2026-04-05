"""Tests for runner-owned stagnation / loop detection (Milestone 3).

Verifies that the runner detects repeated identical actions with no state
change and terminates the loop with an explicit stagnation signal.
"""

from __future__ import annotations

from harness.runner import _action_signature, _StagnationTracker
from harness.types import Action, ActionType

# ---------------------------------------------------------------------------
# Action signature generation
# ---------------------------------------------------------------------------


class TestActionSignature:
    def test_same_action_same_signature(self) -> None:
        a1 = Action(
            action_type=ActionType.CLICK, params={"semantic_target": "ax_123", "x": 100, "y": 200}
        )
        a2 = Action(
            action_type=ActionType.CLICK, params={"semantic_target": "ax_123", "x": 100, "y": 200}
        )
        assert _action_signature(a1) == _action_signature(a2)

    def test_different_target_different_signature(self) -> None:
        a1 = Action(action_type=ActionType.CLICK, params={"semantic_target": "ax_123"})
        a2 = Action(action_type=ActionType.CLICK, params={"semantic_target": "ax_456"})
        assert _action_signature(a1) != _action_signature(a2)

    def test_different_type_different_signature(self) -> None:
        a1 = Action(action_type=ActionType.CLICK, params={"x": 100, "y": 200})
        a2 = Action(action_type=ActionType.DOUBLE_CLICK, params={"x": 100, "y": 200})
        assert _action_signature(a1) != _action_signature(a2)

    def test_different_text_different_signature(self) -> None:
        a1 = Action(action_type=ActionType.TYPE, params={"text": "hello"})
        a2 = Action(action_type=ActionType.TYPE, params={"text": "world"})
        assert _action_signature(a1) != _action_signature(a2)

    def test_different_key_different_signature(self) -> None:
        a1 = Action(action_type=ActionType.PRESS, params={"key": "command+s"})
        a2 = Action(action_type=ActionType.PRESS, params={"key": "command+z"})
        assert _action_signature(a1) != _action_signature(a2)

    def test_done_and_fail_have_distinct_signatures(self) -> None:
        a1 = Action(action_type=ActionType.DONE)
        a2 = Action(action_type=ActionType.FAIL, params={"reason": "test"})
        assert _action_signature(a1) != _action_signature(a2)


# ---------------------------------------------------------------------------
# Stagnation tracker
# ---------------------------------------------------------------------------


class TestStagnationTracker:
    def test_no_stagnation_below_threshold(self) -> None:
        tracker = _StagnationTracker()
        tracker.record("click|ax_1|||", False)
        tracker.record("click|ax_1|||", False)
        # Only 2 repeats — below threshold of 3
        assert not tracker.is_stagnating()

    def test_stagnation_at_threshold(self) -> None:
        tracker = _StagnationTracker()
        sig = "click|ax_1||||"
        tracker.record(sig, False)
        tracker.record(sig, False)
        tracker.record(sig, False)
        assert tracker.is_stagnating()

    def test_no_stagnation_with_state_change(self) -> None:
        """Repeated action + state changed = not stagnating."""
        tracker = _StagnationTracker()
        sig = "click|ax_1||||"
        tracker.record(sig, True)
        tracker.record(sig, True)
        tracker.record(sig, True)
        assert not tracker.is_stagnating()

    def test_no_stagnation_with_mixed_state(self) -> None:
        """If any repeat shows state change, not stagnating."""
        tracker = _StagnationTracker()
        sig = "click|ax_1||||"
        tracker.record(sig, False)
        tracker.record(sig, True)  # state changed once
        tracker.record(sig, False)
        assert not tracker.is_stagnating()

    def test_stagnation_with_none_state(self) -> None:
        """state_changed=None (unknown) should count as non-progress."""
        tracker = _StagnationTracker()
        sig = "click|ax_1||||"
        tracker.record(sig, None)
        tracker.record(sig, None)
        tracker.record(sig, None)
        assert tracker.is_stagnating()

    def test_no_stagnation_different_actions(self) -> None:
        """Different actions should not trigger stagnation."""
        tracker = _StagnationTracker()
        tracker.record("click|ax_1||||", False)
        tracker.record("click|ax_2||||", False)
        tracker.record("click|ax_3||||", False)
        assert not tracker.is_stagnating()

    def test_stagnation_resets_after_different_action(self) -> None:
        """Inserting a different action breaks the stagnation streak."""
        tracker = _StagnationTracker()
        sig = "click|ax_1||||"
        tracker.record(sig, False)
        tracker.record(sig, False)
        tracker.record("type|||||hello", False)  # breaks the streak
        tracker.record(sig, False)
        tracker.record(sig, False)
        # Only 2 consecutive matching — not stagnating yet
        assert not tracker.is_stagnating()

    def test_window_eviction(self) -> None:
        """History is bounded to the window size."""
        tracker = _StagnationTracker()
        # Fill with different actions
        for i in range(10):
            tracker.record(f"action_{i}", False)
        # None of these repeat 3 times
        assert not tracker.is_stagnating()

    def test_empty_tracker(self) -> None:
        tracker = _StagnationTracker()
        assert not tracker.is_stagnating()
