"""CLI entry point for the eval harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.capture import capture_session
from harness.compiler import CompileError, compile_draft_file
from harness.intent_extract import author_task, group_events, load_events
from harness.reporting import collect_runs, generate_comparison_report, generate_detailed_report
from harness.runner import run_task


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="harness", description="Desktop Agent Eval Harness")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a task")
    run_parser.add_argument("task", help="Path to task YAML file")
    run_parser.add_argument(
        "--adapter",
        required=True,
        help="Adapter name (primary: structured_state_desktop, "
        "structured_state_desktop_routed; baselines: deterministic; "
        "legacy comparison: openai_cu, openai_cu_hybrid, codex_subscription)",
    )
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
    author_parser.add_argument("--output", required=True, help="Output draft YAML path")
    author_parser.add_argument("--model", default="gpt-5.4", help="VLM model to use")
    author_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")

    compile_parser = subparsers.add_parser(
        "compile", help="Compile a draft task into a validated runtime task"
    )
    compile_parser.add_argument("draft", help="Path to draft YAML file")
    compile_parser.add_argument("--output", default=None, help="Output compiled task YAML path")
    compile_parser.add_argument(
        "--task-dir",
        default=None,
        help="Project root for resolving script paths (default: current directory)",
    )
    compile_parser.add_argument(
        "--no-validate-scripts",
        action="store_true",
        help="Skip checking that referenced scripts exist on disk",
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
        print(f"\nEvidence captured: {evidence_dir}")
        events = load_events(evidence_dir)
        if events:
            grouped = group_events(events)
            if grouped:
                print("\n--- Recorded actions ---")
                for line in grouped:
                    print(f"  {line}")
                print("---")

    elif args.command == "author":
        evidence_dir_path = Path(args.evidence_dir)
        events = load_events(evidence_dir_path)
        if events:
            grouped = group_events(events)
            if grouped:
                print("--- Recorded actions (ground truth) ---")
                for line in grouped:
                    print(f"  {line}")
                print("---\n")
        yaml_text = author_task(
            evidence_dir=Path(args.evidence_dir),
            output_path=Path(args.output),
            model=args.model,
            dry_run=args.dry_run,
        )
        print(yaml_text)
        if not args.dry_run:
            print(f"\nDraft written to {args.output}")
            print("Review and edit, then run: harness compile " + args.output)

    elif args.command == "compile":
        draft_path = Path(args.draft)
        output_path = Path(args.output) if args.output else None
        task_dir = Path(args.task_dir) if args.task_dir else Path.cwd()
        validate_scripts = not args.no_validate_scripts
        try:
            task = compile_draft_file(
                draft_path,
                output_path=output_path,
                task_dir=task_dir,
                validate_scripts=validate_scripts,
            )
            final_path = output_path or (draft_path.parent / "task.yaml")
            print(f"Compiled task '{task.task_id}' written to {final_path}")
        except CompileError as exc:
            print(f"Compile failed with {len(exc.errors)} error(s):", file=sys.stderr)
            for err in exc.errors:
                print(f"  - {err}", file=sys.stderr)
            sys.exit(1)
