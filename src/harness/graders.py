"""Programmatic graders for verifying task outcomes."""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness.types import GraderResult, Task, VerificationCheck

FORM_SUBMISSION_PATH = Path("/tmp/harness_form_submission.json")


def _parse_quoted_args(s: str) -> list[str]:
    """Parse comma-separated quoted arguments, handling commas inside quotes.

    Examples:
        "'Jane Doe', 'jane@example.com'" -> ["Jane Doe", "jane@example.com"]
        "'Smith, Jane', 'j@x.com'" -> ["Smith, Jane", "j@x.com"]
    """
    return re.findall(r"""['"]([^'"]*?)['"]""", s)


def grade(task: Task, run_dir: Path) -> GraderResult:
    """Run the primary verification check for a task."""
    check = task.verification.primary
    if check.method != "programmatic":
        return GraderResult(
            passed=False,
            method=check.method,
            explanation=f"Grading method '{check.method}' not implemented yet",
        )

    if check.check is None:
        return GraderResult(
            passed=False,
            method="programmatic",
            explanation="No check expression specified",
        )

    return _eval_check(check, run_dir)


def _eval_check(check: VerificationCheck, run_dir: Path) -> GraderResult:
    """Evaluate a programmatic check expression."""
    expr = check.check
    if expr is None:
        return GraderResult(
            passed=False,
            method="programmatic",
            explanation="No check expression",
        )

    if expr.startswith("file_exists("):
        return _check_file_exists(expr, run_dir)

    if expr.startswith("form_submitted("):
        return _check_form_submitted(expr)

    return GraderResult(
        passed=False,
        method="programmatic",
        explanation=f"Unknown check expression: {expr}",
    )


def _check_file_exists(expr: str, run_dir: Path) -> GraderResult:
    """Check whether an expected file exists in the artifacts directory."""
    # Parse file_exists('path') or file_exists("path")
    inner = expr.removeprefix("file_exists(").removesuffix(")")
    file_path = inner.strip("'\"")

    artifacts_dir = run_dir / "artifacts"
    target = artifacts_dir / file_path

    if target.exists():
        return GraderResult(
            passed=True,
            method="file_exists",
            explanation=f"File found: {target}",
            details={"path": str(target), "size_bytes": target.stat().st_size},
        )

    return GraderResult(
        passed=False,
        method="file_exists",
        explanation=f"Expected file not found: {target}",
        details={"expected_path": str(target)},
    )


def _check_form_submitted(expr: str) -> GraderResult:
    """Check whether a form was submitted with expected field values.

    Expects: form_submitted('expected_name', 'expected_email')
    """
    inner = expr.removeprefix("form_submitted(").removesuffix(")")
    # Parse two quoted arguments: form_submitted('name', 'email')
    # Split on "', '" or "', \"" to handle commas inside values
    parts = _parse_quoted_args(inner)
    if len(parts) != 2:
        return GraderResult(
            passed=False,
            method="form_submitted",
            explanation=f"Expected 2 arguments (name, email), got {len(parts)}",
        )

    expected_name, expected_email = parts

    if not FORM_SUBMISSION_PATH.exists():
        return GraderResult(
            passed=False,
            method="form_submitted",
            explanation=f"Submission file not found: {FORM_SUBMISSION_PATH}",
        )

    submission = json.loads(FORM_SUBMISSION_PATH.read_text())
    actual_name = submission.get("name", "")
    actual_email = submission.get("email", "")

    if actual_name == expected_name and actual_email == expected_email:
        return GraderResult(
            passed=True,
            method="form_submitted",
            explanation="Form submitted with correct values",
            details={"name": actual_name, "email": actual_email},
        )

    return GraderResult(
        passed=False,
        method="form_submitted",
        explanation=(
            f"Form values mismatch: "
            f"name={actual_name!r} (expected {expected_name!r}), "
            f"email={actual_email!r} (expected {expected_email!r})"
        ),
        details={
            "expected": {"name": expected_name, "email": expected_email},
            "actual": {"name": actual_name, "email": actual_email},
        },
    )
