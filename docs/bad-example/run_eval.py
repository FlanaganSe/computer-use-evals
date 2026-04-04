"""
Example eval script: run a browser workflow through the eval harness.

Usage:
    # Quick single eval
    python -m scripts.run_eval --workflow tasks/browser/search.json

    # Full comparison suite
    python -m scripts.run_eval --suite --workflows tasks/browser/search.json

    # Dry run (grounding only, no action execution)
    python -m scripts.run_eval --workflow tasks/browser/search.json --dry-run
"""

import asyncio
import argparse
import json
import logging
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gpa_eval.core.types import (
    Action,
    ActionType,
    BoundingBox,
    EvalConfig,
    GroundingStrategy,
    PerceptionMode,
    UIElement,
    UIGraph,
    Workflow,
    WorkflowStep,
)
from gpa_eval.eval.harness import EvalHarness, SuiteRunner, build_comparison_suite, quick_eval
from gpa_eval.eval.metrics import compute_metrics, generate_report


# ---------------------------------------------------------------------------
# Sample workflow definitions
# ---------------------------------------------------------------------------

def create_sample_search_workflow() -> Workflow:
    """A simple Google search workflow for testing the eval harness."""
    return Workflow(
        workflow_id="google_search_01",
        name="Google Search",
        description="Navigate to Google, search for a term, verify results appear",
        target_url="https://www.google.com",
        platform="browser",
        tags=["browser", "search", "simple"],
        steps=[
            WorkflowStep(
                step_number=1,
                description="Click on the search input field",
                intent_category="navigation",
                action=Action(
                    action_type=ActionType.CLICK,
                    target=UIElement(
                        element_id="search_input",
                        role="input",
                        text="",
                        name="Search",
                        bbox=BoundingBox(0.25, 0.40, 0.50, 0.04),
                    ),
                ),
                target_graph=UIGraph(
                    elements=[
                        UIElement(
                            element_id="search_input",
                            role="input",
                            text="",
                            name="Search",
                            bbox=BoundingBox(0.25, 0.40, 0.50, 0.04),
                        ),
                        UIElement(
                            element_id="logo",
                            role="img",
                            text="Google",
                            bbox=BoundingBox(0.35, 0.20, 0.30, 0.12),
                        ),
                        UIElement(
                            element_id="search_btn",
                            role="button",
                            text="Google Search",
                            bbox=BoundingBox(0.35, 0.50, 0.12, 0.03),
                        ),
                        UIElement(
                            element_id="lucky_btn",
                            role="button",
                            text="I'm Feeling Lucky",
                            bbox=BoundingBox(0.52, 0.50, 0.14, 0.03),
                        ),
                    ],
                    edges=[
                        ("search_input", "logo"),
                        ("search_input", "search_btn"),
                        ("search_input", "lucky_btn"),
                        ("search_btn", "lucky_btn"),
                    ],
                ),
                risk_level="low",
            ),
            WorkflowStep(
                step_number=2,
                description="Type search query: 'GUI process automation research 2026'",
                intent_category="data_entry",
                action=Action(
                    action_type=ActionType.TYPE_TEXT,
                    text="GUI process automation research 2026",
                ),
                risk_level="low",
            ),
            WorkflowStep(
                step_number=3,
                description="Press Enter to submit the search",
                intent_category="navigation",
                action=Action(
                    action_type=ActionType.PRESS_KEY,
                    key="Enter",
                ),
                risk_level="low",
            ),
            WorkflowStep(
                step_number=4,
                description="Verify search results are displayed",
                intent_category="verification",
                action=Action(
                    action_type=ActionType.ASSERT,
                ),
                risk_level="low",
                timeout_seconds=10.0,
            ),
        ],
    )


def create_sample_form_workflow() -> Workflow:
    """A form-filling workflow to test data entry and multi-step interaction."""
    return Workflow(
        workflow_id="form_fill_01",
        name="Contact Form Fill",
        description="Navigate to a form, fill in fields, submit",
        target_url="https://httpbin.org/forms/post",
        platform="browser",
        tags=["browser", "form", "data_entry", "medium"],
        steps=[
            WorkflowStep(
                step_number=1,
                description="Click the 'Customer name' input field",
                intent_category="navigation",
                action=Action(
                    action_type=ActionType.CLICK,
                    target=UIElement(
                        element_id="custname",
                        role="input",
                        name="Customer name",
                        bbox=BoundingBox(0.1, 0.15, 0.40, 0.03),
                    ),
                ),
                risk_level="low",
            ),
            WorkflowStep(
                step_number=2,
                description="Type customer name: 'Eval Test User'",
                intent_category="data_entry",
                action=Action(
                    action_type=ActionType.TYPE_TEXT,
                    text="Eval Test User",
                ),
                risk_level="low",
            ),
            WorkflowStep(
                step_number=3,
                description="Click the 'Telephone' input field",
                intent_category="navigation",
                action=Action(
                    action_type=ActionType.CLICK,
                    target=UIElement(
                        element_id="telephone",
                        role="input",
                        name="Telephone",
                        bbox=BoundingBox(0.1, 0.25, 0.40, 0.03),
                    ),
                ),
                risk_level="low",
            ),
            WorkflowStep(
                step_number=4,
                description="Type telephone number: '555-0123'",
                intent_category="data_entry",
                action=Action(
                    action_type=ActionType.TYPE_TEXT,
                    text="555-0123",
                ),
                risk_level="low",
            ),
            WorkflowStep(
                step_number=5,
                description="Click the 'Small' pizza size radio button",
                intent_category="selection",
                action=Action(
                    action_type=ActionType.CLICK,
                    target=UIElement(
                        element_id="size_small",
                        role="radio",
                        text="Small",
                        bbox=BoundingBox(0.1, 0.40, 0.06, 0.02),
                    ),
                ),
                risk_level="low",
            ),
            WorkflowStep(
                step_number=6,
                description="Click the Submit button",
                intent_category="submission",
                action=Action(
                    action_type=ActionType.CLICK,
                    target=UIElement(
                        element_id="submit",
                        role="button",
                        text="Submit",
                        bbox=BoundingBox(0.1, 0.80, 0.10, 0.03),
                    ),
                ),
                risk_level="medium",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def run_quick_eval(args):
    """Run a single workflow eval."""
    workflow = create_sample_search_workflow()

    perception = PerceptionMode[args.perception.upper()]
    grounding = GroundingStrategy[args.grounding.upper()]

    result = await quick_eval(
        workflow=workflow,
        perception_mode=perception,
        grounding_strategy=grounding,
        model_provider=args.model_provider,
        model_name=args.model_name,
        dry_run=args.dry_run,
    )

    report = generate_report([result])
    print(report)

    return result


async def run_comparison_suite(args):
    """Run comparison suite across models and strategies."""
    workflows = [
        create_sample_search_workflow(),
        create_sample_form_workflow(),
    ]

    suite = build_comparison_suite(
        workflows=[w.workflow_id for w in workflows],
        name="poc_comparison",
    )

    # Filter to only models the user has keys for
    import os
    filtered_configs = []
    for config in suite.configs:
        if config.model_provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
            filtered_configs.append(config)
        elif config.model_provider == "openai" and os.environ.get("OPENAI_API_KEY"):
            filtered_configs.append(config)
        elif config.grounding_strategy == GroundingStrategy.GRAPH_MATCH:
            # Graph matching doesn't need an API key
            filtered_configs.append(config)

    if not filtered_configs:
        print("No API keys found. Set ANTHROPIC_API_KEY and/or OPENAI_API_KEY.")
        print("Or run with --dry-run for grounding-only evaluation.")
        return

    suite.configs = filtered_configs
    print(f"Running {len(suite.configs)} configurations across {len(workflows)} workflows...")

    runner = SuiteRunner(suite, workflows)
    results = await runner.run()

    report = generate_report(results)
    print(report)

    # Save results
    output_dir = Path("./eval_results")
    output_dir.mkdir(exist_ok=True)
    report_path = output_dir / f"report_{suite.suite_id}.txt"
    report_path.write_text(report)
    print(f"\nReport saved to: {report_path}")


async def run_grounding_only_eval(args):
    """Eval grounding accuracy without executing actions.

    This is the fastest way to test whether the perception + grounding
    pipeline can reliably find elements. No browser control needed
    for pre-recorded screen states.
    """
    workflow = create_sample_search_workflow()

    config = EvalConfig(
        perception_mode=PerceptionMode.HYBRID,
        grounding_strategy=GroundingStrategy.GRAPH_MATCH,
    )

    harness = EvalHarness(config, dry_run=True)
    await harness.setup()

    try:
        result = await harness.run_workflow(workflow)
        report = generate_report([result])
        print(report)
    finally:
        await harness.teardown()


def main():
    parser = argparse.ArgumentParser(description="GPA Eval Harness")
    parser.add_argument("--suite", action="store_true", help="Run full comparison suite")
    parser.add_argument("--grounding-only", action="store_true",
                       help="Eval grounding only (no action execution)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Capture perception + ground but don't execute actions")
    parser.add_argument("--perception", default="hybrid",
                       choices=["screenshot", "accessibility_tree", "ui_graph", "hybrid"])
    parser.add_argument("--grounding", default="hybrid_graph_llm",
                       choices=["graph_match", "llm_vision", "llm_structured", "hybrid_graph_llm"])
    parser.add_argument("--model-provider", default="anthropic",
                       choices=["anthropic", "openai"])
    parser.add_argument("--model-name", default=None,
                       help="Model ID (uses provider default if omitted)")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.model_name is None:
        args.model_name = (
            "claude-sonnet-4-20250514" if args.model_provider == "anthropic"
            else "gpt-4o"
        )

    if args.suite:
        asyncio.run(run_comparison_suite(args))
    elif args.grounding_only:
        asyncio.run(run_grounding_only_eval(args))
    else:
        asyncio.run(run_quick_eval(args))


if __name__ == "__main__":
    main()
