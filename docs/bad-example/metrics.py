"""
Metrics computation and reporting.

Goes beyond simple pass/fail to surface the unknown-unknowns that
determine product feasibility:

1. WHERE things fail (failure taxonomy)
2. WHEN things fail (step position, workflow length)
3. WHY things fail (grounding confidence distribution)
4. HOW MUCH it costs (per-step, per-workflow, by strategy)
5. HOW FAST it is (latency distribution, by perception mode)
6. WHAT HELPS (healing success rate, fallback value)

These metrics directly map to product decisions:
- If graph matching fails >20% of the time → need better detection
- If LLM fallback rarely helps → don't build it yet
- If a11y trees unavailable >50% → must support screenshot-only
- If cost per workflow >$0.10 → pricing model won't work
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.types import (
    GroundingStrategy,
    PerceptionMode,
    StepOutcome,
    WorkflowResult,
)


@dataclass
class AggregateMetrics:
    """Top-level metrics across all eval runs."""

    # Success rates
    workflow_success_rate: float = 0.0
    step_success_rate: float = 0.0
    healing_rate: float = 0.0

    # Failure analysis
    failure_distribution: dict[str, int] = field(default_factory=dict)
    failure_by_step_position: dict[str, int] = field(default_factory=dict)  # early/mid/late
    failure_by_workflow_length: dict[str, float] = field(default_factory=dict)

    # Grounding analysis
    grounding_confidence_mean: float = 0.0
    grounding_confidence_p50: float = 0.0
    grounding_confidence_p10: float = 0.0
    grounding_ambiguity_mean: float = 0.0
    fallback_rate: float = 0.0              # How often LLM fallback was needed
    fallback_success_rate: float = 0.0      # When needed, how often it helped

    # Performance
    latency_per_step_mean_ms: float = 0.0
    latency_per_step_p95_ms: float = 0.0
    latency_per_workflow_mean_ms: float = 0.0

    # Cost
    cost_per_step_mean_usd: float = 0.0
    cost_per_workflow_mean_usd: float = 0.0
    model_calls_per_workflow_mean: float = 0.0

    # Perception analysis
    a11y_availability_rate: float = 0.0     # How often a11y tree was available
    graph_element_count_mean: float = 0.0   # Average elements per screen

    # Coverage
    total_workflows: int = 0
    total_steps: int = 0
    total_steps_executed: int = 0

    # Raw data for custom analysis
    raw_results: list[dict[str, Any]] = field(default_factory=list)


def compute_metrics(results: list[WorkflowResult]) -> AggregateMetrics:
    """Compute aggregate metrics from a list of workflow results."""
    metrics = AggregateMetrics()

    if not results:
        return metrics

    metrics.total_workflows = len(results)

    # Workflow-level success
    successes = sum(1 for r in results if r.success)
    metrics.workflow_success_rate = successes / len(results)

    # Collect all step results
    all_steps = []
    confidences = []
    ambiguities = []
    step_latencies = []
    step_costs = []
    fallback_attempts = []
    failure_counter: Counter = Counter()
    position_failures: dict[str, int] = {"early": 0, "mid": 0, "late": 0}

    for wf_result in results:
        num_steps = len(wf_result.workflow.steps)

        for sr in wf_result.step_results:
            all_steps.append(sr)
            step_latencies.append(sr.latency_ms)
            step_costs.append(sr.model_cost_usd)

            if sr.grounding_result:
                confidences.append(sr.grounding_result.confidence)
                ambiguities.append(sr.grounding_result.ambiguity_score)

                if sr.grounding_result.fallback_used:
                    fallback_attempts.append(sr.grounding_result.success)

            if sr.outcome not in (StepOutcome.SUCCESS, StepOutcome.HEALED, StepOutcome.SKIPPED):
                failure_counter[sr.outcome.value] += 1

                # Categorize by position
                if num_steps > 0:
                    position = sr.step.step_number / num_steps
                    if position < 0.33:
                        position_failures["early"] += 1
                    elif position < 0.66:
                        position_failures["mid"] += 1
                    else:
                        position_failures["late"] += 1

    metrics.total_steps = sum(len(r.workflow.steps) for r in results)
    metrics.total_steps_executed = len(all_steps)

    # Step success rate
    step_successes = sum(
        1 for sr in all_steps
        if sr.outcome in (StepOutcome.SUCCESS, StepOutcome.HEALED, StepOutcome.SKIPPED)
    )
    metrics.step_success_rate = step_successes / len(all_steps) if all_steps else 0

    # Healing rate
    healing_attempts = [sr for sr in all_steps if sr.healing_attempted]
    if healing_attempts:
        metrics.healing_rate = sum(1 for h in healing_attempts if h.healing_succeeded) / len(healing_attempts)

    # Failure distribution
    metrics.failure_distribution = dict(failure_counter.most_common())
    metrics.failure_by_step_position = position_failures

    # Failure by workflow length
    length_buckets: dict[str, list[bool]] = {"short(1-5)": [], "medium(6-15)": [], "long(16+)": []}
    for r in results:
        n = len(r.workflow.steps)
        bucket = "short(1-5)" if n <= 5 else "medium(6-15)" if n <= 15 else "long(16+)"
        length_buckets[bucket].append(r.success)
    metrics.failure_by_workflow_length = {
        k: (sum(v) / len(v)) if v else 0.0 for k, v in length_buckets.items()
    }

    # Grounding confidence
    if confidences:
        metrics.grounding_confidence_mean = statistics.mean(confidences)
        metrics.grounding_confidence_p50 = statistics.median(confidences)
        sorted_conf = sorted(confidences)
        p10_idx = max(0, int(len(sorted_conf) * 0.1) - 1)
        metrics.grounding_confidence_p10 = sorted_conf[p10_idx]

    if ambiguities:
        metrics.grounding_ambiguity_mean = statistics.mean(ambiguities)

    # Fallback analysis
    if fallback_attempts:
        metrics.fallback_rate = len(fallback_attempts) / len(all_steps)
        metrics.fallback_success_rate = sum(1 for f in fallback_attempts if f) / len(fallback_attempts)

    # Latency
    if step_latencies:
        metrics.latency_per_step_mean_ms = statistics.mean(step_latencies)
        sorted_lat = sorted(step_latencies)
        metrics.latency_per_step_p95_ms = sorted_lat[int(len(sorted_lat) * 0.95)]

    workflow_latencies = [r.total_latency_ms for r in results]
    if workflow_latencies:
        metrics.latency_per_workflow_mean_ms = statistics.mean(workflow_latencies)

    # Cost
    if step_costs:
        metrics.cost_per_step_mean_usd = statistics.mean(step_costs)

    workflow_costs = [r.total_model_cost_usd for r in results]
    if workflow_costs:
        metrics.cost_per_workflow_mean_usd = statistics.mean(workflow_costs)

    model_calls = [r.total_model_calls for r in results]
    if model_calls:
        metrics.model_calls_per_workflow_mean = statistics.mean(model_calls)

    # Perception analysis
    a11y_checks = []
    element_counts = []
    for sr in all_steps:
        if sr.screen_before and sr.screen_before.metadata:
            a11y_avail = sr.screen_before.metadata.get("a11y_available")
            if a11y_avail is not None:
                a11y_checks.append(a11y_avail)
            el_count = sr.screen_before.metadata.get("graph_element_count")
            if el_count is not None:
                element_counts.append(el_count)

    if a11y_checks:
        metrics.a11y_availability_rate = sum(1 for a in a11y_checks if a) / len(a11y_checks)
    if element_counts:
        metrics.graph_element_count_mean = statistics.mean(element_counts)

    return metrics


def compare_strategies(
    results_by_strategy: dict[str, list[WorkflowResult]],
) -> dict[str, Any]:
    """Compare metrics across different strategies.

    Returns a comparison table suitable for printing or serialization.
    This is the main output for deciding which approach to use.
    """
    comparison: dict[str, Any] = {"strategies": {}, "dimensions": []}

    dimensions = [
        "workflow_success_rate",
        "step_success_rate",
        "grounding_confidence_mean",
        "fallback_rate",
        "latency_per_step_mean_ms",
        "cost_per_workflow_mean_usd",
        "model_calls_per_workflow_mean",
    ]
    comparison["dimensions"] = dimensions

    for strategy_name, results in results_by_strategy.items():
        m = compute_metrics(results)
        comparison["strategies"][strategy_name] = {
            dim: getattr(m, dim, 0) for dim in dimensions
        }
        comparison["strategies"][strategy_name]["failure_distribution"] = m.failure_distribution
        comparison["strategies"][strategy_name]["total_workflows"] = m.total_workflows

    return comparison


def generate_report(
    results: list[WorkflowResult],
    output_path: Optional[Path] = None,
) -> str:
    """Generate a human-readable eval report.

    Designed to surface the signals that matter for go/no-go decisions.
    """
    from pathlib import Path as _Path

    metrics = compute_metrics(results)

    lines = [
        "=" * 70,
        "  GUI AUTOMATION EVAL REPORT",
        "=" * 70,
        "",
        "SUMMARY",
        f"  Workflows tested:     {metrics.total_workflows}",
        f"  Steps executed:       {metrics.total_steps_executed} / {metrics.total_steps}",
        f"  Workflow success rate: {metrics.workflow_success_rate:.1%}",
        f"  Step success rate:    {metrics.step_success_rate:.1%}",
        f"  Healing rate:         {metrics.healing_rate:.1%}",
        "",
        "GROUNDING QUALITY",
        f"  Confidence (mean):    {metrics.grounding_confidence_mean:.3f}",
        f"  Confidence (p50):     {metrics.grounding_confidence_p50:.3f}",
        f"  Confidence (p10):     {metrics.grounding_confidence_p10:.3f}",
        f"  Ambiguity (mean):     {metrics.grounding_ambiguity_mean:.3f}",
        f"  Fallback rate:        {metrics.fallback_rate:.1%}",
        f"  Fallback success:     {metrics.fallback_success_rate:.1%}",
        "",
        "PERFORMANCE",
        f"  Step latency (mean):  {metrics.latency_per_step_mean_ms:.0f}ms",
        f"  Step latency (p95):   {metrics.latency_per_step_p95_ms:.0f}ms",
        f"  Workflow latency:     {metrics.latency_per_workflow_mean_ms:.0f}ms",
        "",
        "COST",
        f"  Per step (mean):      ${metrics.cost_per_step_mean_usd:.5f}",
        f"  Per workflow (mean):  ${metrics.cost_per_workflow_mean_usd:.4f}",
        f"  Model calls/workflow: {metrics.model_calls_per_workflow_mean:.1f}",
        "",
        "FAILURE ANALYSIS",
    ]

    if metrics.failure_distribution:
        for failure_type, count in sorted(
            metrics.failure_distribution.items(), key=lambda x: -x[1]
        ):
            lines.append(f"  {failure_type:30s} {count}")
    else:
        lines.append("  No failures!")

    lines.extend([
        "",
        "FAILURE BY POSITION",
        f"  Early (0-33%):        {metrics.failure_by_step_position.get('early', 0)}",
        f"  Mid (33-66%):         {metrics.failure_by_step_position.get('mid', 0)}",
        f"  Late (66-100%):       {metrics.failure_by_step_position.get('late', 0)}",
        "",
        "SUCCESS BY WORKFLOW LENGTH",
    ])
    for bucket, rate in metrics.failure_by_workflow_length.items():
        lines.append(f"  {bucket:20s}  {rate:.1%}")

    lines.extend([
        "",
        "PERCEPTION",
        f"  A11y tree available:  {metrics.a11y_availability_rate:.1%}",
        f"  Elements per screen:  {metrics.graph_element_count_mean:.0f}",
        "",
        "=" * 70,
        "",
        "KEY SIGNALS FOR PRODUCT FEASIBILITY:",
        "",
    ])

    # Automated insights
    if metrics.workflow_success_rate >= 0.9:
        lines.append("  ✓ High workflow success rate — core loop works")
    elif metrics.workflow_success_rate >= 0.7:
        lines.append("  △ Moderate success — investigate failure modes")
    else:
        lines.append("  ✗ Low success rate — fundamental approach may need rethinking")

    if metrics.fallback_rate > 0.3:
        lines.append(f"  ! High fallback rate ({metrics.fallback_rate:.0%}) — "
                     f"graph matching alone insufficient for these UIs")

    if metrics.cost_per_workflow_mean_usd > 0.10:
        lines.append(f"  ! Cost per workflow (${metrics.cost_per_workflow_mean_usd:.3f}) "
                     f"may be too high for target pricing")

    if metrics.a11y_availability_rate < 0.5:
        lines.append(f"  ! A11y trees available only {metrics.a11y_availability_rate:.0%} "
                     f"of the time — must support screenshot-only path")

    most_common_failure = max(
        metrics.failure_distribution.items(), key=lambda x: x[1], default=None
    )
    if most_common_failure:
        lines.append(f"  ! Most common failure: {most_common_failure[0]} "
                     f"({most_common_failure[1]} occurrences)")

    lines.append("")

    report = "\n".join(lines)

    if output_path:
        output_path.write_text(report)

    return report


# For optional import
from pathlib import Path as _OptionalPath
Optional = type(None)  # just to avoid import if not used at module level
