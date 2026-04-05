"""Tests for event grouping logic (pure Python, no OS dependency)."""

from __future__ import annotations

from harness.intent_extract import group_events


class TestGroupEvents:
    def test_empty_events(self) -> None:
        assert group_events([]) == []

    def test_single_click(self) -> None:
        events = [
            {"t": 1.0, "type": "mouse", "button": "left", "x": 500, "y": 300, "click_count": 1}
        ]
        result = group_events(events)
        assert result == ["At t=1.0s clicked (500, 300)"]

    def test_right_click(self) -> None:
        events = [
            {"t": 2.0, "type": "mouse", "button": "right", "x": 100, "y": 200, "click_count": 1}
        ]
        result = group_events(events)
        assert result == ["At t=2.0s right-clicked (100, 200)"]

    def test_double_click(self) -> None:
        events = [
            {"t": 3.0, "type": "mouse", "button": "left", "x": 50, "y": 50, "click_count": 2}
        ]
        result = group_events(events)
        assert result == ["At t=3.0s double-clicked (50, 50)"]

    def test_sequential_keys_grouped(self) -> None:
        events = [
            {"t": 1.0, "type": "key", "char": "h", "keycode": 4, "modifiers": []},
            {"t": 1.1, "type": "key", "char": "i", "keycode": 34, "modifiers": []},
        ]
        result = group_events(events)
        assert result == ["At t=1.0s typed 'hi'"]

    def test_keys_broken_by_pause(self) -> None:
        events = [
            {"t": 1.0, "type": "key", "char": "a", "keycode": 0, "modifiers": []},
            {"t": 2.0, "type": "key", "char": "b", "keycode": 11, "modifiers": []},
        ]
        result = group_events(events)
        assert len(result) == 2
        assert result[0] == "At t=1.0s typed 'a'"
        assert result[1] == "At t=2.0s typed 'b'"

    def test_keys_broken_by_click(self) -> None:
        events = [
            {"t": 1.0, "type": "key", "char": "x", "keycode": 7, "modifiers": []},
            {"t": 1.5, "type": "mouse", "button": "left", "x": 10, "y": 20, "click_count": 1},
            {"t": 2.0, "type": "key", "char": "y", "keycode": 16, "modifiers": []},
        ]
        result = group_events(events)
        assert result == [
            "At t=1.0s typed 'x'",
            "At t=1.5s clicked (10, 20)",
            "At t=2.0s typed 'y'",
        ]

    def test_keys_broken_by_special_key(self) -> None:
        events = [
            {"t": 1.0, "type": "key", "char": "a", "keycode": 0, "modifiers": []},
            {"t": 1.1, "type": "key", "char": "b", "keycode": 11, "modifiers": []},
            {
                "t": 1.2,
                "type": "key",
                "char": None,
                "keycode": 36,
                "key_name": "Return",
                "modifiers": [],
            },
        ]
        result = group_events(events)
        assert result == [
            "At t=1.0s typed 'ab'",
            "At t=1.2s pressed [Return]",
        ]

    def test_modifier_combination(self) -> None:
        events = [
            {"t": 5.7, "type": "key", "char": "c", "keycode": 8, "modifiers": ["command"]},
        ]
        result = group_events(events)
        assert result == ["At t=5.7s pressed Command+c"]

    def test_scroll_down(self) -> None:
        events = [{"t": 8.9, "type": "scroll", "delta_y": -3, "x": 640, "y": 400}]
        result = group_events(events)
        assert result == ["At t=8.9s scrolled down"]

    def test_scroll_up(self) -> None:
        events = [{"t": 1.0, "type": "scroll", "delta_y": 5, "x": 100, "y": 100}]
        result = group_events(events)
        assert result == ["At t=1.0s scrolled up"]

    def test_full_sequence(self) -> None:
        """Realistic sequence: click, type email, press Return."""
        events = [
            {"t": 0.0, "type": "mouse", "button": "left", "x": 500, "y": 300, "click_count": 1},
            {"t": 1.0, "type": "key", "char": "j", "keycode": 38, "modifiers": []},
            {"t": 1.1, "type": "key", "char": "a", "keycode": 0, "modifiers": []},
            {"t": 1.2, "type": "key", "char": "n", "keycode": 45, "modifiers": []},
            {"t": 1.3, "type": "key", "char": "e", "keycode": 14, "modifiers": []},
            {
                "t": 2.3,
                "type": "key",
                "char": None,
                "keycode": 36,
                "key_name": "Return",
                "modifiers": [],
            },
        ]
        result = group_events(events)
        assert result == [
            "At t=0.0s clicked (500, 300)",
            "At t=1.0s typed 'jane'",
            "At t=2.3s pressed [Return]",
        ]

    def test_keys_broken_by_scroll(self) -> None:
        events = [
            {"t": 1.0, "type": "key", "char": "a", "keycode": 0, "modifiers": []},
            {"t": 1.2, "type": "scroll", "delta_y": -1, "x": 0, "y": 0},
            {"t": 1.5, "type": "key", "char": "b", "keycode": 11, "modifiers": []},
        ]
        result = group_events(events)
        assert result == [
            "At t=1.0s typed 'a'",
            "At t=1.2s scrolled down",
            "At t=1.5s typed 'b'",
        ]

    def test_special_key_without_name(self) -> None:
        events = [
            {"t": 1.0, "type": "key", "char": None, "keycode": 999, "modifiers": []},
        ]
        result = group_events(events)
        assert result == ["At t=1.0s pressed keycode=999"]
