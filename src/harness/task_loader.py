"""Load and validate task definitions from YAML files."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from harness.types import Task, VerificationCheck

logger = logging.getLogger(__name__)

# Supported programmatic check function names.
_SUPPORTED_CHECKS = frozenset(
    {
        "file_exists",
        "file_contains",
        "form_submitted",
        "app_focused",
        "script_check",
    }
)


def validate_check_expression(check: VerificationCheck) -> list[str]:
    """Validate a verification check expression. Returns a list of warnings.

    Raises ValueError for unsupported/invalid check expressions that would
    silently produce misleading results.
    """
    warnings: list[str] = []

    if check.method == "programmatic" and check.check is not None:
        expr = check.check

        # Reject boolean operators — these are not supported by the expression evaluator
        if " and " in expr or " or " in expr or expr.startswith("not "):
            msg = (
                f"Unsupported boolean expression in check: {expr!r}. "
                "Only single function calls are supported "
                "(e.g., file_contains('path', 'text'))."
            )
            raise ValueError(msg)

        # Warn about unknown function names
        func_match = re.match(r"(\w+)\(", expr)
        if func_match:
            func_name = func_match.group(1)
            if func_name not in _SUPPORTED_CHECKS:
                warnings.append(
                    f"Unknown check function '{func_name}' in expression: {expr!r}. "
                    f"Supported: {sorted(_SUPPORTED_CHECKS)}"
                )

    if check.method == "llm_judge" and not check.prompt:
        warnings.append("llm_judge check has no prompt — grading may produce poor results")

    return warnings


def load_task(
    path: str | Path,
    overrides: dict[str, str] | None = None,
    *,
    strict: bool = True,
) -> Task:
    """Load a task YAML, validate it, and substitute variables.

    Args:
        path: Path to the task YAML file.
        overrides: Optional variable overrides (name -> value).
        strict: If True (default), raise ValueError for unsupported check
            expressions. If False, log warnings instead.

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
    task = _substitute_variables(task, overrides or {})

    # Validate check expressions
    all_checks = [task.verification.primary]
    if task.verification.fallback:
        all_checks.append(task.verification.fallback)
    for milestone in task.milestones:
        all_checks.append(milestone.check)

    for check in all_checks:
        warnings = validate_check_expression(check)
        for w in warnings:
            if strict:
                raise ValueError(w)
            logger.warning("Task %s: %s", task.task_id, w)

    return task


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
                _substitute(item, variables)
                if isinstance(item, str)
                else _substitute_in_dict(item, variables)
                if isinstance(item, dict)
                else item
                for item in v
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
