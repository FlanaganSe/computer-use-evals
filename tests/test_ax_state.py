"""Tests for AX state module: stable IDs, pruning, and serialization."""

from __future__ import annotations

from harness.ax_state import (
    AXNode,
    build_ax_tree_from_dict,
    coverage_stats,
    find_node_by_id,
    format_for_prompt,
    prune_interactive,
    _make_node_id,
)


# ---------------------------------------------------------------------------
# Stable ID generation
# ---------------------------------------------------------------------------


class TestStableIds:
    def test_deterministic(self) -> None:
        id1 = _make_node_id("AXButton", "Save", "/AXApplication:TextEdit")
        id2 = _make_node_id("AXButton", "Save", "/AXApplication:TextEdit")
        assert id1 == id2

    def test_prefix(self) -> None:
        node_id = _make_node_id("AXButton", "Save", "")
        assert node_id.startswith("ax_")

    def test_different_ancestry_different_id(self) -> None:
        id1 = _make_node_id("AXButton", "Save", "/AXWindow:Doc1")
        id2 = _make_node_id("AXButton", "Save", "/AXWindow:Doc2")
        assert id1 != id2

    def test_different_role_different_id(self) -> None:
        id1 = _make_node_id("AXButton", "Save", "/app")
        id2 = _make_node_id("AXTextField", "Save", "/app")
        assert id1 != id2

    def test_length(self) -> None:
        node_id = _make_node_id("AXButton", "OK", "")
        # "ax_" + 12 hex chars = 15
        assert len(node_id) == 15


# ---------------------------------------------------------------------------
# AXNode model
# ---------------------------------------------------------------------------


class TestAXNode:
    def test_center_with_bounds(self) -> None:
        node = AXNode(
            node_id="ax_test1234",
            role="AXButton",
            title="OK",
            bounds=(100.0, 200.0, 80.0, 24.0),
        )
        assert node.center == (140, 212)

    def test_center_without_bounds(self) -> None:
        node = AXNode(node_id="ax_test1234", role="AXButton", title="OK")
        assert node.center is None

    def test_is_interactive(self) -> None:
        button = AXNode(node_id="ax_1", role="AXButton", title="OK")
        assert button.is_interactive

        group = AXNode(node_id="ax_2", role="AXGroup", title="")
        assert not group.is_interactive

        text_field = AXNode(node_id="ax_3", role="AXTextField", title="Name")
        assert text_field.is_interactive


# ---------------------------------------------------------------------------
# Tree building from dict
# ---------------------------------------------------------------------------


def _sample_tree_dict() -> dict:
    return {
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
                        "title": "Close",
                        "enabled": True,
                        "bounds": [10, 10, 14, 14],
                    },
                    {
                        "role": "AXButton",
                        "title": "Minimize",
                        "enabled": True,
                        "bounds": [30, 10, 14, 14],
                    },
                    {
                        "role": "AXTextArea",
                        "title": "",
                        "value": "Hello world",
                        "focused": True,
                        "bounds": [0, 50, 800, 550],
                    },
                    {
                        "role": "AXGroup",
                        "title": "toolbar",
                        "children": [
                            {
                                "role": "AXButton",
                                "title": "Bold",
                                "enabled": True,
                                "bounds": [100, 30, 24, 24],
                            },
                        ],
                    },
                    {
                        "role": "AXStaticText",
                        "title": "Status",
                        "value": "Ready",
                    },
                    {
                        "role": "AXImage",
                        "title": "icon",
                    },
                ],
            },
        ],
    }


class TestBuildTreeFromDict:
    def test_builds_tree(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        assert tree.role == "AXApplication"
        assert tree.title == "TextEdit"
        assert len(tree.children) == 1  # one window

    def test_nested_children(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        window = tree.children[0]
        assert window.role == "AXWindow"
        # window has: Close, Minimize, TextArea, Group, StaticText, Image
        assert len(window.children) == 6

    def test_respects_max_depth(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict(), max_depth=1)
        assert tree is not None
        # depth 0 = Application, depth 1 = Window, depth 2 = truncated
        window = tree.children[0]
        assert len(window.children) == 0  # children at depth 2 are cut

    def test_bounds_parsed(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        window = tree.children[0]
        close_btn = window.children[0]
        assert close_btn.bounds == (10, 10, 14, 14)

    def test_focused_flag(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        window = tree.children[0]
        text_area = window.children[2]
        assert text_area.focused is True


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


class TestPruning:
    def test_returns_interactive_only(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        result = prune_interactive(tree)
        for node in result:
            assert node.is_interactive or node.focused

    def test_excludes_layout_roles(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        result = prune_interactive(tree)
        roles = {n.role for n in result}
        assert "AXGroup" not in roles
        assert "AXImage" not in roles
        assert "AXStaticText" not in roles

    def test_includes_nested_interactive_inside_excluded(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        result = prune_interactive(tree)
        titles = {n.title for n in result}
        # Bold button is inside an AXGroup (excluded role), but should still appear
        assert "Bold" in titles

    def test_includes_focused_non_interactive(self) -> None:
        # TextArea is interactive anyway, but test with a custom tree
        data = {
            "role": "AXApplication",
            "title": "App",
            "children": [
                {"role": "AXStaticText", "title": "Label", "focused": True},
                {"role": "AXButton", "title": "OK", "enabled": True},
            ],
        }
        tree = build_ax_tree_from_dict(data)
        assert tree is not None
        result = prune_interactive(tree, include_focused=True)
        titles = {n.title for n in result}
        assert "Label" in titles
        assert "OK" in titles

    def test_max_elements_cap(self) -> None:
        children = [{"role": "AXButton", "title": f"Btn{i}", "enabled": True} for i in range(100)]
        data = {"role": "AXApplication", "title": "App", "children": children}
        tree = build_ax_tree_from_dict(data)
        assert tree is not None
        result = prune_interactive(tree, max_elements=10)
        assert len(result) == 10

    def test_disabled_elements_excluded(self) -> None:
        data = {
            "role": "AXApplication",
            "title": "App",
            "children": [
                {"role": "AXButton", "title": "Enabled", "enabled": True},
                {"role": "AXButton", "title": "Disabled", "enabled": False},
            ],
        }
        tree = build_ax_tree_from_dict(data)
        assert tree is not None
        result = prune_interactive(tree)
        titles = {n.title for n in result}
        assert "Enabled" in titles
        assert "Disabled" not in titles

    def test_sample_tree_count(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        result = prune_interactive(tree)
        # Close, Minimize, TextArea (focused+interactive), Bold = 4
        assert len(result) == 4


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


class TestFormatForPrompt:
    def test_basic_format(self) -> None:
        nodes = [
            AXNode(
                node_id="ax_abc12345",
                role="AXButton",
                title="Save",
                enabled=True,
                bounds=(450, 320, 80, 24),
            ),
        ]
        text = format_for_prompt(nodes)
        assert "[ax_abc12345]" in text
        assert 'AXButton "Save"' in text
        assert "bounds=(450,320,80,24)" in text

    def test_disabled_shown(self) -> None:
        nodes = [
            AXNode(node_id="ax_1", role="AXButton", title="Undo", enabled=False),
        ]
        text = format_for_prompt(nodes)
        assert "disabled" in text

    def test_focused_shown(self) -> None:
        nodes = [
            AXNode(node_id="ax_1", role="AXTextArea", title="", focused=True),
        ]
        text = format_for_prompt(nodes)
        assert "focused" in text

    def test_value_truncated(self) -> None:
        nodes = [
            AXNode(node_id="ax_1", role="AXTextField", title="Input", value="x" * 100),
        ]
        text = format_for_prompt(nodes)
        assert "..." in text

    def test_description_included(self) -> None:
        nodes = [
            AXNode(
                node_id="ax_1",
                role="AXButton",
                title="Submit",
                description="Send form",
            ),
        ]
        text = format_for_prompt(nodes)
        assert "(Send form)" in text

    def test_multiple_nodes(self) -> None:
        nodes = [
            AXNode(node_id="ax_1", role="AXButton", title="OK"),
            AXNode(node_id="ax_2", role="AXButton", title="Cancel"),
        ]
        text = format_for_prompt(nodes)
        lines = text.strip().split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# Node lookup
# ---------------------------------------------------------------------------


class TestFindNodeById:
    def test_finds_root(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        found = find_node_by_id(tree, tree.node_id)
        assert found is tree

    def test_finds_nested(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        window = tree.children[0]
        close_btn = window.children[0]
        found = find_node_by_id(tree, close_btn.node_id)
        assert found is close_btn

    def test_returns_none_for_missing(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        assert find_node_by_id(tree, "ax_nonexistent") is None


# ---------------------------------------------------------------------------
# Coverage statistics
# ---------------------------------------------------------------------------


class TestCoverageStats:
    def test_counts(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        stats = coverage_stats(tree)
        assert stats["total_nodes"] > 0
        assert stats["interactive_nodes"] > 0
        assert "AXButton" in stats["roles"]

    def test_bounds_count(self) -> None:
        tree = build_ax_tree_from_dict(_sample_tree_dict())
        assert tree is not None
        stats = coverage_stats(tree)
        # Window + Close + Minimize + TextArea + Bold = 5 nodes with bounds
        assert stats["nodes_with_bounds"] == 5
