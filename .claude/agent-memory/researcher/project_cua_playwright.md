---
name: Cua macOS VM sandboxing and Playwright browser automation — key facts
description: What Cua/Lume provides for macOS VM sandboxing on Apple Silicon, its full API surface, MCP support, stability status, and Playwright's accessibility tree + screenshot capabilities, as of April 2026
type: project
---

Cua (trycua/cua, YC) provides macOS VMs on Apple Silicon via Lume (Swift, Apple Virtualization.framework). Near-native performance (~90% native speed).

Host requirements: Apple Silicon, macOS 15+, 16GB RAM recommended, 30GB free disk (VM image).

Lume daemon runs REST API on port 7777. Python SDK: `pip install cua-agent[all]` (Python 3.12/3.13).

Full API surface confirmed:
- VM lifecycle: ephemeral mode (auto-reset on context exit); REST endpoints for create/run/delete
- Screenshot: `await sb.screenshot()` / `screenshot()` MCP tool
- Mouse: left/right/double click, move, drag — all with (x, y) coordinates
- Keyboard: `type_text`, `press_key`, `hotkey`
- Accessibility tree: `get_accessibility_tree()` — works since v0.1.26 (Oct 2025), was broken before
- Clipboard, filesystem check, shell command execution

MCP server launched January 20, 2026. HTTP and MCP interfaces run simultaneously. MCP server received bug fixes as recently as Jan 29, 2026 — pin version for PoC.

AX tree inside VM still subject to 33% macOS full-coverage gap (Screen2AX). Cua does not synthesize or enhance the tree.

Apple Containerization framework (announced WWDC) will require Cua to evolve Lume layer. Expected in macOS Tahoe 26. Python SDK interface (`Sandbox.ephemeral`) should remain stable; Lume internals will change.

Stability: screenshot/click/type is stable. MCP server is new (3 months). AX tree was broken until Oct 2025. Overall: medium stability, suitable for Milestone 5+ VM isolation, not day-1 dependency.

Playwright MCP (microsoft/playwright-mcp): defaults to accessibility tree mode (ARIA snapshots), not pixels. `locator.ariaSnapshot()` returns YAML of roles/names/attributes. Two modes: snapshot (default, AX tree) and vision (screenshot). @playwright/cli (early 2026) is 4x more token-efficient (~27K vs 114K tokens/task).

Only macOS VM sandboxing options: Cua/Lume (open source, documented) and host macOS (no isolation). All other sandbox platforms (E2B, Modal, Daytona, etc.) are Linux-only or lack documented macOS VM implementation.

**Why:** Milestone 5 decision gate in .plans/plan.md — "if host execution noise is blocking, add VM-backed environment adapter."
**How to apply:** Default to host macOS. Add Cua at Milestone 5 only if contamination is the binding failure mode. When added, pin cua-computer version. Playwright is the Phase 1 browser executor, natively providing both screenshot and AX-tree observation modes for the baseline comparison.
