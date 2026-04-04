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
