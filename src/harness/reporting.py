"""Generate human-readable summary reports from trial results."""

from __future__ import annotations

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

    lines.append("## Artifacts")
    lines.append("")
    lines.append(f"- Run directory: `{run_dir}`")
    artifacts_dir = run_dir / "artifacts"
    if artifacts_dir.exists():
        for f in sorted(artifacts_dir.iterdir()):
            lines.append(f"- `artifacts/{f.name}` ({f.stat().st_size} bytes)")
    lines.append("")

    return "\n".join(lines)


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
        "| Task | Adapter | Outcome | Steps | Cost | Failure |",
        "|---|---|---|---|---|---|",
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

            lines.append(
                f"| {task_id} | {trace.adapter} | {trace.outcome} "
                f"| {trace.total_steps} | {cost} | {failure} |"
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
