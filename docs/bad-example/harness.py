"""
Eval harness: orchestrates experiments across the full matrix of
perception × grounding × model × workflow.

The harness is designed to surface unknown-unknowns by collecting
granular data at every step, not just pass/fail at the workflow level.

Usage:
    harness = EvalHarness(config)
    results = await harness.run()
    report = harness.generate_report(results)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..core.types import (
    Action,
    ActionType,
    EvalConfig,
    EvalSuite,
    GroundingStrategy,
    PerceptionMode,
    ScreenState,
    StepOutcome,
    StepResult,
    Workflow,
    WorkflowResult,
    WorkflowStep,
)
from ..grounding.strategies import GroundingProvider, create_grounding
from ..models.providers import ModelProvider, create_model
from ..perception.providers import PerceptionProvider, create_perception

logger = logging.getLogger(__name__)


class EvalHarness:
    """Orchestrates eval runs across the experiment matrix.

    The harness handles:
    1. Environment setup (browser launch, app positioning)
    2. Perception capture at each step
    3. Grounding via configured strategy
    4. Action execution (or simulation for dry runs)
    5. Post-action verification
    6. Metric collection and reporting

    It does NOT handle the actual browser/desktop control — that's
    delegated to an ActionExecutor (Playwright for browser, OS automation
    for desktop). This separation lets you eval grounding accuracy
    without needing to execute actions.
    """

    def __init__(
        self,
        config: EvalConfig,
        action_executor: Optional[Any] = None,
        dry_run: bool = False,
    ):
        self.config = config
        self.action_executor = action_executor
        self.dry_run = dry_run
        self._model: Optional[ModelProvider] = None
        self._perception: Optional[PerceptionProvider] = None
        self._grounding: Optional[GroundingProvider] = None
        self._results_dir = config.output_dir / config.eval_id
        self._playwright = None
        self._browser = None
        self._page = None

    async def setup(self):
        """Initialize all components."""
        self._results_dir.mkdir(parents=True, exist_ok=True)

        # Model
        self._model = create_model(
            provider=self.config.model_provider,
            model=self.config.model_name,
        )

        # Browser (for browser workflows)
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.config.headless,
            )
            self._page = await self._browser.new_page(
                viewport={
                    "width": self.config.screenshot_resolution[0],
                    "height": self.config.screenshot_resolution[1],
                },
            )
        except ImportError:
            logger.warning("Playwright not installed. Browser workflows will fail.")

        # Perception
        self._perception = create_perception(
            mode=self.config.perception_mode,
            playwright_page=self._page,
        )

        # Grounding
        self._grounding = create_grounding(
            strategy=self.config.grounding_strategy,
            model=self._model,
        )

        logger.info(
            f"Eval harness ready: perception={self.config.perception_mode.name}, "
            f"grounding={self.config.grounding_strategy.name}, "
            f"model={self.config.model_provider}/{self.config.model_name}"
        )

    async def teardown(self):
        """Clean up resources."""
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def run_workflow(self, workflow: Workflow) -> WorkflowResult:
        """Execute a single workflow and collect results.

        This is the core eval loop. For each step:
        1. Capture current screen state (perception)
        2. Attempt to locate the target element (grounding)
        3. Execute the action (or simulate in dry run)
        4. Verify post-action state
        5. Record everything
        """
        logger.info(f"Running workflow: {workflow.name} ({len(workflow.steps)} steps)")
        start = time.monotonic()

        step_results: list[StepResult] = []
        total_model_calls = 0
        total_model_cost = 0.0

        # Navigate to starting state
        if workflow.target_url and self._page:
            try:
                await self._page.goto(workflow.target_url, wait_until="domcontentloaded")
                await asyncio.sleep(1.0)  # Wait for page to settle
            except Exception as e:
                logger.error(f"Failed to navigate to {workflow.target_url}: {e}")
                return WorkflowResult(
                    workflow=workflow,
                    step_results=[],
                    success=False,
                    failure_reason=f"navigation_failed: {e}",
                )

        for step in workflow.steps:
            step_result = await self._execute_step(step)
            step_results.append(step_result)

            total_model_calls += step_result.model_calls
            total_model_cost += step_result.model_cost_usd

            # Save step screenshot for debugging
            if self.config.record_screenshots and step_result.screen_after:
                self._save_step_artifact(workflow, step, step_result)

            # Stop on critical failure
            if step_result.outcome in (
                StepOutcome.AUTH_BLOCKED,
                StepOutcome.ANTI_BOT_BLOCKED,
                StepOutcome.NAVIGATION_ERROR,
            ):
                logger.warning(
                    f"Critical failure at step {step.step_number}: {step_result.outcome.value}"
                )
                break

            # Stop if grounding failed and no healing
            if step_result.outcome == StepOutcome.GROUNDING_FAILURE and not step_result.healing_succeeded:
                logger.warning(
                    f"Grounding failed at step {step.step_number}, stopping workflow"
                )
                break

        total_latency = (time.monotonic() - start) * 1000

        # Determine overall success
        all_ok = all(
            r.outcome in (StepOutcome.SUCCESS, StepOutcome.HEALED, StepOutcome.SKIPPED)
            for r in step_results
        )
        failed_step = next(
            (r.step.step_number for r in step_results
             if r.outcome not in (StepOutcome.SUCCESS, StepOutcome.HEALED, StepOutcome.SKIPPED)),
            None,
        )

        result = WorkflowResult(
            workflow=workflow,
            step_results=step_results,
            success=all_ok and len(step_results) == len(workflow.steps),
            total_latency_ms=total_latency,
            total_model_cost_usd=total_model_cost,
            total_model_calls=total_model_calls,
            failure_step=failed_step,
            failure_reason=next(
                (r.error_message for r in step_results
                 if r.outcome not in (StepOutcome.SUCCESS, StepOutcome.HEALED, StepOutcome.SKIPPED)),
                None,
            ),
            environment=self._collect_environment_info(),
        )

        # Save workflow result
        self._save_workflow_result(result)

        return result

    async def _execute_step(self, step: WorkflowStep) -> StepResult:
        """Execute a single workflow step with retry logic."""
        start = time.monotonic()
        model_calls = 0
        model_cost = 0.0

        for attempt in range(step.retry_budget + 1):
            # 1. Capture current state
            try:
                current_state = await self._perception.capture()
            except Exception as e:
                logger.error(f"Perception failed at step {step.step_number}: {e}")
                return StepResult(
                    step=step,
                    outcome=StepOutcome.ACTION_FAILED,
                    error_message=f"perception_error: {e}",
                    latency_ms=(time.monotonic() - start) * 1000,
                )

            # 2. Ground the target element
            grounding_result = await self._grounding.ground(step, current_state)
            model_calls += 1 if grounding_result.fallback_used else 0
            if grounding_result.debug_info.get("model_cost"):
                model_cost += grounding_result.debug_info["model_cost"]

            if grounding_result.success:
                # 3. Execute action (or simulate)
                if self.dry_run:
                    outcome = StepOutcome.SUCCESS
                else:
                    outcome = await self._execute_action(step, grounding_result)

                # 4. Capture post-action state
                await asyncio.sleep(0.5)  # Wait for UI to settle
                post_state = await self._perception.capture()

                return StepResult(
                    step=step,
                    outcome=outcome,
                    grounding_result=grounding_result,
                    screen_before=current_state,
                    screen_after=post_state,
                    latency_ms=(time.monotonic() - start) * 1000,
                    model_calls=model_calls,
                    model_cost_usd=model_cost,
                    retries=attempt,
                )

            # Grounding failed — retry after brief wait
            if attempt < step.retry_budget:
                logger.info(
                    f"Step {step.step_number} grounding attempt {attempt + 1} failed "
                    f"(confidence={grounding_result.confidence:.2f}). Retrying..."
                )
                await asyncio.sleep(1.0)

        # All retries exhausted
        return StepResult(
            step=step,
            outcome=StepOutcome.GROUNDING_FAILURE,
            grounding_result=grounding_result,
            screen_before=current_state,
            latency_ms=(time.monotonic() - start) * 1000,
            model_calls=model_calls,
            model_cost_usd=model_cost,
            retries=step.retry_budget,
            error_message=f"grounding_failed: {grounding_result.failure_reason}",
        )

    async def _execute_action(self, step: WorkflowStep, grounding: Any) -> StepOutcome:
        """Execute the actual UI action. Returns outcome."""
        if not self._page or not step.action:
            return StepOutcome.ACTION_FAILED

        try:
            action = step.action
            coords = grounding.coordinates

            if action.action_type == ActionType.CLICK and coords:
                vp = self._page.viewport_size or {"width": 1920, "height": 1080}
                await self._page.mouse.click(
                    coords[0] * vp["width"],
                    coords[1] * vp["height"],
                )
            elif action.action_type == ActionType.TYPE_TEXT and action.text:
                if coords:
                    vp = self._page.viewport_size or {"width": 1920, "height": 1080}
                    await self._page.mouse.click(
                        coords[0] * vp["width"],
                        coords[1] * vp["height"],
                    )
                await self._page.keyboard.type(action.text, delay=50)
            elif action.action_type == ActionType.PRESS_KEY and action.key:
                await self._page.keyboard.press(action.key)
            elif action.action_type == ActionType.NAVIGATE and action.text:
                await self._page.goto(action.text)
            elif action.action_type == ActionType.SCROLL and action.scroll_delta:
                await self._page.mouse.wheel(
                    action.scroll_delta[0], action.scroll_delta[1]
                )
            elif action.action_type == ActionType.WAIT:
                await asyncio.sleep(step.timeout_seconds)
            else:
                logger.warning(f"Unhandled action type: {action.action_type}")
                return StepOutcome.ACTION_FAILED

            return StepOutcome.SUCCESS

        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return StepOutcome.ACTION_FAILED

    def _collect_environment_info(self) -> dict[str, Any]:
        """Collect environment metadata for the eval report."""
        import platform
        return {
            "platform": platform.system(),
            "platform_version": platform.version(),
            "python_version": platform.python_version(),
            "perception_mode": self.config.perception_mode.name,
            "grounding_strategy": self.config.grounding_strategy.name,
            "model": f"{self.config.model_provider}/{self.config.model_name}",
            "resolution": self.config.screenshot_resolution,
            "headless": self.config.headless,
            "timestamp": datetime.now().isoformat(),
        }

    def _save_step_artifact(
        self, workflow: Workflow, step: WorkflowStep, result: StepResult
    ):
        """Save debugging artifacts for a step."""
        step_dir = self._results_dir / workflow.workflow_id / f"step_{step.step_number:03d}"
        step_dir.mkdir(parents=True, exist_ok=True)

        if result.screen_before and result.screen_before.screenshot_base64:
            import base64
            with open(step_dir / "before.png", "wb") as f:
                f.write(base64.b64decode(result.screen_before.screenshot_base64))

        if result.screen_after and result.screen_after.screenshot_base64:
            import base64
            with open(step_dir / "after.png", "wb") as f:
                f.write(base64.b64decode(result.screen_after.screenshot_base64))

        # Save grounding debug info
        if result.grounding_result:
            with open(step_dir / "grounding.json", "w") as f:
                json.dump({
                    "success": result.grounding_result.success,
                    "confidence": result.grounding_result.confidence,
                    "ambiguity": result.grounding_result.ambiguity_score,
                    "strategy": result.grounding_result.strategy_used.name if result.grounding_result.strategy_used else None,
                    "fallback_used": result.grounding_result.fallback_used,
                    "candidates": result.grounding_result.candidates_considered,
                    "debug": result.grounding_result.debug_info,
                }, f, indent=2, default=str)

    def _save_workflow_result(self, result: WorkflowResult):
        """Save complete workflow result as JSON."""
        wf_dir = self._results_dir / result.workflow.workflow_id
        wf_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "workflow_id": result.workflow.workflow_id,
            "workflow_name": result.workflow.name,
            "success": result.success,
            "step_success_rate": result.step_success_rate,
            "healing_rate": result.healing_rate,
            "total_latency_ms": result.total_latency_ms,
            "total_model_cost_usd": result.total_model_cost_usd,
            "total_model_calls": result.total_model_calls,
            "failure_step": result.failure_step,
            "failure_reason": result.failure_reason,
            "failure_distribution": result.failure_distribution,
            "environment": result.environment,
            "steps": [
                {
                    "step_number": r.step.step_number,
                    "description": r.step.description,
                    "outcome": r.outcome.value,
                    "confidence": r.grounding_result.confidence if r.grounding_result else None,
                    "latency_ms": r.latency_ms,
                    "model_calls": r.model_calls,
                    "model_cost_usd": r.model_cost_usd,
                    "retries": r.retries,
                    "error": r.error_message,
                }
                for r in result.step_results
            ],
        }

        with open(wf_dir / "result.json", "w") as f:
            json.dump(summary, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Suite runner: factorial experiments
# ---------------------------------------------------------------------------

class SuiteRunner:
    """Run a suite of eval configs and aggregate results.

    Supports factorial experiments like:
    - Same workflow, vary model (Anthropic vs OpenAI)
    - Same model, vary perception mode
    - Same everything, vary grounding strategy
    """

    def __init__(self, suite: EvalSuite, workflows: list[Workflow]):
        self.suite = suite
        self.workflows = {w.workflow_id: w for w in workflows}

    async def run(self) -> list[WorkflowResult]:
        """Run all configs in the suite sequentially."""
        all_results: list[WorkflowResult] = []

        for config in self.suite.configs:
            logger.info(f"\n{'='*60}")
            logger.info(f"Running eval config: {config.eval_id}")
            logger.info(f"  Perception: {config.perception_mode.name}")
            logger.info(f"  Grounding: {config.grounding_strategy.name}")
            logger.info(f"  Model: {config.model_provider}/{config.model_name}")
            logger.info(f"{'='*60}")

            harness = EvalHarness(config)
            try:
                await harness.setup()

                for wf_id in config.workflows:
                    workflow = self.workflows.get(wf_id)
                    if workflow is None:
                        logger.warning(f"Workflow {wf_id} not found, skipping")
                        continue

                    result = await harness.run_workflow(workflow)
                    all_results.append(result)

                    logger.info(
                        f"  {workflow.name}: "
                        f"{'PASS' if result.success else 'FAIL'} "
                        f"(SSR={result.step_success_rate:.1%}, "
                        f"latency={result.total_latency_ms:.0f}ms, "
                        f"cost=${result.total_model_cost_usd:.4f})"
                    )
            finally:
                await harness.teardown()

        return all_results


# ---------------------------------------------------------------------------
# Quick eval helpers
# ---------------------------------------------------------------------------

async def quick_eval(
    workflow: Workflow,
    perception_mode: PerceptionMode = PerceptionMode.HYBRID,
    grounding_strategy: GroundingStrategy = GroundingStrategy.HYBRID_GRAPH_LLM,
    model_provider: str = "anthropic",
    model_name: str = "claude-sonnet-4-20250514",
    dry_run: bool = False,
) -> WorkflowResult:
    """One-shot eval of a single workflow with a single config.

    Good for quick iteration during development.
    """
    config = EvalConfig(
        perception_mode=perception_mode,
        grounding_strategy=grounding_strategy,
        model_provider=model_provider,
        model_name=model_name,
    )
    harness = EvalHarness(config, dry_run=dry_run)

    try:
        await harness.setup()
        result = await harness.run_workflow(workflow)
        return result
    finally:
        await harness.teardown()


def build_comparison_suite(
    workflows: list[str],
    name: str = "model_comparison",
) -> EvalSuite:
    """Build a standard comparison suite: Anthropic vs OpenAI, all perception modes.

    This is the default experiment matrix for answering
    "which combination works best for our use case?"
    """
    configs = []

    models = [
        ("anthropic", "claude-sonnet-4-20250514"),
        ("openai", "gpt-4o"),
        ("openai", "gpt-4o-mini"),
    ]

    strategies = [
        (PerceptionMode.SCREENSHOT, GroundingStrategy.LLM_VISION),
        (PerceptionMode.ACCESSIBILITY_TREE, GroundingStrategy.LLM_STRUCTURED),
        (PerceptionMode.UI_GRAPH, GroundingStrategy.GRAPH_MATCH),
        (PerceptionMode.HYBRID, GroundingStrategy.HYBRID_GRAPH_LLM),
    ]

    for provider, model in models:
        for perception, grounding in strategies:
            # Graph matching doesn't use a model, so only run once
            if grounding == GroundingStrategy.GRAPH_MATCH and provider != "anthropic":
                continue

            configs.append(EvalConfig(
                perception_mode=perception,
                grounding_strategy=grounding,
                model_provider=provider,
                model_name=model,
                workflows=workflows,
                tags=[provider, model, perception.name, grounding.name],
            ))

    return EvalSuite(
        name=name,
        configs=configs,
        description=f"Comparison across {len(models)} models × {len(strategies)} strategies",
    )
