"""Programmatic and LLM-based graders for verifying task outcomes."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from harness.types import GraderResult, MilestoneResult, Task, VerificationCheck

logger = logging.getLogger(__name__)

FORM_SUBMISSION_PATH = Path("/tmp/harness_form_submission.json")

# Model for llm_judge grading
_LLM_JUDGE_MODEL = "gpt-4.1-mini"


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

    if check.method == "llm_judge":
        return _grade_llm_judge(check, task, run_dir)

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

    if expr.startswith("file_contains("):
        return _check_file_contains(expr)

    if expr.startswith("form_submitted("):
        return _check_form_submitted(expr)

    if expr.startswith("app_focused("):
        return _check_app_focused(expr)

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


def _check_file_contains(expr: str) -> GraderResult:
    """Check whether a file at an absolute path contains expected text.

    Expects: file_contains('/path/to/file', 'expected text')
    """
    inner = expr.removeprefix("file_contains(").removesuffix(")")
    parts = _parse_quoted_args(inner)
    if len(parts) != 2:
        return GraderResult(
            passed=False,
            method="file_contains",
            explanation=f"Expected 2 arguments (path, text), got {len(parts)}",
        )

    file_path_str, expected_text = parts
    target = Path(file_path_str)

    if not target.exists():
        return GraderResult(
            passed=False,
            method="file_contains",
            explanation=f"File not found: {target}",
            details={"expected_path": str(target)},
        )

    try:
        actual = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return GraderResult(
            passed=False,
            method="file_contains",
            explanation=f"Could not read file: {exc}",
            details={"path": str(target)},
        )

    if expected_text in actual:
        return GraderResult(
            passed=True,
            method="file_contains",
            explanation="File contains expected text",
            details={"path": str(target), "size_bytes": target.stat().st_size},
        )

    return GraderResult(
        passed=False,
        method="file_contains",
        explanation=f"File does not contain expected text: {expected_text!r}",
        details={
            "path": str(target),
            "expected_text": expected_text,
            "actual_length": len(actual),
        },
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


def _check_app_focused(expr: str) -> GraderResult:
    """Check whether the specified app is the frontmost application.

    Expects: app_focused('AppName')
    """
    inner = expr.removeprefix("app_focused(").removesuffix(")")
    app_name = inner.strip("'\"")

    try:
        import Quartz  # type: ignore[import-untyped]

        workspace = Quartz.NSWorkspace.sharedWorkspace()
        front_app = workspace.frontmostApplication()
        actual_name: str = front_app.localizedName()

        if actual_name == app_name:
            return GraderResult(
                passed=True,
                method="app_focused",
                explanation=f"{app_name} is the focused app",
            )
        return GraderResult(
            passed=False,
            method="app_focused",
            explanation=f"Expected {app_name}, but {actual_name} is focused",
            details={"expected": app_name, "actual": actual_name},
        )
    except ImportError:
        return GraderResult(
            passed=False,
            method="app_focused",
            explanation="Quartz not available — cannot check focused app",
        )


# ---------------------------------------------------------------------------
# Milestone evaluation
# ---------------------------------------------------------------------------


def evaluate_milestones(task: Task, run_dir: Path) -> list[MilestoneResult]:
    """Evaluate all milestones defined on a task.

    Returns a MilestoneResult for each milestone, in definition order.
    Milestones are evaluated independently — a later milestone can pass
    even if an earlier one failed.
    """
    results: list[MilestoneResult] = []
    for m in task.milestones:
        result = _evaluate_single_milestone(m.check, task, run_dir)
        results.append(
            MilestoneResult(id=m.id, passed=result.passed, explanation=result.explanation)
        )
    return results


def _evaluate_single_milestone(
    check: VerificationCheck, task: Task, run_dir: Path
) -> GraderResult:
    """Evaluate a single milestone check using existing grader infrastructure."""
    if check.method == "programmatic":
        if check.check is None:
            return GraderResult(
                passed=False, method="programmatic", explanation="No check expression"
            )
        return _eval_check(check, run_dir)

    if check.method == "llm_judge":
        return _grade_llm_judge(check, task, run_dir)

    # ax_contains and other methods — not yet implemented
    return GraderResult(
        passed=False,
        method=check.method,
        explanation=f"Milestone check method '{check.method}' not implemented yet",
    )


# ---------------------------------------------------------------------------
# LLM judge grader
# ---------------------------------------------------------------------------


def _grade_llm_judge(check: VerificationCheck, task: Task, run_dir: Path) -> GraderResult:
    """Grade a task using an LLM judge.

    Sends the task description and check prompt to a cheap model and asks
    for a pass/fail judgment with explanation.
    """
    prompt = check.prompt
    if not prompt:
        prompt = f"Did the following task succeed? Task: {task.goal.description}"

    threshold = check.threshold or 0.5

    try:
        return _call_llm_judge(prompt, task, run_dir, threshold)
    except Exception as exc:
        logger.error("LLM judge failed: %s", exc)
        return GraderResult(
            passed=False,
            method="llm_judge",
            explanation=f"LLM judge error: {exc}",
        )


def _call_llm_judge(prompt: str, task: Task, run_dir: Path, threshold: float) -> GraderResult:
    """Make the actual LLM judge API call."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return GraderResult(
            passed=False,
            method="llm_judge",
            explanation="OPENAI_API_KEY not set — cannot run LLM judge",
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    # Build context from available artifacts
    context_parts = [f"Task: {task.goal.description}", f"Evaluation prompt: {prompt}"]

    # Include trace if available
    trace_path = run_dir / "trace.json"
    if trace_path.exists():
        trace_text = trace_path.read_text()[:2000]
        context_parts.append(f"Run trace (truncated):\n{trace_text}")

    response = client.chat.completions.create(
        model=_LLM_JUDGE_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an evaluation judge. Given a task description and "
                    "evaluation criteria, determine if the task succeeded. "
                    "Respond with a JSON object: "
                    '{"passed": true/false, "confidence": 0.0-1.0, "explanation": "..."}'
                ),
            },
            {"role": "user", "content": "\n\n".join(context_parts)},
        ],
        temperature=0,
        max_completion_tokens=256,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or "{}"
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return GraderResult(
            passed=False,
            method="llm_judge",
            explanation=f"Could not parse LLM judge response: {raw[:200]}",
        )

    passed = bool(result.get("passed", False))
    confidence = float(result.get("confidence", 0.0))
    explanation = str(result.get("explanation", "No explanation"))

    # Apply threshold
    if passed and confidence < threshold:
        passed = False
        explanation = f"Passed but below confidence threshold ({confidence:.2f} < {threshold:.2f}): {explanation}"

    return GraderResult(
        passed=passed,
        method="llm_judge",
        explanation=explanation,
        details={
            "confidence": confidence,
            "threshold": threshold,
            "model": _LLM_JUDGE_MODEL,
        },
    )
