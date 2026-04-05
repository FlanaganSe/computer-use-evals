"""Tests for detailed metrics and reporting functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from harness.reporting import (
    _format_runtime_verification,
    avg_latency_ms,
    cost_per_success,
    failure_distribution,
    generate_detailed_report,
    generate_report,
    semantic_action_ratio,
    step_success_rate,
)
from harness.types import GraderResult, StepRecord, Task, Trace


def _make_step(
    step: int = 1,
    action: dict | None = None,
    result: str = "ok",
    ts: datetime | None = None,
    metrics: dict | None = None,
) -> StepRecord:
    return StepRecord(
        step=step,
        action=action or {"type": "click", "selector": "#btn"},
        result=result,
        timestamp=ts or datetime(2026, 4, 1, tzinfo=UTC),
        metrics=metrics,
    )


def _make_trace(
    task_id: str = "browser-download",
    adapter: str = "deterministic",
    outcome: str = "pass",
    total_steps: int = 3,
    steps: list[StepRecord] | None = None,
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
        steps=steps or [],
        metadata=metadata,
        failure_category=failure_category,
    )


def _make_grade(passed: bool = True) -> GraderResult:
    return GraderResult(
        passed=passed,
        method="file_exists",
        explanation="OK" if passed else "Not found",
    )


class TestStepSuccessRate:
    def test_empty_trace(self):
        trace = _make_trace(steps=[])
        assert step_success_rate(trace) == 0.0

    def test_all_ok(self):
        steps = [_make_step(i, result="ok") for i in range(1, 4)]
        trace = _make_trace(steps=steps)
        assert step_success_rate(trace) == 1.0

    def test_mixed(self):
        steps = [
            _make_step(1, result="ok"),
            _make_step(2, result="error:timeout"),
            _make_step(3, result="done"),
        ]
        trace = _make_trace(steps=steps)
        # 2 out of 3 succeed (ok + done)
        assert abs(step_success_rate(trace) - 2 / 3) < 1e-9

    def test_ok_prefix_variants(self):
        steps = [
            _make_step(1, result="ok:downloaded:file.txt"),
            _make_step(2, result="ok"),
        ]
        trace = _make_trace(steps=steps)
        assert step_success_rate(trace) == 1.0


class TestFailureDistribution:
    def test_empty(self):
        assert failure_distribution([]) == {}

    def test_no_failures(self):
        runs = [(_make_trace(), _make_grade())]
        assert failure_distribution(runs) == {}

    def test_counts_categories(self):
        runs = [
            (_make_trace(failure_category="perception"), _make_grade(False)),
            (_make_trace(failure_category="perception"), _make_grade(False)),
            (_make_trace(failure_category="planning"), _make_grade(False)),
        ]
        dist = failure_distribution(runs)
        assert dist == {"perception": 2, "planning": 1}


class TestSemanticActionRatio:
    def test_empty(self):
        trace = _make_trace(steps=[])
        assert semantic_action_ratio(trace) == 0.0

    def test_all_semantic(self):
        steps = [
            _make_step(1, action={"type": "click", "selector": "#btn"}),
            _make_step(2, action={"type": "type", "selector": "#input", "text": "hi"}),
        ]
        trace = _make_trace(steps=steps)
        assert semantic_action_ratio(trace) == 1.0

    def test_all_pixel(self):
        steps = [
            _make_step(1, action={"type": "click", "x": 100, "y": 200}),
            _make_step(2, action={"type": "click", "x": 300, "y": 400}),
        ]
        trace = _make_trace(steps=steps)
        assert semantic_action_ratio(trace) == 0.0

    def test_mixed(self):
        steps = [
            _make_step(1, action={"type": "click", "selector": "#btn"}),
            _make_step(2, action={"type": "click", "x": 100, "y": 200}),
        ]
        trace = _make_trace(steps=steps)
        assert semantic_action_ratio(trace) == 0.5

    def test_semantic_target_field(self):
        """structured-state adapter emits 'semantic_target', not 'selector' (B5)."""
        steps = [
            _make_step(1, action={"type": "click", "semantic_target": "ax_abc123"}),
            _make_step(2, action={"type": "click", "x": 100, "y": 200}),
        ]
        trace = _make_trace(steps=steps)
        assert semantic_action_ratio(trace) == 0.5

    def test_all_semantic_target(self):
        steps = [
            _make_step(1, action={"type": "click", "semantic_target": "ax_abc123"}),
            _make_step(2, action={"type": "click", "semantic_target": "ax_def456"}),
        ]
        trace = _make_trace(steps=steps)
        assert semantic_action_ratio(trace) == 1.0

    def test_neither_semantic_nor_pixel(self):
        steps = [_make_step(1, action={"type": "done"})]
        trace = _make_trace(steps=steps)
        assert semantic_action_ratio(trace) == 0.0


class TestCostPerSuccess:
    def test_no_runs(self):
        assert cost_per_success([]) is None

    def test_no_passes(self):
        runs = [(_make_trace(outcome="fail"), _make_grade(False))]
        assert cost_per_success(runs) is None

    def test_single_pass(self):
        trace = _make_trace(metadata={"estimated_cost_usd": 0.10})
        runs = [(trace, _make_grade(True))]
        assert cost_per_success(runs) == 0.10

    def test_averages_across_passes(self):
        runs = [
            (_make_trace(metadata={"estimated_cost_usd": 0.10}), _make_grade(True)),
            (_make_trace(metadata={"estimated_cost_usd": 0.20}), _make_grade(True)),
            (_make_trace(outcome="fail"), _make_grade(False)),
        ]
        # Only 2 passed, total cost 0.30 / 2 = 0.15
        assert abs(cost_per_success(runs) - 0.15) < 1e-9  # type: ignore[operator]

    def test_zero_cost_passes(self):
        runs = [(_make_trace(), _make_grade(True))]
        assert cost_per_success(runs) == 0.0


class TestAvgLatencyMs:
    def test_from_metadata(self):
        trace = _make_trace(metadata={"avg_latency_ms": 500})
        assert avg_latency_ms(trace) == 500.0

    def test_from_timestamps(self):
        base = datetime(2026, 4, 1, tzinfo=UTC)
        steps = [
            _make_step(1, ts=base),
            _make_step(2, ts=base + timedelta(milliseconds=200)),
            _make_step(3, ts=base + timedelta(milliseconds=600)),
        ]
        trace = _make_trace(steps=steps)
        latency = avg_latency_ms(trace)
        assert latency is not None
        # (200 + 400) / 2 = 300
        assert abs(latency - 300.0) < 1e-9

    def test_single_step_returns_none(self):
        trace = _make_trace(steps=[_make_step(1)])
        assert avg_latency_ms(trace) is None


class TestGenerateDetailedReport:
    def test_empty(self):
        report = generate_detailed_report([])
        assert "No runs found" in report

    def test_overview_section(self):
        runs = [
            (
                _make_trace(
                    adapter="deterministic",
                    steps=[_make_step(1), _make_step(2), _make_step(3, result="done")],
                    total_steps=3,
                ),
                _make_grade(True),
            ),
        ]
        report = generate_detailed_report(runs)
        assert "## Overview" in report
        assert "deterministic" in report
        assert "1" in report  # 1 passed

    def test_per_task_section(self):
        runs = [
            (_make_trace(task_id="browser-download"), _make_grade(True)),
            (_make_trace(task_id="browser-form-fill"), _make_grade(True)),
        ]
        report = generate_detailed_report(runs)
        assert "### browser-download" in report
        assert "### browser-form-fill" in report

    def test_failure_analysis_section(self):
        runs = [
            (
                _make_trace(adapter="openai_cu", failure_category="perception", outcome="fail"),
                _make_grade(False),
            ),
        ]
        report = generate_detailed_report(runs)
        assert "## Failure Analysis" in report
        assert "openai_cu" in report

    def test_cost_efficiency_section(self):
        runs = [
            (
                _make_trace(
                    adapter="openai_cu",
                    metadata={"estimated_cost_usd": 0.10},
                ),
                _make_grade(True),
            ),
        ]
        report = generate_detailed_report(runs)
        assert "## Cost Efficiency" in report
        assert "$0.1000" in report

    def test_observation_experiment_section(self):
        runs = [
            (_make_trace(adapter="openai_cu"), _make_grade(True)),
            (_make_trace(adapter="openai_cu_hybrid"), _make_grade(True)),
        ]
        report = generate_detailed_report(runs)
        assert "## Observation Mode Comparison (Legacy)" in report
        assert "openai_cu_hybrid" in report

    def test_no_observation_section_without_openai(self):
        runs = [(_make_trace(adapter="deterministic"), _make_grade(True))]
        report = generate_detailed_report(runs)
        assert "Observation Mode Comparison (Legacy)" not in report

    def test_key_findings_placeholder(self):
        runs = [(_make_trace(), _make_grade(True))]
        report = generate_detailed_report(runs)
        assert "## Key Findings" in report

    def test_structured_state_experiment_section(self):
        runs = [
            (
                _make_trace(
                    adapter="structured_state_desktop",
                    metadata={"estimated_cost_usd": 0.01},
                ),
                _make_grade(True),
            ),
            (
                _make_trace(
                    adapter="structured_state_desktop_routed",
                    metadata={
                        "estimated_cost_usd": 0.005,
                        "routing_enabled": True,
                        "cheap_steps": 4,
                        "strong_steps": 1,
                        "escalations": 1,
                    },
                ),
                _make_grade(True),
            ),
        ]
        report = generate_detailed_report(runs)
        assert "## Structured-State Desktop" in report
        assert "structured_state_desktop_routed" in report
        assert "Cheap Steps" in report

    def test_structured_state_section_shows_routing_metadata(self):
        runs = [
            (
                _make_trace(
                    adapter="structured_state_desktop_routed",
                    metadata={
                        "estimated_cost_usd": 0.003,
                        "routing_enabled": True,
                        "cheap_steps": 7,
                        "strong_steps": 2,
                        "escalations": 2,
                    },
                ),
                _make_grade(True),
            ),
        ]
        report = generate_detailed_report(runs)
        assert "## Structured-State Desktop" in report
        assert "7" in report  # cheap_steps
        assert "2" in report  # strong_steps

    def test_no_structured_state_section_without_ss_runs(self):
        runs = [(_make_trace(adapter="deterministic"), _make_grade(True))]
        report = generate_detailed_report(runs)
        assert "Structured-State Desktop" not in report

    def test_baseline_ss_shows_dashes_for_routing_fields(self):
        runs = [
            (
                _make_trace(
                    adapter="structured_state_desktop",
                    metadata={"estimated_cost_usd": 0.01},
                ),
                _make_grade(True),
            ),
        ]
        report = generate_detailed_report(runs)
        assert "## Structured-State Desktop" in report
        # Baseline has no routing metadata, so should show dashes
        assert "\u2014" in report


# ---------------------------------------------------------------------------
# Runtime verification reporting (M3)
# ---------------------------------------------------------------------------


class TestRuntimeVerification:
    def test_format_state_change_summary(self) -> None:
        steps = [
            _make_step(1, metrics={"state_changed": True}),
            _make_step(2, metrics={"state_changed": False}),
            _make_step(3, metrics={"state_changed": None}),
        ]
        lines = _format_runtime_verification(steps)
        text = "\n".join(lines)
        assert "1 yes" in text
        assert "1 no" in text
        assert "1 unknown" in text

    def test_format_stagnation_detected(self) -> None:
        steps = [
            _make_step(1, metrics={"stagnation_detected": True}),
        ]
        lines = _format_runtime_verification(steps)
        text = "\n".join(lines)
        assert "Stagnation detected" in text

    def test_format_ax_quality(self) -> None:
        steps = [
            _make_step(
                1,
                metrics={
                    "state_changed": True,
                    "interactive_total": 10,
                    "interactive_with_bounds": 8,
                    "interactive_without_bounds": 2,
                },
            ),
            _make_step(
                2,
                metrics={
                    "state_changed": True,
                    "interactive_total": 12,
                    "interactive_with_bounds": 10,
                    "interactive_without_bounds": 2,
                },
            ),
        ]
        lines = _format_runtime_verification(steps)
        text = "\n".join(lines)
        assert "Avg interactive elements" in text
        assert "with bounds" in text

    def test_no_stagnation_line_when_none(self) -> None:
        steps = [
            _make_step(1, metrics={"state_changed": True}),
        ]
        lines = _format_runtime_verification(steps)
        text = "\n".join(lines)
        assert "Stagnation" not in text

    def test_report_includes_verification_section(self, tmp_path: Path) -> None:
        """generate_report should include runtime verification when steps have metrics."""
        task = Task(
            task_id="test-task",
            version="1.0",
            goal={"description": "Test"},
            verification={"primary": {"method": "programmatic", "check": "file_exists('x')"}},
        )
        steps = [
            StepRecord(
                step=1,
                action={"type": "click", "x": 100, "y": 200},
                result="ok",
                metrics={
                    "state_changed": True,
                    "action_transport": "coordinates",
                    "target_found": True,
                    "interactive_total": 5,
                    "interactive_with_bounds": 3,
                    "interactive_without_bounds": 2,
                },
            ),
        ]
        trace = Trace(
            task_id="test-task",
            adapter="test",
            started_at=datetime(2026, 4, 1, tzinfo=UTC),
            completed_at=datetime(2026, 4, 1, 0, 1, tzinfo=UTC),
            steps=steps,
            outcome="pass",
            total_steps=1,
        )
        grade_result = GraderResult(passed=True, method="test", explanation="OK")
        report = generate_report(task, trace, grade_result, tmp_path)
        assert "## Runtime Verification" in report
        assert "State changed" in report

    def test_report_omits_verification_without_metrics(self, tmp_path: Path) -> None:
        """generate_report should not include runtime verification without metrics."""
        task = Task(
            task_id="test-task",
            version="1.0",
            goal={"description": "Test"},
            verification={"primary": {"method": "programmatic", "check": "file_exists('x')"}},
        )
        steps = [
            StepRecord(
                step=1,
                action={"type": "click", "x": 100, "y": 200},
                result="ok",
            ),
        ]
        trace = Trace(
            task_id="test-task",
            adapter="test",
            started_at=datetime(2026, 4, 1, tzinfo=UTC),
            completed_at=datetime(2026, 4, 1, 0, 1, tzinfo=UTC),
            steps=steps,
            outcome="pass",
            total_steps=1,
        )
        grade_result = GraderResult(passed=True, method="test", explanation="OK")
        report = generate_report(task, trace, grade_result, tmp_path)
        assert "Runtime Verification" not in report
