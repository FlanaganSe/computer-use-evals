"""CLI entry point for the eval harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.capture import capture_session
from harness.intent_extract import author_task
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

    capture_parser = subparsers.add_parser("capture", help="Capture screen evidence")
    capture_parser.add_argument("--output", required=True, help="Output evidence directory")
    capture_parser.add_argument(
        "--interval", type=float, default=2.0, help="Capture interval in seconds"
    )
    capture_parser.add_argument("--aria", action="store_true", help="Capture ARIA/AX state")
    capture_parser.add_argument(
        "--no-events",
        action="store_true",
        default=False,
        help="Disable keyboard/mouse event recording",
    )
    capture_parser.add_argument("--name", default="untitled", help="Task name for manifest")

    author_parser = subparsers.add_parser("author", help="Generate draft task from evidence")
    author_parser.add_argument("evidence_dir", help="Path to evidence directory")
    author_parser.add_argument("--output", required=True, help="Output task YAML path")
    author_parser.add_argument("--model", default="gpt-4o", help="VLM model to use")
    author_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")

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

    elif args.command == "capture":
        capture_events = not args.no_events
        if capture_events:
            print("Recording keyboard and mouse input. Evidence may contain passwords.")
        print(f"Capturing to {args.output} every {args.interval}s (Ctrl+C to stop)...")
        evidence_dir = capture_session(
            output_dir=Path(args.output),
            interval_seconds=args.interval,
            capture_aria=args.aria,
            capture_events=capture_events,
            task_name=args.name,
        )
        print(f"Evidence captured: {evidence_dir}")

    elif args.command == "author":
        yaml_text = author_task(
            evidence_dir=Path(args.evidence_dir),
            output_path=Path(args.output),
            model=args.model,
            dry_run=args.dry_run,
        )
        print(yaml_text)
        if not args.dry_run:
            print(f"\nDraft task written to {args.output}")
            print("Review and edit before running through the harness.")
