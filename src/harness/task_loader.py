"""Load and validate task definitions from YAML files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from harness.types import Task


def load_task(path: str | Path, overrides: dict[str, str] | None = None) -> Task:
    """Load a task YAML, validate it, and substitute variables.

    Args:
        path: Path to the task YAML file.
        overrides: Optional variable overrides (name -> value).

    Returns:
        A validated Task with variables substituted into goal.description
        and verification checks.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        msg = f"Task file must contain a YAML mapping, got {type(raw).__name__}"
        raise ValueError(msg)

    task = Task.model_validate(raw)
    return _substitute_variables(task, overrides or {})


def _resolve_variables(task: Task, overrides: dict[str, str]) -> dict[str, str]:
    """Build the final variable map from defaults + overrides."""
    variables: dict[str, str] = {}
    for name, var in task.goal.variables.items():
        variables[name] = overrides.get(name, var.default)
    return variables


def _substitute(text: str, variables: dict[str, str]) -> str:
    """Replace {{var}} placeholders in text."""

    def replacer(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return variables.get(key, match.group(0))

    return re.sub(r"\{\{(\w+)\}\}", replacer, text)


def _substitute_in_dict(d: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    """Recursively substitute variables in a dict."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = _substitute(v, variables)
        elif isinstance(v, dict):
            result[k] = _substitute_in_dict(v, variables)
        elif isinstance(v, list):
            result[k] = [
                _substitute(item, variables) if isinstance(item, str) else item for item in v
            ]
        else:
            result[k] = v
    return result


def _substitute_variables(task: Task, overrides: dict[str, str]) -> Task:
    """Return a new Task with all {{var}} placeholders resolved."""
    variables = _resolve_variables(task, overrides)
    if not variables:
        return task

    data = task.model_dump(by_alias=True)
    substituted = _substitute_in_dict(data, variables)
    return Task.model_validate(substituted)
