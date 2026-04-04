"""Core data models and adapter protocol for the eval harness."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from harness.failures import FailureCategory

# ---------------------------------------------------------------------------
# Observation types — what an adapter can request from the environment
# ---------------------------------------------------------------------------


class ObservationType(StrEnum):
    """What kind of observation an adapter needs each step."""

    NONE = "none"
    """No observation needed (deterministic adapters)."""

    SCREENSHOT = "screenshot"
    """PNG screenshot bytes."""

    ARIA_STATE = "aria_state"
    """Serialized ARIA / accessibility snapshot."""

    SCREENSHOT_AND_ARIA = "screenshot_and_aria"
    """Both screenshot and ARIA state."""


class Observation(BaseModel):
    """Environment observation delivered to an adapter."""

    observation_type: ObservationType
    screenshot: bytes | None = None
    aria_snapshot: str | None = None
    url: str | None = None
    page_title: str | None = None
    focused_app: str | None = None
    a11y_available: bool | None = None


# ---------------------------------------------------------------------------
# Action types — what an adapter returns for the environment to execute
# ---------------------------------------------------------------------------


class ActionType(StrEnum):
    GOTO = "goto"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    TYPE = "type"
    PRESS = "press"
    SCROLL = "scroll"
    WAIT = "wait"
    DRAG = "drag"
    MOVE = "move"
    SCREENSHOT = "screenshot"
    SHELL = "shell"
    DONE = "done"
    FAIL = "fail"


class Action(BaseModel):
    """A single action for the environment to execute.

    Uses a flat dict for params so the schema accommodates selector-based,
    pixel-coordinate, and semantic-locator actions without separate subclasses.
    """

    action_type: ActionType = Field(alias="type")
    params: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Task definition (loaded from YAML)
# ---------------------------------------------------------------------------


class TaskVariable(BaseModel):
    type: str
    default: str


class TaskGoal(BaseModel):
    description: str
    variables: dict[str, TaskVariable] = Field(default_factory=dict)


class VerificationCheck(BaseModel):
    method: Literal["programmatic", "llm_judge"]
    check: str | None = None
    prompt: str | None = None
    threshold: float | None = None


class TaskVerification(BaseModel):
    primary: VerificationCheck
    fallback: VerificationCheck | None = None


class Task(BaseModel):
    """A normalized task definition loaded from YAML."""

    task_id: str
    version: str
    goal: TaskGoal
    preconditions: list[str] = Field(default_factory=list)
    setup_script: str | None = None
    verification: TaskVerification
    cleanup_script: str | None = None
    environment: str | None = None


# ---------------------------------------------------------------------------
# Trace and step records (written to trace.json)
# ---------------------------------------------------------------------------


class StepRecord(BaseModel):
    """One step in a trial trace."""

    step: int
    action: dict[str, Any]
    result: str
    error: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class Trace(BaseModel):
    """Full trace of a trial run."""

    task_id: str
    adapter: str
    started_at: datetime
    completed_at: datetime | None = None
    steps: list[StepRecord] = Field(default_factory=list)
    outcome: Literal["pass", "fail", "error"] = "error"
    failure_category: FailureCategory | None = None
    total_steps: int = 0
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Grader result
# ---------------------------------------------------------------------------


class GraderResult(BaseModel):
    """Result of grading a trial."""

    passed: bool
    method: str
    explanation: str
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


class Report(BaseModel):
    """Summary report for a trial run."""

    task_id: str
    adapter: str
    outcome: Literal["pass", "fail", "error"]
    failure_category: FailureCategory | None = None
    total_steps: int
    started_at: datetime
    completed_at: datetime | None = None
    grader_result: GraderResult
    run_dir: str


# ---------------------------------------------------------------------------
# Run config
# ---------------------------------------------------------------------------


class RunConfig(BaseModel):
    """Configuration for a harness run."""

    adapter: str
    tasks: list[str] = Field(default_factory=list)
    max_steps: int = 30
    trial_count: int = 1


# ---------------------------------------------------------------------------
# Adapter protocol — the contract between runner and adapters
# ---------------------------------------------------------------------------


@runtime_checkable
class Adapter(Protocol):
    """Protocol that all adapters must satisfy.

    The runner calls observation_request() to know what to collect from the
    environment, then passes the result to decide() which returns actions.

    - Deterministic adapter: requests NONE, ignores observation, returns
      a hardcoded action sequence.
    - OpenAI CU adapter (M2): requests SCREENSHOT, receives PNG bytes,
      returns pixel-coordinate actions.
    - Codex adapter (M3): requests ARIA_STATE, receives serialized state,
      returns semantic-locator actions.
    """

    @property
    def name(self) -> str: ...

    def observation_request(self) -> ObservationType: ...

    def decide(self, observation: Observation, task: Task) -> list[Action]: ...

    def reset(self) -> None:
        """Reset adapter state for a new trial."""
        ...


# ---------------------------------------------------------------------------
# Environment protocol — browser, desktop, etc.
# ---------------------------------------------------------------------------


@runtime_checkable
class Environment(Protocol):
    """Protocol for execution environments (browser, desktop, etc.)."""

    async def setup(self, task: Task, run_dir: Path) -> None: ...

    async def collect_observation(self, observation_type: ObservationType) -> Observation: ...

    async def execute_action(self, action: Action) -> str: ...

    async def teardown(self) -> None: ...
