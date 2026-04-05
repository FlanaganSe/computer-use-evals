# Architecture Decision Records

### ADR-001: Accessibility-first execution over computer-use models
**Date:** 2026-04-04
**Status:** accepted
**Context:** The harness was originally built with OpenAI computer-use (screenshot → pixel coordinates) as a primary track. Research from late-2025 and 2026 (DMI, GPA, ShowUI-Aloha, "Are LLM Agents the New RPA?") consistently shows accessibility-backed structured state + regular LLMs achieves +67% success, -43.5% steps, and 78% cost reduction vs screenshot-based approaches. Computer-use models are expensive, slow, and less reliable than structured-state approaches for the vast majority of desktop tasks.
**Decision:** The primary adapter architecture will be structured state first (AX trees, UIA, DOM/ARIA) → regular LLM → semantic actions, with vision as a fallback only when structured state is unavailable. Computer-use adapters are retained as benchmarking tools, not the production path.
**Consequences:** The Codex adapter pattern (structured state → LLM → semantic actions) becomes the template. It needs to be generalized beyond browser-only. The openai_cu adapter remains for comparison but is not the direction of investment.

### ADR-002: Research-driven pivot before further implementation
**Date:** 2026-04-04
**Status:** accepted
**Context:** After M1-M6 completion, the user identified that the harness may be testing the wrong things relative to the product vision. The consolidated research strongly supports a different architecture than what was originally planned. April 2026 tools and models may have shifted the landscape further.
**Decision:** Pause implementation. Conduct deep research (see `.claude/plans/research-handoff.md`) into current tools, frameworks, models, and best practices before building the next phase. The research must be grounded in what's available and working in April 2026, not just what papers proposed.
**Consequences:** M7 (CGEventTap input events) is deferred. The next deliverable is a research findings document, not code. Future milestones will be informed by that research rather than the original M1-M6 plan.

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
