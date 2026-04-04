"""Tests for comparison reporting."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from harness.reporting import collect_runs, generate_comparison_report
from harness.types import GraderResult, Trace


def _make_trace(
    task_id: str = "browser-download",
    adapter: str = "deterministic",
    outcome: str = "pass",
    total_steps: int = 3,
    metadata: dict | None = None,
    failure_category: str | None = None,
) -> Trace:
    return Trace(
        task_id=task_id,
        adapter=adapter,
        started_at=datetime(2026, 4, 1, tzinfo=UTC),
        completed_at=datetime(2026, 4, 1, 0, 1, tzinfo=UTC),
        outcome=outcome,
        total_steps=total_steps,
        metadata=metadata,
        failure_category=failure_category,
    )


def _make_grade(passed: bool = True) -> GraderResult:
    return GraderResult(
        passed=passed,
        method="file_exists",
        explanation="OK" if passed else "Not found",
    )


class TestGenerateComparisonReport:
    def test_empty_runs(self):
        report = generate_comparison_report([])
        assert "No runs found" in report

    def test_single_run(self):
        runs = [(_make_trace(), _make_grade())]
        report = generate_comparison_report(runs)
        assert "browser-download" in report
        assert "deterministic" in report
        assert "pass" in report

    def test_multiple_adapters_same_task(self):
        runs = [
            (_make_trace(adapter="deterministic", total_steps=3), _make_grade()),
            (
                _make_trace(
                    adapter="openai_cu",
                    total_steps=7,
                    metadata={
                        "input_tokens": 12000,
                        "output_tokens": 600,
                        "total_tokens": 12600,
                        "estimated_cost_usd": 0.043,
                        "model": "computer-use-preview",
                        "api_calls": 5,
                    },
                ),
                _make_grade(),
            ),
        ]
        report = generate_comparison_report(runs)

        assert "deterministic" in report
        assert "openai_cu" in report
        assert "$0.00" in report  # deterministic has no cost
        assert "$0.04" in report  # openai cost rounded

    def test_multiple_tasks(self):
        runs = [
            (_make_trace(task_id="browser-download"), _make_grade()),
            (_make_trace(task_id="browser-form-fill", adapter="deterministic"), _make_grade()),
        ]
        report = generate_comparison_report(runs)
        assert "browser-download" in report
        assert "browser-form-fill" in report

    def test_failure_category_shown(self):
        runs = [
            (
                _make_trace(outcome="fail", failure_category="perception"),
                _make_grade(passed=False),
            ),
        ]
        report = generate_comparison_report(runs)
        assert "perception" in report

    def test_cost_summary_section(self):
        runs = [
            (
                _make_trace(
                    adapter="openai_cu",
                    metadata={
                        "input_tokens": 10000,
                        "output_tokens": 500,
                        "total_tokens": 10500,
                        "estimated_cost_usd": 0.036,
                        "api_calls": 3,
                    },
                ),
                _make_grade(),
            ),
        ]
        report = generate_comparison_report(runs)
        assert "Cost Summary" in report
        assert "10,000" in report
        assert "500" in report


class TestCollectRuns:
    def test_collects_from_directory(self, tmp_path: Path):
        run1 = tmp_path / "run1"
        run1.mkdir()
        trace = _make_trace(task_id="task-a")
        grade = _make_grade()
        (run1 / "trace.json").write_text(trace.model_dump_json())
        (run1 / "grade.json").write_text(grade.model_dump_json())

        run2 = tmp_path / "run2"
        run2.mkdir()
        trace2 = _make_trace(task_id="task-b")
        (run2 / "trace.json").write_text(trace2.model_dump_json())
        (run2 / "grade.json").write_text(grade.model_dump_json())

        runs = collect_runs(tmp_path)
        assert len(runs) == 2

    def test_filters_by_task(self, tmp_path: Path):
        for i, tid in enumerate(["task-a", "task-b", "task-a"]):
            d = tmp_path / f"run{i}"
            d.mkdir()
            (d / "trace.json").write_text(_make_trace(task_id=tid).model_dump_json())
            (d / "grade.json").write_text(_make_grade().model_dump_json())

        runs = collect_runs(tmp_path, task_filter="task-a")
        assert len(runs) == 2
        assert all(t.task_id == "task-a" for t, _ in runs)

    def test_skips_incomplete_runs(self, tmp_path: Path):
        # Directory with no trace.json
        incomplete = tmp_path / "incomplete"
        incomplete.mkdir()
        (incomplete / "grade.json").write_text(_make_grade().model_dump_json())

        runs = collect_runs(tmp_path)
        assert len(runs) == 0

    def test_nonexistent_directory(self, tmp_path: Path):
        runs = collect_runs(tmp_path / "nope")
        assert len(runs) == 0
