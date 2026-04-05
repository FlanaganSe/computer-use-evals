"""Tests for milestone evaluation, trace persistence, and report rendering."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from harness.graders import evaluate_milestones
from harness.reporting import (
    generate_comparison_report,
    generate_detailed_report,
    generate_report,
)
from harness.failures import FailureCategory
from harness.types import (
    GraderResult,
    Milestone,
    MilestoneResult,
    StepRecord,
    Task,
    TaskGoal,
    TaskVerification,
    Trace,
    VerificationCheck,
)


def _make_task_with_milestones(
    milestones: list[Milestone] | None = None,
) -> Task:
    return Task(
        task_id="desktop-textedit-save",
        version="2.0",
        goal=TaskGoal(description="Open TextEdit and save a file"),
        verification=TaskVerification(
            primary=VerificationCheck(
                method="programmatic",
                check="file_contains('/tmp/test.txt', 'hello')",
            ),
        ),
        milestones=milestones or [],
    )


def _make_trace(
    milestone_results: list[MilestoneResult] | None = None,
    outcome: str = "fail",
    failure_category: FailureCategory | None = None,
) -> Trace:
    return Trace(
        task_id="desktop-textedit-save",
        adapter="structured_state_desktop",
        started_at=datetime(2026, 4, 1, tzinfo=UTC),
        completed_at=datetime(2026, 4, 1, 0, 1, tzinfo=UTC),
        outcome=outcome,
        total_steps=5,
        steps=[
            StepRecord(step=1, action={"type": "click", "target": "ax_001"}, result="ok"),
            StepRecord(step=2, action={"type": "type", "text": "hello"}, result="ok"),
            StepRecord(step=3, action={"type": "done"}, result="done"),
        ],
        milestone_results=milestone_results or [],
        failure_category=failure_category,
    )


# ---------------------------------------------------------------------------
# Milestone evaluation
# ---------------------------------------------------------------------------


class TestEvaluateMilestones:
    def test_no_milestones(self, tmp_path: Path) -> None:
        task = _make_task_with_milestones(milestones=[])
        results = evaluate_milestones(task, tmp_path)
        assert results == []

    def test_programmatic_milestone_passes(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("hello world")

        task = _make_task_with_milestones(
            milestones=[
                Milestone(
                    id="file_created",
                    description="File exists",
                    check=VerificationCheck(
                        method="programmatic",
                        check=f"file_contains('{target}', 'hello')",
                    ),
                )
            ]
        )
        results = evaluate_milestones(task, tmp_path)
        assert len(results) == 1
        assert results[0].id == "file_created"
        assert results[0].passed is True

    def test_programmatic_milestone_fails(self, tmp_path: Path) -> None:
        task = _make_task_with_milestones(
            milestones=[
                Milestone(
                    id="file_created",
                    description="File exists",
                    check=VerificationCheck(
                        method="programmatic",
                        check="file_contains('/nonexistent/path', 'hello')",
                    ),
                )
            ]
        )
        results = evaluate_milestones(task, tmp_path)
        assert len(results) == 1
        assert results[0].id == "file_created"
        assert results[0].passed is False

    def test_multiple_milestones_mixed(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("hello world")

        task = _make_task_with_milestones(
            milestones=[
                Milestone(
                    id="app_open",
                    description="App is open",
                    check=VerificationCheck(
                        method="programmatic",
                        check=f"file_contains('{target}', 'hello')",
                    ),
                ),
                Milestone(
                    id="content_typed",
                    description="Content typed",
                    check=VerificationCheck(
                        method="programmatic",
                        check="file_contains('/nonexistent', 'nope')",
                    ),
                ),
            ]
        )
        results = evaluate_milestones(task, tmp_path)
        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False

    def test_ax_contains_not_implemented(self, tmp_path: Path) -> None:
        task = _make_task_with_milestones(
            milestones=[
                Milestone(
                    id="content_visible",
                    description="Content visible in AX tree",
                    check=VerificationCheck(
                        method="ax_contains",
                        role="AXTextArea",
                        value="hello",
                    ),
                )
            ]
        )
        results = evaluate_milestones(task, tmp_path)
        assert len(results) == 1
        assert results[0].passed is False
        assert "not implemented" in results[0].explanation

    def test_milestone_no_check_expression(self, tmp_path: Path) -> None:
        task = _make_task_with_milestones(
            milestones=[
                Milestone(
                    id="empty_check",
                    description="No check expression",
                    check=VerificationCheck(method="programmatic"),
                )
            ]
        )
        results = evaluate_milestones(task, tmp_path)
        assert len(results) == 1
        assert results[0].passed is False


# ---------------------------------------------------------------------------
# Trace serialization with milestone results
# ---------------------------------------------------------------------------


class TestTraceMilestoneResults:
    def test_trace_serializes_milestone_results(self) -> None:
        trace = _make_trace(
            milestone_results=[
                MilestoneResult(id="m1", passed=True, explanation="OK"),
                MilestoneResult(id="m2", passed=False, explanation="Not found"),
            ]
        )
        data = json.loads(trace.model_dump_json())
        assert "milestone_results" in data
        assert len(data["milestone_results"]) == 2
        assert data["milestone_results"][0]["id"] == "m1"
        assert data["milestone_results"][0]["passed"] is True
        assert data["milestone_results"][1]["passed"] is False

    def test_trace_deserializes_milestone_results(self) -> None:
        trace = _make_trace(
            milestone_results=[
                MilestoneResult(id="m1", passed=True, explanation="OK"),
            ]
        )
        raw = trace.model_dump_json()
        restored = Trace.model_validate_json(raw)
        assert len(restored.milestone_results) == 1
        assert restored.milestone_results[0].id == "m1"
        assert restored.milestone_results[0].passed is True

    def test_trace_without_milestones_backward_compatible(self) -> None:
        # Simulate loading a v1 trace that has no milestone_results field
        data = {
            "task_id": "old-task",
            "adapter": "deterministic",
            "started_at": "2026-04-01T00:00:00+00:00",
            "outcome": "pass",
            "total_steps": 3,
            "steps": [],
        }
        trace = Trace.model_validate(data)
        assert trace.milestone_results == []


# ---------------------------------------------------------------------------
# Single-run report with milestones
# ---------------------------------------------------------------------------


class TestReportMilestones:
    def test_report_shows_milestone_pass_fail(self, tmp_path: Path) -> None:
        task = _make_task_with_milestones(
            milestones=[
                Milestone(
                    id="app_open",
                    description="App is open",
                    check=VerificationCheck(
                        method="programmatic", check="app_focused('TextEdit')"
                    ),
                ),
                Milestone(
                    id="file_saved",
                    description="File saved",
                    check=VerificationCheck(
                        method="programmatic",
                        check="file_contains('/tmp/test.txt', 'hello')",
                    ),
                ),
            ]
        )
        trace = _make_trace(
            milestone_results=[
                MilestoneResult(id="app_open", passed=True, explanation="TextEdit is focused"),
                MilestoneResult(id="file_saved", passed=False, explanation="File not found"),
            ]
        )
        grader_result = GraderResult(
            passed=False, method="file_contains", explanation="File not found"
        )
        report = generate_report(task, trace, grader_result, tmp_path)

        assert "[PASS] **app_open**" in report
        assert "[FAIL] **file_saved**" in report
        assert "**First failure at:** file_saved" in report

    def test_report_no_milestones_no_section(self, tmp_path: Path) -> None:
        task = _make_task_with_milestones(milestones=[])
        trace = _make_trace()
        grader_result = GraderResult(passed=True, method="programmatic", explanation="OK")
        report = generate_report(task, trace, grader_result, tmp_path)

        assert "## Milestones" not in report

    def test_report_all_milestones_pass(self, tmp_path: Path) -> None:
        task = _make_task_with_milestones(
            milestones=[
                Milestone(
                    id="m1",
                    description="First",
                    check=VerificationCheck(method="programmatic", check="app_focused('X')"),
                ),
            ]
        )
        trace = _make_trace(
            outcome="pass",
            milestone_results=[
                MilestoneResult(id="m1", passed=True, explanation="OK"),
            ],
        )
        grader_result = GraderResult(passed=True, method="programmatic", explanation="OK")
        report = generate_report(task, trace, grader_result, tmp_path)

        assert "[PASS] **m1**" in report
        assert "First failure at" not in report

    def test_report_milestones_without_results(self, tmp_path: Path) -> None:
        """Milestones defined but no results (e.g., legacy run)."""
        task = _make_task_with_milestones(
            milestones=[
                Milestone(
                    id="m1",
                    description="First",
                    check=VerificationCheck(method="programmatic", check="app_focused('X')"),
                ),
            ]
        )
        trace = _make_trace(milestone_results=[])
        grader_result = GraderResult(passed=False, method="programmatic", explanation="Failed")
        report = generate_report(task, trace, grader_result, tmp_path)

        # Should show milestones with [--] since no results
        assert "[--] **m1**" in report


# ---------------------------------------------------------------------------
# Comparison report with milestones
# ---------------------------------------------------------------------------


class TestComparisonReportMilestones:
    def test_comparison_shows_milestone_column(self) -> None:
        trace = _make_trace(
            outcome="fail",
            milestone_results=[
                MilestoneResult(id="m1", passed=True, explanation="OK"),
                MilestoneResult(id="m2", passed=False, explanation="Failed"),
            ],
        )
        grade = GraderResult(passed=False, method="programmatic", explanation="Failed")
        report = generate_comparison_report([(trace, grade)])

        assert "Milestones" in report
        assert "1/2" in report

    def test_comparison_no_milestones_shows_dash(self) -> None:
        trace = _make_trace(outcome="pass", milestone_results=[])
        grade = GraderResult(passed=True, method="programmatic", explanation="OK")
        report = generate_comparison_report([(trace, grade)])

        # Should show — for milestones column
        assert "| — |" in report


class TestDetailedReportMilestones:
    def test_detailed_per_task_shows_milestones(self) -> None:
        trace = _make_trace(
            outcome="fail",
            milestone_results=[
                MilestoneResult(id="m1", passed=True, explanation="OK"),
                MilestoneResult(id="m2", passed=True, explanation="OK"),
                MilestoneResult(id="m3", passed=False, explanation="Failed"),
            ],
        )
        grade = GraderResult(passed=False, method="programmatic", explanation="Failed")
        report = generate_detailed_report([(trace, grade)])

        assert "Milestones" in report
        assert "2/3" in report
