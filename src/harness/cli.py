"""CLI entry point for the eval harness."""

from __future__ import annotations

import argparse
import sys

from harness.runner import run_task


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="harness", description="Desktop Agent Eval Harness")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a task")
    run_parser.add_argument("task", help="Path to task YAML file")
    run_parser.add_argument("--adapter", required=True, help="Adapter name")
    run_parser.add_argument("--max-steps", type=int, default=30, help="Maximum steps")
    run_parser.add_argument("--runs-dir", default="runs", help="Output directory for runs")

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
