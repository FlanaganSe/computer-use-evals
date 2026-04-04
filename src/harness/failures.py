"""Failure taxonomy for classifying why agent trials fail."""

from enum import StrEnum


class FailureCategory(StrEnum):
    """Why a trial failed. Categories are mutually exclusive per trial."""

    PERCEPTION = "perception"
    """Agent misread the screen or structured state."""

    PLANNING = "planning"
    """Agent chose the wrong sequence of actions."""

    EXECUTION = "execution"
    """Agent's action didn't produce the intended effect (e.g., misclick)."""

    CONTEXT = "context"
    """Agent lost track of state over a long horizon."""

    ENVIRONMENT = "environment"
    """Host, browser, or app state caused the failure (not the agent)."""

    TOOL_CHOICE = "tool_choice"
    """Agent used a suboptimal tool or action type when better options existed."""

    HARNESS = "harness"
    """Bug or limitation in the eval harness itself."""
