"""CLI entry point for the eval harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.reporting import collect_runs, generate_comparison_report, generate_detailed_report
from harness.runner import run_task


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="harness", description="Desktop Agent Eval Harness")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a task")
    run_parser.add_argument("task", help="Path to task YAML file")
    run_parser.add_argument("--adapter", required=True, help="Adapter name")
    run_parser.add_argument("--max-steps", type=int, default=30, help="Maximum steps")
    run_parser.add_argument("--runs-dir", default="runs", help="Output directory for runs")

    compare_parser = subparsers.add_parser("compare", help="Compare runs across adapters")
    compare_parser.add_argument(
        "--runs-dir", default="runs", help="Directory containing run outputs"
    )
    compare_parser.add_argument("--task", default=None, help="Filter by task ID")
    compare_parser.add_argument("--output", default=None, help="Write report to file")
    compare_parser.add_argument(
        "--detailed", action="store_true", help="Generate detailed report with extended metrics"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        run_dir = run_task(
            task_path=args.task,
            adapter_name=args.adapter,
            max_steps=args.max_steps,
            runs_dir=args.runs_dir,
        )
        print(f"Run complete: {run_dir}")

    elif args.command == "compare":
        runs = collect_runs(Path(args.runs_dir), task_filter=args.task)
        report = (
            generate_detailed_report(runs) if args.detailed else generate_comparison_report(runs)
        )
        print(report)
        if args.output:
            Path(args.output).write_text(report)
            print(f"Report written to {args.output}")
