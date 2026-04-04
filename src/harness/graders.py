"""Programmatic graders for verifying task outcomes."""

from __future__ import annotations

from pathlib import Path

from harness.types import GraderResult, Task, VerificationCheck


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
