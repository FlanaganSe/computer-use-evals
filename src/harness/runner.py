"""Core run loop: setup → adapter loop → grade → write artifacts."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.util
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from harness.adapters.codex_subscription import CodexSubscriptionAdapter
from harness.adapters.deterministic import DeterministicAdapter
from harness.adapters.openai_cu import OpenAIComputerUseAdapter
from harness.adapters.structured_state_desktop import StructuredStateDesktopAdapter
from harness.environments.browser import BrowserEnvironment
from harness.environments.macos import MacOSDesktopEnvironment
from harness.failures import FailureCategory
from harness.graders import evaluate_milestones, grade
from harness.reporting import generate_report
from harness.task_loader import load_task
from harness.types import (
    Action,
    ActionType,
    GraderResult,
    StepRecord,
    Trace,
)

_AdapterFactory = type | Callable[[], Any]

ADAPTERS: dict[str, _AdapterFactory] = {
    "deterministic": DeterministicAdapter,
    "openai_cu": OpenAIComputerUseAdapter,
    "openai_cu_hybrid": lambda: OpenAIComputerUseAdapter(hybrid=True),
    "codex_subscription": CodexSubscriptionAdapter,
    "structured_state_desktop": StructuredStateDesktopAdapter,
}

ENVIRONMENTS: dict[str, type] = {
    "browser": BrowserEnvironment,
    "macos_desktop": MacOSDesktopEnvironment,
}


def _flatten_action(action: Action) -> dict[str, Any]:
    """Flatten an Action to a trace-friendly dict: {type, ...params}."""
    return {"type": action.action_type.value, **action.params}


def run_task(
    task_path: str,
    adapter_name: str,
    max_steps: int = 30,
    runs_dir: str = "runs",
) -> Path:
    """Run a single task with the specified adapter. Returns the run directory."""
    return asyncio.run(_run_task_async(task_path, adapter_name, max_steps, runs_dir))


async def _run_task_async(
    task_path: str,
    adapter_name: str,
    max_steps: int,
    runs_dir: str,
) -> Path:
    task = load_task(task_path)

    # Create adapter
    adapter_cls = ADAPTERS.get(adapter_name)
    if adapter_cls is None:
        msg = f"Unknown adapter: {adapter_name}. Available: {list(ADAPTERS.keys())}"
        raise ValueError(msg)
    adapter = adapter_cls()

    # Create run directory
    now = datetime.now(tz=UTC)
    timestamp = now.strftime("%Y-%m-%dT%H-%M-%S")
    run_name = f"{timestamp}_{task.task_id}_{adapter_name}"
    run_dir = Path(runs_dir) / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write resolved task and config
    _write_resolved_task(task_path, task, run_dir)
    _write_config(adapter_name, max_steps, run_dir)

    # Initialize environment and setup
    setup_module = _load_setup_module(task.setup_script)
    env_name = task.environment or "browser"
    env_cls = ENVIRONMENTS.get(env_name)
    if env_cls is None:
        msg = f"Unknown environment: {env_name}. Available: {list(ENVIRONMENTS.keys())}"
        raise ValueError(msg)
    env = env_cls()
    trace = Trace(task_id=task.task_id, adapter=adapter_name, started_at=now)
    grader_result = GraderResult(
        passed=False, method="not_run", explanation="Grading did not execute"
    )

    try:
        # Run setup script inside try so cleanup always runs
        if setup_module is not None and hasattr(setup_module, "setup"):
            setup_module.setup()

        await env.setup(task, run_dir)

        # Main loop
        step_num = 0
        done = False
        for _ in range(max_steps):
            obs_type = adapter.observation_request()
            observation = await env.collect_observation(obs_type)
            actions = adapter.decide(observation, task)

            for action in actions:
                step_num += 1

                if action.action_type == ActionType.DONE:
                    trace.steps.append(
                        StepRecord(step=step_num, action={"type": "done"}, result="done")
                    )
                    done = True
                    break

                if action.action_type == ActionType.FAIL:
                    reason = action.params.get("reason", "Agent declared failure")
                    trace.steps.append(
                        StepRecord(
                            step=step_num,
                            action=_flatten_action(action),
                            result=f"fail:{reason}",
                            error=reason,
                        )
                    )
                    trace.outcome = "fail"
                    trace.failure_category = FailureCategory.PLANNING
                    done = True
                    break

                try:
                    result = await env.execute_action(action)
                except Exception as exc:
                    trace.steps.append(
                        StepRecord(
                            step=step_num,
                            action=_flatten_action(action),
                            result=f"error:{exc}",
                            error=str(exc),
                        )
                    )
                    trace.outcome = "error"
                    trace.failure_category = FailureCategory.EXECUTION
                    done = True
                    break

                trace.steps.append(
                    StepRecord(
                        step=step_num,
                        action=_flatten_action(action),
                        result=result,
                    )
                )

            if done:
                break

        trace.total_steps = step_num
        trace.completed_at = datetime.now(tz=UTC)

        # Always grade by outcome — even a "failed" adapter might have produced
        # the right result (outcome-first grading principle)
        grader_result = grade(task, run_dir)
        if grader_result.passed:
            trace.outcome = "pass"
        elif trace.outcome == "error":
            pass  # keep error status
        else:
            trace.outcome = "fail"

        # Evaluate milestones if the task defines them.
        # Wrapped in try/except so a milestone evaluation failure cannot
        # corrupt the primary grade result or trace outcome.
        if task.milestones:
            try:
                trace.milestone_results = evaluate_milestones(task, run_dir)
            except Exception:
                pass  # milestone_results stays [] — primary outcome unaffected

            # Refine failure categorization using milestone evidence:
            # If final grading failed but some milestones passed, we know
            # the run progressed partially — the failure is likely in
            # planning (chose wrong actions after a certain point) rather
            # than a total harness or perception failure.
            if not grader_result.passed and trace.failure_category is None:
                passed_ids = [mr.id for mr in trace.milestone_results if mr.passed]
                failed_ids = [mr.id for mr in trace.milestone_results if not mr.passed]
                if passed_ids and failed_ids:
                    # Partial progress: the agent got somewhere but didn't finish
                    trace.failure_category = FailureCategory.PLANNING

    except Exception as exc:
        trace.completed_at = datetime.now(tz=UTC)
        trace.outcome = "error"
        trace.failure_category = FailureCategory.HARNESS
        trace.total_steps = len(trace.steps)
        grader_result = GraderResult(
            passed=False,
            method="error",
            explanation=f"Harness error: {exc}",
        )
    finally:
        await env.teardown()
        with contextlib.suppress(Exception):
            _run_cleanup(setup_module, task.cleanup_script)

    # Attach cost metadata if the adapter provides it
    if hasattr(adapter, "get_cost_metadata"):
        trace.metadata = adapter.get_cost_metadata()

    # Write artifacts
    _write_trace(trace, run_dir)
    _write_grade(grader_result, run_dir)
    report_md = generate_report(task, trace, grader_result, run_dir)
    (run_dir / "report.md").write_text(report_md)

    # Persist decision-point evidence if the adapter collected it
    if hasattr(adapter, "get_step_evidence"):
        evidence = adapter.get_step_evidence()
        if evidence:
            _write_evidence(evidence, run_dir)

    return run_dir


def _write_resolved_task(task_path: str, task: Any, run_dir: Path) -> None:
    """Write the resolved task YAML to the run directory."""
    data = task.model_dump(by_alias=True)
    (run_dir / "task.yaml").write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def _write_config(adapter_name: str, max_steps: int, run_dir: Path) -> None:
    config = {"adapter": adapter_name, "max_steps": max_steps}
    (run_dir / "config.json").write_text(json.dumps(config, indent=2))


def _write_trace(trace: Trace, run_dir: Path) -> None:
    data = json.loads(trace.model_dump_json(by_alias=True))
    (run_dir / "trace.json").write_text(json.dumps(data, indent=2))


def _write_grade(result: GraderResult, run_dir: Path) -> None:
    data = json.loads(result.model_dump_json())
    (run_dir / "grade.json").write_text(json.dumps(data, indent=2))


def _write_evidence(evidence: list[dict[str, Any]], run_dir: Path) -> None:
    """Persist decision-point evidence from the adapter."""
    (run_dir / "evidence.json").write_text(json.dumps(evidence, indent=2, default=str))


def _load_setup_module(setup_script: str | None) -> Any:
    """Import a setup/cleanup script module."""
    if setup_script is None:
        return None

    path = Path(setup_script)
    if not path.exists():
        msg = f"Setup script not found: {setup_script}"
        raise FileNotFoundError(msg)

    spec = importlib.util.spec_from_file_location("setup_module", path)
    if spec is None or spec.loader is None:
        msg = f"Could not load setup script: {setup_script}"
        raise ImportError(msg)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cleanup(setup_module: Any, cleanup_script: str | None) -> None:
    """Run cleanup: first try the setup module's cleanup(), then a separate script."""
    if setup_module is not None and hasattr(setup_module, "cleanup"):
        setup_module.cleanup()

    if cleanup_script is not None:
        cleanup_mod = _load_setup_module(cleanup_script)
        if cleanup_mod is not None and hasattr(cleanup_mod, "cleanup"):
            cleanup_mod.cleanup()
