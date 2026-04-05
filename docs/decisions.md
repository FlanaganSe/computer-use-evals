# Architecture Decision Records

### ADR-001: Accessibility-first execution over computer-use models
**Date:** 2026-04-04
**Status:** accepted
**Context:** The harness was originally built with OpenAI computer-use (screenshot → pixel coordinates) as a primary track. Research from late-2025 and 2026 (DMI, GPA, ShowUI-Aloha, "Are LLM Agents the New RPA?") consistently shows accessibility-backed structured state + regular LLMs achieves +67% success, -43.5% steps, and 78% cost reduction vs screenshot-based approaches. Computer-use models are expensive, slow, and less reliable than structured-state approaches for the vast majority of desktop tasks.
**Decision:** The primary adapter architecture will be structured state first (AX trees, UIA, DOM/ARIA) → regular LLM → semantic actions, with vision as a fallback only when structured state is unavailable. Computer-use adapters are retained as benchmarking tools, not the production path.
**Consequences:** The Codex adapter pattern (structured state → LLM → semantic actions) becomes the template. It needs to be generalized beyond browser-only. The openai_cu adapter remains for comparison but is not the direction of investment.

### ADR-002: Research-driven pivot before further implementation
**Date:** 2026-04-04
**Status:** superseded (research completed; informed the M1-M6 plan that was executed 2026-04-05)
**Context:** After the initial M1-M6 buildout, the user identified that the harness may be testing the wrong things relative to the product vision. The consolidated research strongly supports a different architecture than what was originally planned.
**Decision:** Pause implementation. Conduct deep research into current tools, frameworks, models, and best practices before building the next phase.
**Consequences:** Research completed (see `docs/research-consolidated.md`). Findings drove the M1-M6 plan that restructured the runtime, authoring, and capture pipelines.

### ADR-003: Structured-state desktop promoted as primary path
**Date:** 2026-04-05
**Status:** accepted
**Context:** After completing Milestones 1–4, the structured-state desktop adapter demonstrated viable AX-first execution with semantic targeting, milestone-aware verification, decision-evidence persistence, and cheap-first routing. The repo's defaults, docs, and configs still implied screenshot-first was the primary desktop direction.
**Decision:** Promote structured-state desktop as the documented primary desktop path. Reorder the adapter registry, update CLI help text, relabel report sections, and rewrite README. Legacy screenshot-first adapters (`openai_cu`, `openai_cu_hybrid`, `codex_subscription`) remain registered and functional as comparison lanes, not the direction of investment.
**Consequences:** New contributors can infer the correct primary path from `README.md`, CLI help, and the ADAPTERS dict comments. Legacy adapters are available for benchmarking but are clearly marked as comparison infrastructure.

### ADR-004: Milestone-aware verification as minimum eval trust
**Date:** 2026-04-05
**Status:** accepted
**Context:** Final-outcome-only grading (pass/fail on the end state) cannot diagnose where a multi-step task fails. Milestone 2 added optional milestone definitions to tasks, with per-milestone evaluation that identifies the first failure point.
**Decision:** Tasks should define milestones when possible. Milestone results are persisted in `trace.json`, surfaced in reports, and used to refine failure categorization. Milestone evaluation cannot corrupt the primary grade — it runs in a try/except wrapper.
**Consequences:** Failed runs are diagnosable from artifacts alone. The `MilestoneResult` list in traces supports automated failure-location analysis without replaying tasks.

### ADR-005: Cheap-first routing with explicit measurement
**Date:** 2026-04-05
**Status:** accepted
**Context:** The research showed 78% cost reduction is achievable through difficulty-based model routing. Milestone 4 added heuristic routing inside the structured-state adapter: cheap model for well-grounded AX decisions, strong model for sparse trees or post-failure retries.
**Decision:** Routing is internal to the adapter (invisible to the runner/protocol). Routing metadata (`cheap_steps`, `strong_steps`, `escalations`, `model_used`) is tracked per step and persisted in trace metadata and decision evidence. Cost estimation uses weighted blending based on actual step ratios.
**Consequences:** Routing experiments are separable from baseline comparisons. The `structured_state_desktop_routed` adapter variant exists alongside the non-routed baseline so routing effects can be measured independently.

### ADR-006: Screenshot fallback deferred — not justified by evidence
**Date:** 2026-04-05
**Status:** accepted
**Context:** The research noted macOS AX coverage is ~33% across all apps (Screen2AX). The plan included conditional screenshot fallback if M1-M3 evidence showed AX gaps on target apps.
**Decision:** No screenshot fallback was added. TextEdit and Calendar (the trusted desktop task apps) have sufficient AX coverage. The 33% figure is across all macOS apps, not the targeted ones. Adding vision fallback would introduce a second variable into the experiment without evidence-backed justification.
**Consequences:** The structured-state adapter is AX-only. If future tasks target AX-poor apps, screenshot fallback becomes justified and can be added as a separate measured experiment.

### ADR-007: Script-based verification for native app state
**Date:** 2026-04-05
**Status:** accepted
**Context:** Programmatic verification checks (`file_exists`, `file_contains`) work for filesystem outcomes but cannot verify native app state (calendar events, reminders, notes). `llm_judge` can evaluate traces but cannot check actual system state.
**Decision:** Add `script_check('path/to/verify.py')` as a programmatic check method. The script runs as a subprocess; exit 0 = pass, non-zero = fail, stdout = explanation. This enables AppleScript-based verification for any macOS app without adding app-specific grader functions.
**Consequences:** New desktop tasks can use custom verification scripts. The author pipeline's prompt is updated to include `script_check` so VLM-generated tasks can reference it.

### ADR-008: Typed runtime result contract with adapter feedback
**Date:** 2026-04-05
**Status:** accepted
**Context:** The runner recorded action outcomes as opaque strings, and the adapter never learned whether its previous actions succeeded. Every action history entry showed `"pending"`, causing the LLM to repeat failed actions without adjustment. AXPress errors were treated as definitive failures even when the UI state had changed.
**Decision:** Replace string results with a typed `RuntimeResult` model (`status`, `execution_method`, `state_changed`, `metadata`). Add `notify_result()` to the adapter protocol so the runner feeds actual outcomes back into the LLM's action history. Treat AXPress transport errors as provisional when post-action state evidence shows the UI changed. Add runner-owned stagnation detection using repeated action signatures plus unchanged-state evidence.
**Consequences:** The adapter prompt shows real outcomes (`ok`, `error`, `no_op`) instead of `"pending"`. The runner can terminate obvious no-progress loops. Action success is no longer determined solely by AXPress return codes. Reports expose structured execution data for research analysis.

### ADR-009: Draft-to-compiled task pipeline
**Date:** 2026-04-05
**Status:** accepted
**Context:** The `harness author` command directly produced the final runtime task YAML. There was no validation between VLM output and execution — invalid check expressions, unresolved variables, and missing script paths only failed at runtime. The same `goal.description` field served as human docs, agent instruction, and VLM prompt, conflating three audiences.
**Decision:** Split authoring from compilation. `harness author` produces a draft artifact with `compile_metadata` provenance. `harness compile` validates check expressions, variable references, and script paths, then emits a strict runtime task. Add `goal.agent_brief` to separate agent-facing instruction from human-facing description. Adapters prefer `agent_brief` when present.
**Consequences:** Invalid tasks fail at compile time, not at runtime. The author/compile seam enables prompt and compilation experiments without touching the runtime. Human-readable descriptions and agent instructions can evolve independently.

### ADR-010: Event-aligned capture as opt-in evidence mode
**Date:** 2026-04-05
**Status:** accepted
**Context:** Capture recorded screenshots on a fixed timer with input events on a separate monotonic timeline. Screenshots and events used different clocks and required manual correlation. The VLM received evenly-sampled screenshots plus a text timeline with no structural alignment between them, forcing it to infer what happened between frames.
**Decision:** Add an explicit `--aligned` capture mode that takes additional screenshots on high-signal events (clicks, app-focus changes) and persists an `aligned_timeline` in the manifest correlating events, screenshots, optional AX snapshots, and app context on a shared time base. Keep it opt-in — the default interval-only mode is preserved. Start with clicks and focus transitions only; do not default to per-keystroke capture.
**Consequences:** Aligned evidence lets the authoring pipeline correlate user actions directly with screenshots. The VLM receives numbered screenshots with aligned event descriptions. Artifact growth is bounded by debouncing and the sparse trigger set. The standard interval path remains available for lightweight capture.
