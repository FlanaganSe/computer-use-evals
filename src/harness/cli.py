"""CLI entry point for the eval harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from harness.capture import capture_session
from harness.compiler import CompileError, compile_draft_file
from harness.intent_extract import author_task, group_events, load_events
from harness.reporting import collect_runs, generate_comparison_report, generate_detailed_report
from harness.runner import ADAPTERS, run_task
from harness.types import GraderResult, RunConfig, Trace


def _run_config(config_path: str, runs_dir: str) -> None:
    """Execute all tasks defined in a run config YAML file."""
    path = Path(config_path)
    if not path.exists():
        print(f"Error: config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    from pydantic import ValidationError

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        print(f"Error: config file is empty or not valid YAML: {config_path}", file=sys.stderr)
        sys.exit(1)
    try:
        config = RunConfig.model_validate(raw)
    except ValidationError as exc:
        print(f"Error: invalid config: {exc}", file=sys.stderr)
        sys.exit(1)

    if config.adapter not in ADAPTERS:
        print(
            f"Error: unknown adapter '{config.adapter}'. Available: {', '.join(ADAPTERS.keys())}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not config.tasks:
        print("Error: config defines no tasks", file=sys.stderr)
        sys.exit(1)

    if config.trial_count < 1:
        print("Error: trial_count must be at least 1", file=sys.stderr)
        sys.exit(1)

    total_runs = len(config.tasks) * config.trial_count
    print(f"Config: {config_path}")
    print(f"Adapter: {config.adapter}")
    print(f"Tasks: {len(config.tasks)} | Trials: {config.trial_count} | Total runs: {total_runs}")
    print()

    run_dirs: list[Path] = []
    run_num = 0
    for task_path in config.tasks:
        for trial in range(config.trial_count):
            run_num += 1
            trial_label = (
                f" (trial {trial + 1}/{config.trial_count})" if config.trial_count > 1 else ""
            )
            print(
                f"[{run_num}/{total_runs}] {task_path}{trial_label} ... ",
                end="",
                flush=True,
            )
            try:
                run_dir = run_task(
                    task_path=task_path,
                    adapter_name=config.adapter,
                    max_steps=config.max_steps,
                    runs_dir=runs_dir,
                )
                run_dirs.append(run_dir)
                trace_path = run_dir / "trace.json"
                if trace_path.exists():
                    trace = Trace.model_validate_json(trace_path.read_text())
                    print(trace.outcome)
                else:
                    print("done")
            except Exception as exc:
                print(f"error: {exc}")

    # Print summary from completed runs
    if run_dirs:
        results: list[tuple[Trace, GraderResult]] = []
        for rd in run_dirs:
            trace_path = rd / "trace.json"
            grade_path = rd / "grade.json"
            if trace_path.exists() and grade_path.exists():
                t = Trace.model_validate_json(trace_path.read_text())
                g = GraderResult.model_validate_json(grade_path.read_text())
                results.append((t, g))
        if results:
            print()
            print(generate_comparison_report(results))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="harness", description="Desktop Agent Eval Harness")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a task or execute a run config")
    run_parser.add_argument("task", nargs="?", default=None, help="Path to task YAML file")
    run_parser.add_argument(
        "--config",
        default=None,
        help="Path to run config YAML file (alternative to positional task)",
    )
    run_parser.add_argument(
        "--adapter",
        default=None,
        help="Adapter name (required with positional task; "
        "primary: structured_state_desktop, "
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
        if args.config and args.task:
            print("Error: cannot specify both task and --config", file=sys.stderr)
            sys.exit(1)
        if args.config:
            if args.adapter:
                print(
                    "Error: --adapter is set by the config file; do not combine with --config",
                    file=sys.stderr,
                )
                sys.exit(1)
            _run_config(args.config, args.runs_dir)
        elif args.task:
            if not args.adapter:
                print(
                    "Error: --adapter is required when running a single task",
                    file=sys.stderr,
                )
                sys.exit(1)
            run_dir = run_task(
                task_path=args.task,
                adapter_name=args.adapter,
                max_steps=args.max_steps,
                runs_dir=args.runs_dir,
            )
            print(f"Run complete: {run_dir}")
        else:
            print("Error: provide a task path or --config", file=sys.stderr)
            sys.exit(1)

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
