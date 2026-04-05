"""Typed runtime result contract for action execution outcomes.

Replaces the stringly-typed str return from Environment.execute_action()
with a structured model that captures status, execution method, and
state-change evidence. Designed to be filled incrementally: Milestone 2
establishes the contract; later milestones populate state_changed and
expected_change_observed fields.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ResultStatus(StrEnum):
    """Outcome status for an executed action."""

    OK = "ok"
    ERROR = "error"
    NO_OP = "no_op"
    DONE = "done"
    FAIL = "fail"


class ExecutionMethod(StrEnum):
    """How the action was physically executed."""

    COORDINATES = "coordinates"
    AX_PRESS = "ax_press"
    KEYBOARD = "keyboard"
    SHELL = "shell"
    WAIT = "wait"
    SELECTOR = "selector"
    OTHER = "other"


class RuntimeResult:
    """Structured outcome of a single action execution.

    Attributes:
        status: Overall outcome of the action.
        message: Compact human-readable description of what happened.
        execution_method: How the action was physically performed.
        target_resolved: Whether the target element was found/resolved.
        state_changed: Whether the UI state changed after the action.
            None when unknown (default in Milestone 2).
        expected_change_observed: Whether the expected change was detected.
            None when unknown (default in Milestone 2).
        metadata: Arbitrary key-value pairs for environment-specific detail.
    """

    __slots__ = (
        "status",
        "message",
        "execution_method",
        "target_resolved",
        "state_changed",
        "expected_change_observed",
        "metadata",
    )

    def __init__(
        self,
        *,
        status: ResultStatus,
        message: str = "",
        execution_method: ExecutionMethod = ExecutionMethod.OTHER,
        target_resolved: bool = True,
        state_changed: bool | None = None,
        expected_change_observed: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.message = message
        self.execution_method = execution_method
        self.target_resolved = target_resolved
        self.state_changed = state_changed
        self.expected_change_observed = expected_change_observed
        self.metadata = metadata or {}

    @property
    def summary(self) -> str:
        """Compact human-readable summary for trace and report display.

        Preserves the style of the old string contract:
        - "ok" for simple success
        - "ok:downloaded:file.txt" for success with detail
        - "error:reason" for errors
        - "done" / "fail:reason" for terminal states
        """
        if self.status == ResultStatus.OK:
            return f"ok:{self.message}" if self.message else "ok"
        if self.status == ResultStatus.ERROR:
            return f"error:{self.message}" if self.message else "error"
        if self.status == ResultStatus.FAIL:
            return f"fail:{self.message}" if self.message else "fail"
        if self.status == ResultStatus.DONE:
            return "done"
        if self.status == ResultStatus.NO_OP:
            return f"no_op:{self.message}" if self.message else "no_op"
        return str(self.status)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for trace persistence."""
        d: dict[str, Any] = {
            "status": self.status.value,
            "message": self.message,
            "execution_method": self.execution_method.value,
            "target_resolved": self.target_resolved,
        }
        if self.state_changed is not None:
            d["state_changed"] = self.state_changed
        if self.expected_change_observed is not None:
            d["expected_change_observed"] = self.expected_change_observed
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def __repr__(self) -> str:
        return f"RuntimeResult(status={self.status!r}, message={self.message!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RuntimeResult):
            return NotImplemented
        return (
            self.status == other.status
            and self.message == other.message
            and self.execution_method == other.execution_method
            and self.target_resolved == other.target_resolved
            and self.state_changed == other.state_changed
            and self.expected_change_observed == other.expected_change_observed
            and self.metadata == other.metadata
        )


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def ok(
    message: str = "",
    *,
    method: ExecutionMethod = ExecutionMethod.OTHER,
    target_resolved: bool = True,
    state_changed: bool | None = None,
    expected_change_observed: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeResult:
    """Create an OK result."""
    return RuntimeResult(
        status=ResultStatus.OK,
        message=message,
        execution_method=method,
        target_resolved=target_resolved,
        state_changed=state_changed,
        expected_change_observed=expected_change_observed,
        metadata=metadata,
    )


def error(
    message: str,
    *,
    method: ExecutionMethod = ExecutionMethod.OTHER,
    target_resolved: bool = False,
    state_changed: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeResult:
    """Create an error result."""
    return RuntimeResult(
        status=ResultStatus.ERROR,
        message=message,
        execution_method=method,
        target_resolved=target_resolved,
        state_changed=state_changed,
        metadata=metadata,
    )


def done() -> RuntimeResult:
    """Create a done (task complete) result."""
    return RuntimeResult(status=ResultStatus.DONE)


def fail(reason: str = "Agent declared failure") -> RuntimeResult:
    """Create a fail result."""
    return RuntimeResult(status=ResultStatus.FAIL, message=reason)
