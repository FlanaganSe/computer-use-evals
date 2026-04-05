"""Machine-readable AX state representation with stable IDs and pruning.

Provides a parallel path to the existing human-readable text serializer
in environments/macos.py. The text serializer is preserved for debugging;
this module adds structured nodes with stable references, bounding boxes,
and interactive-element filtering for the structured-state desktop adapter.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AX node model
# ---------------------------------------------------------------------------

# Roles considered interactive for pruning purposes.
# Based on research-findings.md §3.1 pruning rules.
INTERACTIVE_ROLES: frozenset[str] = frozenset(
    {
        "AXButton",
        "AXTextField",
        "AXTextArea",
        "AXCheckBox",
        "AXRadioButton",
        "AXPopUpButton",
        "AXMenuItem",
        "AXLink",
        "AXSlider",
        "AXIncrementor",
        "AXComboBox",
        "AXMenuButton",
        "AXDisclosureTriangle",
        "AXToolbar",
        "AXTabGroup",
        "AXTab",
    }
)

# Roles explicitly excluded from pruning output (layout/decorative).
EXCLUDED_ROLES: frozenset[str] = frozenset(
    {
        "AXGroup",
        "AXScrollArea",
        "AXSplitGroup",
        "AXImage",
        "AXSeparator",
        "AXSplitter",
        "AXGrowArea",
        "AXMatte",
        "AXRuler",
        "AXLayoutArea",
        "AXLayoutItem",
    }
)


class AXNode(BaseModel):
    """A single node in a machine-readable AX tree."""

    node_id: str
    """Stable ID: hash of (role, title, ancestry_path)."""

    role: str
    title: str = ""
    value: str = ""
    description: str = ""
    enabled: bool = True
    focused: bool = False

    # Bounding box in screen points (x, y, width, height).
    # None if the element has no position/size.
    bounds: tuple[float, float, float, float] | None = None

    children: list[AXNode] = Field(default_factory=list)

    @property
    def center(self) -> tuple[int, int] | None:
        """Center point in screen coordinates, or None if no bounds."""
        if self.bounds is None:
            return None
        x, y, w, h = self.bounds
        return (int(x + w / 2), int(y + h / 2))

    @property
    def is_interactive(self) -> bool:
        return self.role in INTERACTIVE_ROLES


# ---------------------------------------------------------------------------
# Stable ID generation
# ---------------------------------------------------------------------------


def _make_node_id(role: str, title: str, ancestry_path: str, sibling_index: int = 0) -> str:
    """Generate a stable, short node ID from role + title + ancestry path.

    Uses a truncated SHA-256 so IDs are deterministic across runs for the
    same UI structure, but short enough for LLM prompts.

    The sibling_index disambiguates siblings that share the same role and
    title (e.g. multiple untitled AXButton elements in a toolbar). It is
    only included in the hash when > 0 so that existing IDs for unique
    siblings remain stable.

    12 hex chars = 48 bits of ID space, giving negligible collision risk
    across typical screen sizes (~50 elements) and multi-step runs.
    """
    key = f"{ancestry_path}/{role}:{title}"
    if sibling_index > 0:
        key = f"{key}#{sibling_index}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"ax_{digest}"


# ---------------------------------------------------------------------------
# Build structured tree from raw pyobjc AXUIElement
# ---------------------------------------------------------------------------


def _get_attr(element: Any, attr_name: str) -> Any:
    """Safely get an AX attribute from an element."""
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue  # type: ignore[import-untyped]

        err, value = AXUIElementCopyAttributeValue(element, attr_name, None)
        if err == 0:
            return value
    except Exception:
        pass
    return None


def _get_bounds(element: Any) -> tuple[float, float, float, float] | None:
    """Extract bounding box (x, y, w, h) from AXPosition + AXSize."""
    pos = _get_attr(element, "AXPosition")
    size = _get_attr(element, "AXSize")
    if pos is None or size is None:
        return None
    try:
        x = float(pos.x)
        y = float(pos.y)
        w = float(size.width)
        h = float(size.height)
        return (x, y, w, h)
    except (AttributeError, TypeError, ValueError):
        return None


def build_ax_tree(
    element: Any,
    depth: int = 0,
    max_depth: int = 10,
    ancestry_path: str = "",
    sibling_index: int = 0,
    _refs: dict[str, Any] | None = None,
) -> AXNode | None:
    """Recursively build a structured AXNode tree from a pyobjc AXUIElement.

    If _refs is provided, populates it with {node_id: raw_AXUIElement}
    so callers can perform direct AX actions on elements without coordinates.

    Returns None if the element cannot be read.
    """
    if depth > max_depth:
        return None

    role = _get_attr(element, "AXRole") or "unknown"
    title = str(_get_attr(element, "AXTitle") or "")
    value_raw = _get_attr(element, "AXValue")
    value = str(value_raw)[:200] if value_raw is not None else ""
    desc = str(_get_attr(element, "AXDescription") or "")

    enabled_raw = _get_attr(element, "AXEnabled")
    enabled = bool(enabled_raw) if enabled_raw is not None else True

    focused_raw = _get_attr(element, "AXFocused")
    focused = bool(focused_raw) if focused_raw is not None else False

    bounds = _get_bounds(element)

    current_path = f"{ancestry_path}/{role}:{title}"
    node_id = _make_node_id(role, title, ancestry_path, sibling_index)

    if _refs is not None:
        _refs[node_id] = element

    children: list[AXNode] = []
    ax_children = _get_attr(element, "AXChildren") or []
    # Track sibling signature counts to disambiguate children with same role+title
    sibling_counts: dict[str, int] = {}
    for child in ax_children:
        child_role = _get_attr(child, "AXRole") or "unknown"
        child_title = str(_get_attr(child, "AXTitle") or "")
        sig = f"{child_role}:{child_title}"
        child_idx = sibling_counts.get(sig, 0)
        sibling_counts[sig] = child_idx + 1
        child_node = build_ax_tree(child, depth + 1, max_depth, current_path, child_idx, _refs)
        if child_node is not None:
            children.append(child_node)

    return AXNode(
        node_id=node_id,
        role=role,
        title=title,
        value=value,
        description=desc,
        enabled=enabled,
        focused=focused,
        bounds=bounds,
        children=children,
    )


# ---------------------------------------------------------------------------
# Build structured tree from a plain dict (for testing / deserialization)
# ---------------------------------------------------------------------------


def build_ax_tree_from_dict(
    data: dict[str, Any],
    depth: int = 0,
    max_depth: int = 10,
    ancestry_path: str = "",
    sibling_index: int = 0,
) -> AXNode | None:
    """Build an AXNode tree from a dict representation (for testing)."""
    if depth > max_depth:
        return None

    role = data.get("role", "unknown")
    title = data.get("title", "")
    value = data.get("value", "")
    desc = data.get("description", "")
    enabled = data.get("enabled", True)
    focused = data.get("focused", False)
    raw_bounds = data.get("bounds")
    bounds = tuple(raw_bounds) if raw_bounds is not None and len(raw_bounds) == 4 else None

    current_path = f"{ancestry_path}/{role}:{title}"
    node_id = _make_node_id(role, title, ancestry_path, sibling_index)

    children: list[AXNode] = []
    sibling_counts: dict[str, int] = {}
    for child_data in data.get("children", []):
        child_role = child_data.get("role", "unknown")
        child_title = child_data.get("title", "")
        sig = f"{child_role}:{child_title}"
        child_idx = sibling_counts.get(sig, 0)
        sibling_counts[sig] = child_idx + 1
        child_node = build_ax_tree_from_dict(
            child_data, depth + 1, max_depth, current_path, child_idx
        )
        if child_node is not None:
            children.append(child_node)

    return AXNode(
        node_id=node_id,
        role=role,
        title=title,
        value=value,
        description=desc,
        enabled=enabled,
        focused=focused,
        bounds=bounds,
        children=children,
    )


# ---------------------------------------------------------------------------
# Pruning: extract interactive elements
# ---------------------------------------------------------------------------


def prune_interactive(
    root: AXNode,
    *,
    max_elements: int = 50,
    include_focused: bool = True,
) -> list[AXNode]:
    """Extract interactive elements from an AX tree.

    Returns a flat list of interactive nodes, pruned to max_elements.
    The focused element is always included even if non-interactive.
    Nodes are ordered by tree traversal (top-down, left-to-right).
    """
    interactive: list[AXNode] = []
    focused_node: AXNode | None = None

    def _walk(node: AXNode) -> None:
        nonlocal focused_node

        if include_focused and node.focused and focused_node is None:
            focused_node = node

        if node.role in EXCLUDED_ROLES:
            # Still walk children — interactive elements can be nested
            for child in node.children:
                _walk(child)
            return

        if node.is_interactive and node.enabled:
            interactive.append(node)

        for child in node.children:
            _walk(child)

    _walk(root)

    # Ensure focused element is included even if not interactive
    if focused_node is not None and focused_node not in interactive:
        interactive.insert(0, focused_node)

    # Cap at max_elements
    if len(interactive) > max_elements:
        interactive = interactive[:max_elements]

    return interactive


# ---------------------------------------------------------------------------
# Format for LLM prompt
# ---------------------------------------------------------------------------


def format_for_prompt(nodes: list[AXNode]) -> str:
    """Format a list of AX nodes into the prompt representation.

    Output format per node:
        [ax_abc123] AXButton "Save" enabled bounds=(450,320,80,24)
    """
    lines: list[str] = []
    for node in nodes:
        parts = [f"[{node.node_id}]", node.role]

        if node.title:
            parts.append(f'"{node.title}"')

        if node.description and node.description != node.title:
            parts.append(f"({node.description})")

        if node.value:
            val = node.value if len(node.value) <= 60 else node.value[:57] + "..."
            parts.append(f'value="{val}"')

        if not node.enabled:
            parts.append("disabled")
        if node.focused:
            parts.append("focused")

        if node.bounds is not None:
            x, y, w, h = node.bounds
            parts.append(f"bounds=({int(x)},{int(y)},{int(w)},{int(h)})")

        lines.append(" ".join(parts))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Node lookup by ID
# ---------------------------------------------------------------------------


def find_node_by_id(root: AXNode, node_id: str) -> AXNode | None:
    """Find a node by its stable ID in the tree. Returns None if not found."""
    if root.node_id == node_id:
        return root
    for child in root.children:
        found = find_node_by_id(child, node_id)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# Coverage statistics (for AX probe)
# ---------------------------------------------------------------------------


def coverage_stats(root: AXNode) -> dict[str, Any]:
    """Compute AX coverage statistics for a tree.

    Returns counts useful for the AX coverage probe.
    """
    total = 0
    interactive_count = 0
    roles: dict[str, int] = {}
    has_bounds = 0

    def _walk(node: AXNode) -> None:
        nonlocal total, interactive_count, has_bounds
        total += 1
        roles[node.role] = roles.get(node.role, 0) + 1
        if node.is_interactive:
            interactive_count += 1
        if node.bounds is not None:
            has_bounds += 1
        for child in node.children:
            _walk(child)

    _walk(root)
    return {
        "total_nodes": total,
        "interactive_nodes": interactive_count,
        "nodes_with_bounds": has_bounds,
        "roles": roles,
    }
