#!/usr/bin/env python3
"""AX coverage probe: dump and analyze accessibility trees for anchor apps.

Run this before broadening scope to verify that target apps have usable
AX coverage. This is the first sub-step of Milestone 1 — ~30 minutes
of work that can save days if AX trees are too sparse.

Usage:
    python scripts/ax_coverage_probe.py [--app TextEdit] [--pid 1234]

If no --app or --pid is given, probes the frontmost application.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

# Add src to path for imports
sys.path.insert(0, "src")

from harness.ax_state import (
    build_ax_tree,
    coverage_stats,
    format_for_prompt,
    prune_interactive,
)
from harness.environments.macos import _get_ax_tree, _serialize_ax_element


def _get_pid_for_app(app_name: str) -> int | None:
    """Get PID for a running app by name."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", app_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip().split("\n")[0])
    except Exception:
        pass
    return None


def _get_frontmost_pid() -> tuple[str, int] | None:
    """Get the frontmost app name and PID."""
    try:
        import Quartz

        workspace = Quartz.NSWorkspace.sharedWorkspace()
        front_app = workspace.frontmostApplication()
        return (front_app.localizedName(), front_app.processIdentifier())
    except Exception:
        return None


def probe(pid: int, app_name: str) -> dict:
    """Probe AX coverage for a given app PID."""
    from ApplicationServices import AXUIElementCreateApplication

    print(f"\n{'=' * 60}")
    print(f"AX Coverage Probe: {app_name} (PID {pid})")
    print(f"{'=' * 60}")

    # 1. Get human-readable tree
    text_tree = _get_ax_tree(pid)
    if text_tree is None:
        print("\n  FAIL: Could not capture AX tree. Check permissions.")
        return {"app": app_name, "pid": pid, "status": "no_tree"}

    print(f"\n--- Human-readable AX tree (first 2000 chars) ---")
    print(text_tree[:2000])
    if len(text_tree) > 2000:
        print(f"... ({len(text_tree)} total chars)")

    # 2. Build structured tree
    app_ref = AXUIElementCreateApplication(pid)
    start = time.monotonic()
    structured = build_ax_tree(app_ref)
    elapsed_ms = (time.monotonic() - start) * 1000

    if structured is None:
        print("\n  FAIL: Could not build structured AX tree.")
        return {"app": app_name, "pid": pid, "status": "no_structured_tree"}

    print(f"\n--- Structured tree built in {elapsed_ms:.0f}ms ---")

    # 3. Coverage stats
    stats = coverage_stats(structured)
    print(f"\n--- Coverage Statistics ---")
    print(f"  Total nodes:         {stats['total_nodes']}")
    print(f"  Interactive nodes:   {stats['interactive_nodes']}")
    print(f"  Nodes with bounds:   {stats['nodes_with_bounds']}")
    print(f"  Capture latency:     {elapsed_ms:.0f}ms")

    print(f"\n--- Role distribution ---")
    for role, count in sorted(stats["roles"].items(), key=lambda x: -x[1]):
        marker = (
            " ← interactive"
            if role
            in {
                "AXButton",
                "AXTextField",
                "AXTextArea",
                "AXCheckBox",
                "AXRadioButton",
                "AXPopUpButton",
                "AXMenuItem",
                "AXLink",
                "AXSlider",
                "AXIncrementor",
                "AXComboBox",
            }
            else ""
        )
        print(f"    {role:30s} {count:4d}{marker}")

    # 4. Pruned interactive elements
    interactive = prune_interactive(structured)
    print(f"\n--- Pruned interactive elements ({len(interactive)}) ---")
    prompt_text = format_for_prompt(interactive)
    print(prompt_text)

    # 5. Viability assessment
    viable = stats["interactive_nodes"] >= 5
    print(f"\n--- Viability ---")
    if viable:
        print(f"  PASS: {stats['interactive_nodes']} interactive elements (≥5 required)")
    else:
        print(f"  WARN: Only {stats['interactive_nodes']} interactive elements (<5)")
        print("  Consider: vision fallback may be needed for this app")

    return {
        "app": app_name,
        "pid": pid,
        "status": "ok",
        "stats": stats,
        "interactive_count": len(interactive),
        "capture_latency_ms": elapsed_ms,
        "viable": viable,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="AX coverage probe for desktop apps")
    parser.add_argument("--app", help="App name to probe (e.g., TextEdit)")
    parser.add_argument("--pid", type=int, help="PID to probe directly")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    args = parser.parse_args()

    if args.pid:
        result = probe(args.pid, args.app or f"PID-{args.pid}")
    elif args.app:
        pid = _get_pid_for_app(args.app)
        if pid is None:
            print(f"Could not find running app: {args.app}")
            print(f"Try: open -a '{args.app}' first")
            sys.exit(1)
        result = probe(pid, args.app)
    else:
        info = _get_frontmost_pid()
        if info is None:
            print("Could not determine frontmost app. Use --app or --pid.")
            sys.exit(1)
        app_name, pid = info
        result = probe(pid, app_name)

    if args.json:
        print(f"\n--- JSON Summary ---")
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
