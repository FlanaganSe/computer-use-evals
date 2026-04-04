"""
Core types for the GPA eval harness.

Design principle: every dimension of variation in the eval is represented
as a distinct type, so you can independently vary perception, grounding,
model, and execution strategy.

The type system also captures the "unknown unknowns" — failure modes,
timing data, cost data, and environmental metadata that help surface
issues you didn't know to look for.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Perception: how the system "sees" the screen
# ---------------------------------------------------------------------------

class PerceptionMode(Enum):
    """Which perception channel(s) to use.

    This is a key eval variable: the research shows screenshot-based agents
    use ~50K tokens per frame while accessibility trees use ~4K. For long
    workflows, this 12x difference compounds into fundamentally different
    reliability profiles.
    """
    SCREENSHOT = auto()          # Raw pixels only (vision model approach)
    ACCESSIBILITY_TREE = auto()  # OS accessibility API (structured)
    UI_GRAPH = auto()            # GPA-style: detected elements + spatial graph
    HYBRID = auto()              # Accessibility tree primary, screenshot fallback


@dataclass(frozen=True)
class BoundingBox:
    """Normalized bounding box [0, 1] relative to screen/window dimensions."""
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def contains_point(self, px: float, py: float) -> bool:
        return (self.x <= px <= self.x + self.width and
                self.y <= py <= self.y + self.height)


@dataclass
class UIElement:
    """A single UI element as perceived by the system.

    This is the atom of perception. Different perception modes populate
    different subsets of fields — the eval measures which fields matter
    for grounding accuracy.
    """
    element_id: str
    role: str                             # button, input, link, text, icon, etc.
    bbox: BoundingBox
    text: Optional[str] = None            # OCR or accessibility label
    name: Optional[str] = None            # Accessibility name
    value: Optional[str] = None           # Current value (for inputs)
    state: Optional[dict[str, bool]] = None  # enabled, focused, checked, etc.
    icon_embedding: Optional[list[float]] = None  # Visual embedding (GPA-style)
    text_embedding: Optional[list[float]] = None  # Text embedding
    confidence: float = 1.0               # Detection confidence
    source: PerceptionMode = PerceptionMode.SCREENSHOT


@dataclass
class UIGraph:
    """GPA-style UI graph: elements as nodes, spatial proximity as edges.

    This is the core data structure from the GPA paper. Each node stores
    visual + textual features; edges connect spatially nearby elements.
    The graph enables geometric matching even when individual elements
    change appearance.
    """
    elements: list[UIElement]
    edges: list[tuple[str, str]]          # (element_id, element_id) pairs
    window_bounds: Optional[BoundingBox] = None
    scale_factor: float = 1.0
    timestamp: float = field(default_factory=time.time)

    def neighbors(self, element_id: str, k: int = 8) -> list[UIElement]:
        """Get k-nearest neighbors for an element (the 'context nodes' in GPA)."""
        neighbor_ids = set()
        for a, b in self.edges:
            if a == element_id:
                neighbor_ids.add(b)
            elif b == element_id:
                neighbor_ids.add(a)
        id_to_el = {e.element_id: e for e in self.elements}
        return [id_to_el[nid] for nid in list(neighbor_ids)[:k] if nid in id_to_el]


@dataclass
class ScreenState:
    """Complete perception state at a single point in time.

    Captures everything the system can see, from multiple perception modes.
    The eval compares strategies by giving them the same ScreenState and
    measuring which fields they use and how well they ground actions.
    """
    screenshot_path: Optional[Path] = None
    screenshot_base64: Optional[str] = None
    accessibility_tree: Optional[dict[str, Any]] = None
    ui_graph: Optional[UIGraph] = None
    active_app: Optional[str] = None
    window_title: Optional[str] = None
    screen_resolution: Optional[tuple[int, int]] = None
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Actions: what the system can do
# ---------------------------------------------------------------------------

class ActionType(Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE_TEXT = "type_text"
    PRESS_KEY = "press_key"
    HOTKEY = "hotkey"
    SCROLL = "scroll"
    DRAG = "drag"
    WAIT = "wait"
    NAVIGATE = "navigate"       # Browser URL navigation
    SELECT_OPTION = "select"    # Dropdown selection
    ASSERT = "assert"           # Verification step (no UI action)


@dataclass
class Action:
    """A single action to perform.

    Intentionally separates WHAT to do from WHERE to do it.
    'target' is a grounding result — could be coordinates, element ref, or selector.
    """
    action_type: ActionType
    target: Optional[UIElement] = None
    coordinates: Optional[tuple[float, float]] = None  # Normalized (0-1)
    text: Optional[str] = None
    key: Optional[str] = None
    selector: Optional[str] = None    # CSS/XPath fallback for browser
    scroll_delta: Optional[tuple[int, int]] = None
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Workflow: the sequence of steps that define a task
# ---------------------------------------------------------------------------

@dataclass
class WorkflowStep:
    """A single step in a recorded/defined workflow.

    Captures both the action AND the expected state before/after,
    enabling the eval to measure whether the system correctly identifies
    when it's ready to act (GPA's "readiness checking" concept).
    """
    step_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    step_number: int = 0
    description: str = ""                          # Human-readable intent
    intent_category: Optional[str] = None          # navigation, data_entry, verification, etc.
    action: Optional[Action] = None
    pre_state: Optional[ScreenState] = None        # Expected state before action
    post_state: Optional[ScreenState] = None       # Expected state after action
    target_graph: Optional[UIGraph] = None         # GPA-style: stored subgraph for matching
    variables: dict[str, str] = field(default_factory=dict)
    risk_level: str = "low"                        # low, medium, high, critical
    timeout_seconds: float = 30.0
    retry_budget: int = 3


@dataclass
class Workflow:
    """A complete workflow definition.

    Can be created by recording (demo phase) or manually defined
    for eval purposes. The workflow is the unit of evaluation.
    """
    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    target_url: Optional[str] = None               # Starting URL for browser workflows
    target_app: Optional[str] = None               # Starting app for desktop workflows
    platform: str = "browser"                       # browser, macos, windows, linux
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Grounding: how the system locates elements
# ---------------------------------------------------------------------------

class GroundingStrategy(Enum):
    """Which method to use for locating UI elements at runtime.

    This is the second key eval variable. GPA shows graph matching
    can achieve 100% success; the question is whether that holds
    across diverse real-world UIs.
    """
    GRAPH_MATCH = auto()       # GPA-style: SMC over UI graph
    LLM_VISION = auto()        # Send screenshot to VLM, ask for coordinates
    LLM_STRUCTURED = auto()    # Send a11y tree to LLM, ask for element
    SELECTOR = auto()          # Traditional CSS/XPath (baseline)
    HYBRID_GRAPH_LLM = auto()  # Graph match first, LLM fallback on low confidence


@dataclass
class GroundingResult:
    """Result of attempting to locate a target element.

    Captures enough metadata to diagnose WHY grounding fails,
    not just whether it did.
    """
    success: bool
    target_element: Optional[UIElement] = None
    coordinates: Optional[tuple[float, float]] = None
    confidence: float = 0.0
    strategy_used: Optional[GroundingStrategy] = None
    candidates_considered: int = 0
    ambiguity_score: float = 0.0  # GPA's entropy-based ambiguity (0=clear, 1=ambiguous)
    latency_ms: float = 0.0
    fallback_used: bool = False
    failure_reason: Optional[str] = None
    debug_info: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Execution results and failure taxonomy
# ---------------------------------------------------------------------------

class StepOutcome(Enum):
    """What happened when we tried to execute a step.

    The taxonomy matters: different failure modes have different
    implications for product design.
    """
    SUCCESS = "success"
    GROUNDING_FAILURE = "grounding_failure"          # Couldn't find element
    GROUNDING_AMBIGUOUS = "grounding_ambiguous"      # Found multiple candidates
    GROUNDING_WRONG = "grounding_wrong"              # Found wrong element
    READINESS_TIMEOUT = "readiness_timeout"          # Screen not in expected state
    ACTION_FAILED = "action_failed"                  # Element found but action failed
    STATE_MISMATCH = "state_mismatch"                # Post-action state unexpected
    NAVIGATION_ERROR = "navigation_error"            # Wrong page/app
    AUTH_BLOCKED = "auth_blocked"                    # SSO/MFA/CAPTCHA blocked progress
    ANTI_BOT_BLOCKED = "anti_bot_blocked"            # Anti-automation detection
    TIMING_ERROR = "timing_error"                    # Race condition / animation
    HEALED = "healed"                                # Failed initially, AI repaired it
    SKIPPED = "skipped"                              # Step not applicable (e.g., scroll-to-find)


@dataclass
class StepResult:
    """Complete result of executing one workflow step.

    Captures timing, cost, and diagnostic data for every step.
    This granularity is essential for discovering unknown-unknowns.
    """
    step: WorkflowStep
    outcome: StepOutcome
    grounding_result: Optional[GroundingResult] = None
    actual_action: Optional[Action] = None
    pre_state_match: bool = True
    post_state_match: bool = True
    screen_before: Optional[ScreenState] = None
    screen_after: Optional[ScreenState] = None
    latency_ms: float = 0.0
    model_calls: int = 0
    model_tokens_in: int = 0
    model_tokens_out: int = 0
    model_cost_usd: float = 0.0
    retries: int = 0
    healing_attempted: bool = False
    healing_succeeded: bool = False
    error_message: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class WorkflowResult:
    """Complete result of executing an entire workflow."""
    workflow: Workflow
    step_results: list[StepResult]
    success: bool = False
    total_latency_ms: float = 0.0
    total_model_cost_usd: float = 0.0
    total_model_calls: int = 0
    failure_step: Optional[int] = None
    failure_reason: Optional[str] = None
    environment: dict[str, Any] = field(default_factory=dict)

    @property
    def step_success_rate(self) -> float:
        if not self.step_results:
            return 0.0
        successes = sum(1 for r in self.step_results
                        if r.outcome in (StepOutcome.SUCCESS, StepOutcome.HEALED, StepOutcome.SKIPPED))
        return successes / len(self.step_results)

    @property
    def healing_rate(self) -> float:
        healed = [r for r in self.step_results if r.healing_attempted]
        if not healed:
            return 0.0
        return sum(1 for r in healed if r.healing_succeeded) / len(healed)

    @property
    def failure_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for r in self.step_results:
            if r.outcome not in (StepOutcome.SUCCESS, StepOutcome.SKIPPED):
                dist[r.outcome.value] = dist.get(r.outcome.value, 0) + 1
        return dist


# ---------------------------------------------------------------------------
# Eval configuration: what experiment are we running?
# ---------------------------------------------------------------------------

@dataclass
class EvalConfig:
    """Configuration for a single eval run.

    Each config represents one point in the experiment space:
    (perception_mode × grounding_strategy × model × workflow).
    """
    eval_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    perception_mode: PerceptionMode = PerceptionMode.HYBRID
    grounding_strategy: GroundingStrategy = GroundingStrategy.HYBRID_GRAPH_LLM
    model_provider: str = "anthropic"      # anthropic, openai
    model_name: str = "claude-sonnet-4-20250514"
    workflows: list[str] = field(default_factory=list)  # Workflow IDs to run
    max_retries_per_step: int = 3
    screenshot_resolution: tuple[int, int] = (1920, 1080)
    headless: bool = False
    record_screenshots: bool = True        # Save screenshots for debugging
    record_accessibility_trees: bool = True
    output_dir: Path = Path("./eval_results")
    timeout_seconds: float = 300.0         # Per-workflow timeout
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalSuite:
    """A collection of eval configs to run as a batch.

    Designed to support factorial experiments: vary one dimension
    while holding others constant.
    """
    suite_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    configs: list[EvalConfig] = field(default_factory=list)
    description: str = ""
