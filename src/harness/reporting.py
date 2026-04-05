"""Generate human-readable summary reports from trial results."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from harness.types import GraderResult, Task, Trace


def generate_report(
    task: Task,
    trace: Trace,
    grader_result: GraderResult,
    run_dir: Path,
) -> str:
    """Generate a Markdown summary report for a single trial."""
    outcome_icon = {"pass": "PASS", "fail": "FAIL", "error": "ERROR"}.get(trace.outcome, "UNKNOWN")

    lines = [
        f"# Run Report: {task.task_id}",
        "",
        f"**Outcome:** {outcome_icon}",
        f"**Adapter:** {trace.adapter}",
        f"**Steps:** {trace.total_steps}",
        f"**Started:** {trace.started_at.isoformat()}",
        f"**Completed:** {trace.completed_at.isoformat() if trace.completed_at else 'N/A'}",
        "",
    ]

    if trace.failure_category:
        lines.append(f"**Failure Category:** {trace.failure_category.value}")
        lines.append("")

    lines.append("## Task")
    lines.append("")
    lines.append(f"**Goal:** {task.goal.description}")
    lines.append("")

    lines.append("## Grading")
    lines.append("")
    lines.append(f"**Method:** {grader_result.method}")
    lines.append(f"**Passed:** {grader_result.passed}")
    lines.append(f"**Explanation:** {grader_result.explanation}")
    lines.append("")

    lines.append("## Steps")
    lines.append("")
    for step in trace.steps:
        action_desc = _format_action(step.action)
        status = "ok" if step.error is None else f"error: {step.error}"
        lines.append(f"{step.step}. {action_desc} -> {status}")
    lines.append("")

    # Evidence summary (if decision-point evidence was persisted)
    evidence_path = run_dir / "evidence.json"
    if evidence_path.exists():
        lines.append("## Decision Evidence")
        lines.append("")
        try:
            import json

            evidence = json.loads(evidence_path.read_text())
            lines.append(f"- **Steps with evidence:** {len(evidence)}")
            for i, ev in enumerate(evidence[:5]):  # Show first 5
                app = ev.get("focused_app", "?")
                action = ev.get("parsed_action", {})
                action_name = action.get("action", "?") if isinstance(action, dict) else "?"
                target = action.get("target", "") if isinstance(action, dict) else ""
                lines.append(f"  {i + 1}. [{app}] {action_name} → {target}")
            if len(evidence) > 5:
                lines.append(f"  ... and {len(evidence) - 5} more (see evidence.json)")
        except Exception:
            lines.append("- Evidence file exists but could not be parsed")
        lines.append("")

    # Milestone progress (if task has milestones)
    if task.milestones:
        lines.append("## Milestones")
        lines.append("")

        # Build a lookup from milestone results on the trace
        result_map: dict[str, tuple[bool, str]] = {}
        for mr in trace.milestone_results:
            result_map[mr.id] = (mr.passed, mr.explanation)

        first_failed: str | None = None
        for m in task.milestones:
            if m.id in result_map:
                passed, explanation = result_map[m.id]
                icon = "PASS" if passed else "FAIL"
                lines.append(f"- [{icon}] **{m.id}**: {m.description}")
                if explanation:
                    lines.append(f"  - {explanation}")
                if not passed and first_failed is None:
                    first_failed = m.id
            else:
                lines.append(f"- [--] **{m.id}**: {m.description}")

        if first_failed is not None:
            lines.append("")
            lines.append(f"**First failure at:** {first_failed}")
        lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    lines.append(f"- Run directory: `{run_dir}`")
    artifacts_dir = run_dir / "artifacts"
    if artifacts_dir.exists():
        for f in sorted(artifacts_dir.iterdir()):
            lines.append(f"- `artifacts/{f.name}` ({f.stat().st_size} bytes)")
    if evidence_path.exists():
        lines.append(f"- `evidence.json` ({evidence_path.stat().st_size} bytes)")
    lines.append("")

    return "\n".join(lines)


def _format_milestone_progress(trace: Trace) -> str:
    """Format milestone results as a compact progress string, e.g. '2/3'."""
    if not trace.milestone_results:
        return "—"
    passed = sum(1 for mr in trace.milestone_results if mr.passed)
    total = len(trace.milestone_results)
    return f"{passed}/{total}"


def _format_action(action: dict[str, object]) -> str:
    """Format a flat action dict for human-readable display."""
    action_type = action.get("type", "unknown")

    if action_type == "goto":
        return f"goto {action.get('url', '?')}"
    if action_type == "click":
        target = action.get("selector", f"({action.get('x')},{action.get('y')})")
        return f"click {target}"
    if action_type == "type":
        return f"type '{action.get('text', '')}'"
    if action_type in ("done", "fail"):
        return str(action_type)

    extras = {k: v for k, v in action.items() if k != "type"}
    return f"{action_type} {extras}" if extras else str(action_type)


# ---------------------------------------------------------------------------
# Detailed metrics
# ---------------------------------------------------------------------------


def step_success_rate(trace: Trace) -> float:
    """Fraction of steps that succeeded (result starts with 'ok' or is 'done')."""
    if not trace.steps:
        return 0.0
    ok = sum(1 for s in trace.steps if s.result.startswith("ok") or s.result == "done")
    return ok / len(trace.steps)


def failure_distribution(runs: list[tuple[Trace, GraderResult]]) -> dict[str, int]:
    """Count failure categories across multiple runs."""
    counts: dict[str, int] = {}
    for trace, _ in runs:
        if trace.failure_category:
            cat = trace.failure_category.value
            counts[cat] = counts.get(cat, 0) + 1
    return counts


def semantic_action_ratio(trace: Trace) -> float:
    """Fraction of actions using selectors vs pixel coordinates.

    Each step is counted at most once: selector takes precedence over coordinates.
    Steps with neither (e.g. done, fail, press) are excluded from the ratio.
    """
    if not trace.steps:
        return 0.0
    semantic = 0
    pixel = 0
    for s in trace.steps:
        if "selector" in s.action:
            semantic += 1
        elif "x" in s.action and "y" in s.action:
            pixel += 1
    total = semantic + pixel
    return semantic / total if total > 0 else 0.0


def cost_per_success(runs: list[tuple[Trace, GraderResult]]) -> float | None:
    """Average cost per successful run. Returns None if no runs passed."""
    passed = [(t, g) for t, g in runs if g.passed]
    if not passed:
        return None
    total_cost: float = sum(
        float((t.metadata or {}).get("estimated_cost_usd", 0.0)) for t, _ in passed
    )
    return total_cost / len(passed)


def avg_latency_ms(trace: Trace) -> float | None:
    """Average latency per step from trace metadata, if available."""
    meta = trace.metadata or {}
    if "avg_latency_ms" in meta:
        return float(meta["avg_latency_ms"])
    # For adapters with timestamps, compute from step intervals
    if len(trace.steps) < 2:
        return None
    deltas: list[float] = []
    for i in range(1, len(trace.steps)):
        dt = (trace.steps[i].timestamp - trace.steps[i - 1].timestamp).total_seconds() * 1000
        if dt > 0:
            deltas.append(dt)
    return sum(deltas) / len(deltas) if deltas else None


# ---------------------------------------------------------------------------
# Comparison reporting
# ---------------------------------------------------------------------------


def _load_run_data(run_dir: Path) -> tuple[Trace, GraderResult] | None:
    """Load trace and grade from a run directory. Returns None on failure."""
    trace_path = run_dir / "trace.json"
    grade_path = run_dir / "grade.json"

    if not trace_path.exists() or not grade_path.exists():
        return None

    trace = Trace.model_validate_json(trace_path.read_text())
    grader_result = GraderResult.model_validate_json(grade_path.read_text())
    return trace, grader_result


def collect_runs(
    runs_dir: Path, task_filter: str | None = None
) -> list[tuple[Trace, GraderResult]]:
    """Collect all (trace, grade) pairs from run directories under runs_dir."""
    results: list[tuple[Trace, GraderResult]] = []

    if not runs_dir.exists():
        return results

    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir():
            continue
        data = _load_run_data(entry)
        if data is None:
            continue
        trace, grade_result = data
        if task_filter and trace.task_id != task_filter:
            continue
        results.append((trace, grade_result))

    return results


def generate_comparison_report(runs: list[tuple[Trace, GraderResult]]) -> str:
    """Generate a Markdown comparison table from multiple runs."""
    if not runs:
        return "No runs found.\n"

    # Group by task
    by_task: dict[str, list[tuple[Trace, GraderResult]]] = {}
    for trace, grade_result in runs:
        by_task.setdefault(trace.task_id, []).append((trace, grade_result))

    lines = [
        "# Comparison Report",
        "",
        "| Task | Adapter | Outcome | Steps | Cost | Failure | Milestones |",
        "|---|---|---|---|---|---|---|",
    ]

    for task_id in sorted(by_task.keys()):
        task_runs = by_task[task_id]
        # Sort by adapter name for consistent ordering
        task_runs.sort(key=lambda r: r[0].adapter)

        for trace, _grade_result in task_runs:
            cost = "$0.00"
            if trace.metadata and "estimated_cost_usd" in trace.metadata:
                cost = f"${trace.metadata['estimated_cost_usd']:.2f}"

            failure = trace.failure_category.value if trace.failure_category else "—"

            milestones = _format_milestone_progress(trace)

            lines.append(
                f"| {task_id} | {trace.adapter} | {trace.outcome} "
                f"| {trace.total_steps} | {cost} | {failure} | {milestones} |"
            )

    lines.append("")

    # Token summary for runs with metadata
    token_runs = [(t, g) for t, g in runs if t.metadata and "total_tokens" in t.metadata]
    if token_runs:
        lines.append("## Cost Summary")
        lines.append("")
        lines.append(
            "| Task | Adapter | Input Tokens | Output Tokens | Total Tokens | Cost | API Calls |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for trace, _ in token_runs:
            meta = trace.metadata or {}
            lines.append(
                f"| {trace.task_id} | {trace.adapter} "
                f"| {meta.get('input_tokens', 0):,} "
                f"| {meta.get('output_tokens', 0):,} "
                f"| {meta.get('total_tokens', 0):,} "
                f"| ${meta.get('estimated_cost_usd', 0):.4f} "
                f"| {meta.get('api_calls', 0)} |"
            )
        lines.append("")

    return "\n".join(lines)


def generate_detailed_report(runs: list[tuple[Trace, GraderResult]]) -> str:
    """Generate a detailed Markdown comparison report with extended metrics."""
    if not runs:
        return "No runs found.\n"

    from harness.failures import FailureCategory

    # Group by adapter and task
    by_adapter: dict[str, list[tuple[Trace, GraderResult]]] = defaultdict(list)
    by_task: dict[str, list[tuple[Trace, GraderResult]]] = defaultdict(list)
    for trace, grade_result in runs:
        by_adapter[trace.adapter].append((trace, grade_result))
        by_task[trace.task_id].append((trace, grade_result))

    lines = ["# Detailed Comparison Report", ""]

    # --- Overview table ---
    lines.append("## Overview")
    lines.append("")
    lines.append(
        "| Adapter | Tasks Passed | Tasks Failed | Step Success Rate | Avg Steps | Avg Cost |"
    )
    lines.append("|---|---|---|---|---|---|")

    for adapter_name in sorted(by_adapter.keys()):
        adapter_runs = by_adapter[adapter_name]
        passed = sum(1 for _, g in adapter_runs if g.passed)
        failed = len(adapter_runs) - passed
        avg_ssr = (
            sum(step_success_rate(t) for t, _ in adapter_runs) / len(adapter_runs)
            if adapter_runs
            else 0.0
        )
        avg_steps = (
            sum(t.total_steps for t, _ in adapter_runs) / len(adapter_runs)
            if adapter_runs
            else 0.0
        )
        avg_cost = sum(
            (t.metadata or {}).get("estimated_cost_usd", 0.0) for t, _ in adapter_runs
        ) / max(len(adapter_runs), 1)
        lines.append(
            f"| {adapter_name} | {passed} | {failed} "
            f"| {avg_ssr:.0%} | {avg_steps:.1f} | ${avg_cost:.4f} |"
        )

    lines.append("")

    # --- Per-task breakdown ---
    lines.append("## Per-Task Breakdown")
    lines.append("")

    for task_id in sorted(by_task.keys()):
        lines.append(f"### {task_id}")
        lines.append("")
        lines.append(
            "| Adapter | Outcome | Steps | Step Success | Cost | Latency (ms) | Milestones |"
        )
        lines.append("|---|---|---|---|---|---|---|")

        task_runs = by_task[task_id]
        task_runs.sort(key=lambda r: r[0].adapter)
        for trace, _grade_result in task_runs:
            ssr = step_success_rate(trace)
            cost = (trace.metadata or {}).get("estimated_cost_usd", 0.0)
            latency = avg_latency_ms(trace)
            latency_str = f"{latency:.0f}" if latency is not None else "\u2014"
            milestones = _format_milestone_progress(trace)
            lines.append(
                f"| {trace.adapter} | {trace.outcome} "
                f"| {trace.total_steps} | {ssr:.0%} | ${cost:.4f} | {latency_str} | {milestones} |"
            )
        lines.append("")

    # --- Failure analysis ---
    lines.append("## Failure Analysis")
    lines.append("")

    all_categories = [c.value for c in FailureCategory]
    header_cats = " | ".join(cat.capitalize() for cat in all_categories)
    lines.append(f"| Adapter | {header_cats} |")
    lines.append("|---" + "|---" * len(all_categories) + "|")

    for adapter_name in sorted(by_adapter.keys()):
        adapter_runs = by_adapter[adapter_name]
        dist = failure_distribution(adapter_runs)
        counts = " | ".join(str(dist.get(cat, 0)) for cat in all_categories)
        lines.append(f"| {adapter_name} | {counts} |")

    lines.append("")

    # --- Cost efficiency ---
    lines.append("## Cost Efficiency")
    lines.append("")
    lines.append("| Adapter | Runs | Passed | Cost/Success | Semantic Action % |")
    lines.append("|---|---|---|---|---|")

    for adapter_name in sorted(by_adapter.keys()):
        adapter_runs = by_adapter[adapter_name]
        total = len(adapter_runs)
        passed = sum(1 for _, g in adapter_runs if g.passed)
        cps = cost_per_success(adapter_runs)
        cps_str = f"${cps:.4f}" if cps is not None else "\u2014"
        avg_sar = (
            sum(semantic_action_ratio(t) for t, _ in adapter_runs) / len(adapter_runs)
            if adapter_runs
            else 0.0
        )
        lines.append(f"| {adapter_name} | {total} | {passed} | {cps_str} | {avg_sar:.0%} |")

    lines.append("")

    # --- Observation experiment (if hybrid runs exist) ---
    hybrid_runs = [r for r in runs if r[0].adapter in ("openai_cu", "openai_cu_hybrid")]
    if hybrid_runs:
        lines.append("## Observation Experiment")
        lines.append("")
        lines.append("| Variant | Task | Outcome | Steps | Step Success | Cost |")
        lines.append("|---|---|---|---|---|---|")
        hybrid_runs.sort(key=lambda r: (r[0].adapter, r[0].task_id))
        for trace, _grade_result in hybrid_runs:
            ssr = step_success_rate(trace)
            cost = (trace.metadata or {}).get("estimated_cost_usd", 0.0)
            lines.append(
                f"| {trace.adapter} | {trace.task_id} "
                f"| {trace.outcome} | {trace.total_steps} | {ssr:.0%} | ${cost:.4f} |"
            )
        lines.append("")

    # --- Key Findings placeholder ---
    lines.append("## Key Findings")
    lines.append("")
    lines.append("*(To be documented after running experiments)*")
    lines.append("")

    return "\n".join(lines)
