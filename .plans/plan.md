# Implementation Plan: Structured-State-First Harness Rewrite

## Contract

### 1. Problem

The harness spine is mostly correct, but it is currently evaluating the wrong execution architecture for the product direction this repo is meant to prove.

Today:

- `src/harness/runner.py` already provides a clean setup -> observe -> decide -> execute -> grade loop.
- `src/harness/types.py` already separates Adapter and Environment protocols cleanly.
- `src/harness/environments/macos.py` already captures macOS accessibility state, but no adapter uses it to plan actions.
- `src/harness/graders.py` only implements a small programmatic checker set, while `llm_judge` is declared in the schema and unimplemented.
- `tasks/my-test-task/task.yaml` already expresses unsupported grader logic, so current eval trust is partly broken.
- `src/harness/reporting.py` and `trace.json` preserve actions/results, but not the decision-useful structured evidence needed for debugging the new path.

The rewrite needs to realign the harness around structured accessibility state -> regular LLM -> semantic action -> environment resolution, while keeping the harness lean and useful as a proof system rather than rebuilding a product stack inside it.

### 2. Requirements

- Preserve the existing harness spine unless a concrete code seam blocks the new direction.
- Stay macOS-first for the rewrite.
- Make the first implementation additive where possible.
- Build the smallest first slice that can validate the structured-state hypothesis on a real desktop task.
- Repair evaluator trust before relying on new experiment results.
- Add milestone-based verification as the minimum task-format upgrade.
- Persist evidence in a form that helps explain failures without replaying runs interactively.
- Keep the experiment matrix controlled so only one major variable changes at a time: state shape, prompt shape, model/routing, or fallback mode.
- Keep `deterministic` and `openai_cu` available long enough to compare against the new path.
- Separate “replace now” from “deprecate later”.
- Avoid speculative platform work: no full product abstractions, no broad cross-platform layer, no learned router, no major recorder rebuild in the first phase.

### 3. Acceptance Criteria

- A new macOS structured-state desktop adapter exists and runs through the existing runner without protocol breakage.
- The new path can target desktop elements semantically using stable AX references with coordinate fallback.
- At least one trusted desktop task uses milestones and yields decision-useful evidence artifacts.
- Unsupported grader/task expressions fail fast instead of silently producing misleading eval results.
- `llm_judge` works as a small, explicit verification path for tasks that cannot be checked programmatically.
- Reporting and trace artifacts make milestone progress and new failure modes inspectable.
- Legacy baselines still run during the comparison phase.
- There is a clear gate for when to keep, demote, or later remove screenshot-first desktop paths.

### 4. Non-goals

- Building a full desktop agent product architecture inside the harness.
- Building a skill compiler, branch synthesis engine, generalized workflow DSL, or replay compiler.
- Cross-platform accessibility abstractions in this rewrite.
- Learned model routing or classifier-based escalation in the first implementation.
- A full continuous-video recorder overhaul before the AX-first execution path is validated.
- Removing legacy adapters before the new path has comparison evidence behind it.

### 5. Constraints

- Primary planning source is `.plans/research-findings.md`; secondary sources are `.plans/research.md` and `.claude/plans/research-findings.md`.
- `docs/architecture.md` and `.agents/rules/*` are referenced by repo guidance but do not exist locally; the actual source and tests therefore carry more weight than missing docs.
- Existing repo state is already dirty; this plan assumes additive changes and careful migration rather than cleanup-by-reversion.
- The current task schema and grader implementation are partially out of sync; the plan must treat evaluator trust as a first-order issue.
- The rewrite should minimize complexity and keep milestones independently shippable and testable.

## Implementation Plan

### 1. Summary

Recommended first implementation milestone:

- Build a single trustworthy AX-first desktop slice: structured AX snapshot with stable node refs, a new semantic desktop adapter, minimal macOS target resolution, decision-evidence persistence, and enough verifier/task fixes to trust the result.

Why this first:

- It is the smallest rewrite that changes what the harness can prove.
- It tests the core product-direction hypothesis directly on macOS.
- It avoids premature work on routing, fallback vision, and recorder overhaul.
- It fixes the minimum trust gaps that would otherwise contaminate comparison data.

Preserve these architectural decisions:

- Keep the Adapter and Environment protocols in `src/harness/types.py`.
- Keep the runner loop shape in `src/harness/runner.py`.
- Keep `src/harness/adapters/deterministic.py` as the permanent sanity baseline.
- Keep `src/harness/adapters/openai_cu.py` as a comparison lane until the new path is validated.
- Keep the existing human-readable AX text serializer for logs; add a parallel machine-readable state representation instead of replacing it immediately.

### 2. Current State

What can stay as-is:

- `src/harness/types.py` Adapter and Environment protocols.
- `src/harness/runner.py` overall orchestration model.
- `src/harness/environments/browser.py` and browser tasks as existing non-desktop baselines.
- `src/harness/adapters/deterministic.py`.
- The failure taxonomy enum in `src/harness/failures.py`.
- Most comparison-report scaffolding in `src/harness/reporting.py`.
- Variable substitution mechanics in `src/harness/task_loader.py`, with targeted extension rather than rewrite.

What should be modified in place:

- `src/harness/types.py` to add optional milestone and evidence-oriented models.
- `src/harness/task_loader.py` to validate new task fields and reject unsupported grader/check forms.
- `src/harness/runner.py` to record decision evidence, evaluate milestones, and classify more failures when supported by evidence.
- `src/harness/graders.py` to implement `llm_judge` and a small milestone-aware verification surface.
- `src/harness/reporting.py` to show milestone/evidence outcomes and treat AX-targeted actions as semantic actions.
- `src/harness/environments/macos.py` to expose structured AX state and resolve semantic targets.
- `src/harness/capture.py` and `src/harness/intent_extract.py` to add AX snapshots before any broader recorder redesign.
- `tasks/desktop_textedit_save/task.yaml` and `tasks/my-test-task/task.yaml` so the task suite is trustworthy enough for comparison.

What should be added in parallel first:

- A new desktop structured-state adapter rather than repurposing `codex_subscription` immediately.
- A parallel AX state module or serializer for stable IDs, pruning, and machine-readable output.
- A small milestone verifier module or helper layer, if keeping this logic out of `graders.py` keeps complexity down.
- New tests dedicated to the new path rather than rewriting legacy adapter tests up front.

What should be demoted or removed later:

- The screenshot-first desktop path should be demoted after comparison evidence exists, not before.
- Browser-specific semantics inside `codex_subscription` should only be generalized after the desktop path proves out.
- Any legacy report sections that assume `openai_cu` is a primary path should be reworded only after the new path becomes the default comparison target.

### 3. Files to Change

- `src/harness/types.py`
  - Add optional `Milestone`, `MilestoneResult`, and step-evidence fields with backward compatibility.
  - Extend verification typing only as far as needed for milestone checks.
- `src/harness/task_loader.py`
  - Support substitution/validation for optional milestone structures.
  - Add a lint/validation layer for unsupported check expressions.
- `src/harness/runner.py`
  - Persist decision-point evidence.
  - Add milestone evaluation hooks.
  - Assign more failure categories where evidence supports them.
  - Register the new adapter without disrupting existing registries.
- `src/harness/graders.py`
  - Implement `llm_judge`.
  - Centralize supported check parsing so invalid task DSL fails fast.
  - Add minimal milestone helpers.
- `src/harness/reporting.py`
  - Surface milestone outcomes, evidence locations, and semantic-target usage.
  - Preserve current comparison views while adding the new signals.
- `src/harness/environments/macos.py`
  - Add structured AX export with stable references and bounds.
  - Resolve semantic targets to executable desktop actions.
  - Keep coordinate execution as fallback.
- `src/harness/capture.py`
  - Persist AX snapshots alongside screenshots.
- `src/harness/intent_extract.py`
  - Consume more than first/last AX context once AX snapshots exist.
- `src/harness/adapters/openai_cu.py`
  - Only small cleanup later if needed for legacy labeling or comparison metadata.
- `tasks/desktop_textedit_save/task.yaml`
  - Upgrade to optional milestones without breaking deterministic compatibility.
- `tasks/my-test-task/task.yaml`
  - Convert to supported verification or mark it non-comparable until it is valid.
- `tests/test_task_loader.py`
  - Add milestone/task validation coverage.
- `tests/test_graders.py`
  - Add `llm_judge` and unsupported-check validation coverage.
- `tests/test_macos_env.py`
  - Add structured AX export and semantic target resolution tests.
- `tests/test_capture.py`
  - Cover AX snapshot persistence.
- `tests/test_intent_extract.py`
  - Cover multi-snapshot AX prompt construction.
- `tests/test_detailed_report.py`
  - Cover milestone/evidence reporting additions.

### 4. Files to Create

- `src/harness/adapters/structured_state_desktop.py`
  - New primary experiment adapter for AX-first desktop planning.
- `src/harness/ax_state.py`
  - Shared AX node model, pruning, stable ID generation, and serializer helpers.
- `src/harness/milestones.py`
  - Optional helper layer for milestone evaluation and result shaping, if this keeps `runner.py` and `graders.py` cleaner.
- `tests/test_ax_state.py`
  - Stable ID, pruning, and serialization tests.
- `tests/test_structured_state_desktop.py`
  - Prompt construction, semantic action parsing, and routing/fallback tests.
- `tests/test_milestones.py`
  - Runner- and verifier-facing milestone behavior tests.

If milestone logic stays small enough, `src/harness/milestones.py` and `tests/test_milestones.py` can collapse into `graders.py` and `tests/test_graders.py` to avoid needless file count growth.

### 5. Milestone Outline

#### ~~Milestone 1: Trustworthy AX Desktop Slice~~ ✓ Complete

- [x] Step 1 — AX state module: stable IDs, pruning, machine-readable serialization (`src/harness/ax_state.py` + `tests/test_ax_state.py`) → verify: `python -m pytest tests/test_ax_state.py -v`
- [x] Step 2 — Structured-state desktop adapter + semantic target resolution in macOS env (`src/harness/adapters/structured_state_desktop.py`, extend `environments/macos.py`) → verify: `python -m pytest tests/test_structured_state_desktop.py tests/test_macos_env.py -v`
- [x] Step 3 — Decision-evidence persistence in runner + llm_judge + task/check validation (`runner.py`, `graders.py`, `task_loader.py`, `types.py`) → verify: `python -m pytest tests/test_graders.py tests/test_task_loader.py -v`
- [x] Step 4 — Anchor task upgrade + my-test-task exclusion + reporting evidence section → verify: `python -m pytest tests/ -v --ignore=tests/test_deterministic_smoke.py --ignore=tests/test_openai_adapter.py -k "not test_passes_with_matching_fields and not test_fails_with_wrong_name"`
- [x] Step 5 — AX coverage probe script + bounded prompt/state-shaping pass (manual, documented) → verify: manual run of probe script
Commit: "implement structured-state desktop milestone foundation"

Objective:

- Validate the structured-state desktop hypothesis with the smallest additive slice that still yields trustworthy evidence.

Scope:

- **First sub-step (gates the rest):** Run an AX coverage probe on anchor apps (TextEdit, Finder, Chrome) — dump the tree, count interactive elements, check whether task-critical controls are present. If the anchor desktop app lacks usable AX coverage, stop and reassess before building the adapter. This is ~30 minutes of work that can save days.
- Add a machine-readable AX state path with stable node references, bounds, and interactive-element pruning. Pruning rules and their iteration are first-class concerns here — which roles to include, how many elements to cap at, and how to prioritize near-focus elements will likely dominate adapter performance more than model choice. Start with the rules from `.plans/research-findings.md` §3.1 and expect to iterate during prompt tuning.
- Add `StructuredStateDesktopAdapter` using the existing Adapter protocol.
- Extend `MacOSDesktopEnvironment` to resolve semantic target IDs to coordinates and execute with coordinate fallback.
- Persist decision-point evidence per step:
  - focused app/window
  - pruned AX state or reference
  - chosen action
  - execution result
- Implement minimal `llm_judge`.
- Add task/check validation so unsupported expressions fail fast.
- Upgrade `desktop_textedit_save` to optional milestones while preserving final verification.
- Either repair `tasks/my-test-task/task.yaml` into supported semantics or explicitly remove it from trusted comparison runs until repaired.
- Run one bounded state-shaping experiment on the anchor desktop task with a single model:
  - compare 2-3 pruning/prompt variants max
  - do not add routing yet
  - do not add a second provider yet
  - do not add vision fallback yet
  This keeps the first learning loop focused on whether context shape, not model fanout, is driving desktop performance.

Why this is first:

- It directly answers whether AX-first desktop planning works on macOS.
- It preserves the rest of the harness spine.
- It prevents obviously invalid tasks/verifiers from poisoning the first comparison signal.

Dependencies:

- None beyond current repo state.

Exit gate:

- The new adapter can complete or credibly attempt `desktop_textedit_save` through the existing runner.
- Stable AX node references can be used to select a target and resolve fallback coordinates.
- A failed run is explainable from persisted evidence without replaying the task manually.
- Unsupported check expressions now fail at load/validation time instead of producing misleading results.
- `llm_judge` has unit coverage and one harness-path integration test.
- One prompt/state baseline is chosen and frozen for subsequent comparisons, so later experiments do not conflate architecture changes with prompt churn.

Acceptance experiments:

- AX coverage probe results (from first sub-step) confirm anchor apps are viable.
- Prompt/state-shaping probe results identify one compact prompt/pruning format that is good enough to carry forward.
- Accept Milestone 1 if the new adapter can complete or credibly attempt the anchor desktop task and the evidence artifacts are diagnostic enough to explain what happened.

#### ~~Milestone 2: Milestone-Aware Verification and Reporting~~ ✓ Complete

- [x] Step 1 — Add `evaluate_milestones()` to `graders.py` reusing existing `_eval_check()` for programmatic checks → verify: `python -m pytest tests/test_graders.py -v`
- [x] Step 2 — Add `milestone_results` to `Trace`, integrate evaluation into runner, persist in trace.json, refine failure categorization → verify: `python -m pytest tests/test_graders.py tests/test_task_loader.py -v`
- [x] Step 3 — Update single-run and comparison reports to show milestone pass/fail and failure location → verify: `python -m pytest tests/test_detailed_report.py tests/test_comparison_report.py -v`
- [x] Step 4 — Add tests covering milestone evaluation, report rendering, trace serialization, and backward compatibility → verify: `python -m pytest tests/ -v --ignore=tests/test_deterministic_smoke.py --ignore=tests/test_openai_adapter.py`
Commit: “implement milestone-aware verification and reporting”

Objective:

- Make the harness outputs more trustworthy and more diagnostic without widening the execution architecture again.

Scope:

- Add optional milestone schema to `Task`.
- Evaluate milestone status during runs using a minimal checker surface:
  - `programmatic`
  - small AX-state predicate support where needed
  - `llm_judge` only where programmatic checks are impractical
- Record milestone results in trace/report artifacts.
- Improve failure assignment only where milestone/evidence data makes the category defensible.
- Update reporting to show where the run broke, not just whether it passed.

Dependencies:

- Milestone 1 decision evidence and task-validation foundation.

Exit gate:

- At least one task can show milestone progress in artifacts and reports.
- Milestone data identifies failure location better than final-only grading on at least one failed run.
- Runner changes remain backward compatible with v1 tasks.

Acceptance experiments:

- Re-run the upgraded desktop task and compare “final-outcome-only” diagnosis versus milestone-aware diagnosis.
- Accept if milestone data reveals at least one actionable failure explanation that the current trace/report would not.

#### ~~Milestone 3: Capture and Authoring Alignment~~ ✓ Complete

- [x] Step 1 — Add `load_sampled_aria()` and `_truncate_aria()` to `intent_extract.py`; update `build_prompt()` to accept `aria_samples` list; update `extract_intent()` to load sampled AX snapshots instead of only first/last → verify: `python -m pytest tests/test_intent_extract.py -v`
- [x] Step 2 — Add tests: sampled AX prompt construction, bounded truncation, backward compatibility when AX disabled, integration with `author_task()` → verify: `python -m pytest tests/test_intent_extract.py tests/test_capture.py tests/test_events.py -v`
Commit: "align capture and authoring with sampled ax context"

Objective:

- Improve authoring inputs with AX snapshots before any recorder overhaul.

Scope:

- Save AX snapshots during capture sessions.
- Update `intent_extract.py` to include sampled AX context from the capture rather than only first/last state.
- Keep screenshot sampling and event grouping; add AX rather than replacing those inputs.
- Do not move to video-first capture yet.

Dependencies:

- Structured AX export from Milestone 1.

Exit gate:

- Capture output includes AX state artifacts consistently when enabled.
- Intent extraction prompts include sampled AX context in a bounded, testable way.
- Existing capture and authoring workflows still work when AX capture is disabled.

Acceptance experiments:

- Compare task drafting quality on a small set of recordings with and without AX context.
- Accept if AX context yields noticeably better task descriptions or variable extraction on at least two recordings.

#### Milestone 4: Comparison Runs and Cheap-First Routing

Objective:

- Decide whether the new path should become the harness’s primary desktop execution lane.

Scope:

- Add heuristic routing inside the new structured-state adapter:
  - cheap tier for easy, well-grounded AX decisions
  - stronger model for ambiguous steps or post-failure retries
- Only add a second model or provider after the baseline prompt/state format is stable enough that remaining uncertainty is genuinely model-driven.
- Add screenshot fallback only if Milestone 1-3 evidence shows AX coverage gaps on target apps.
- Run side-by-side comparison suite across:
  - `deterministic`
  - `structured_state_desktop`
  - `openai_cu`
  - optional hybrid variant if AX coverage requires it
- Expand desktop task coverage only after the first desktop slice is trusted.

Dependencies:

- Milestones 1-3 complete.

Exit gate:

- Structured-state desktop path matches or beats the screenshot-first desktop path on the trusted task set at materially lower cost or lower step count.
- Cheap-first routing retains acceptable success relative to the always-strong model path.
- If screenshot fallback is needed, its triggering conditions are simple and evidence-backed.

Acceptance experiments:

- Structured-state vs screenshot-first:
  - accept if structured state is at least as successful on the trusted desktop set and materially cheaper or shorter-step on average
- Cheap-first routing:
  - accept if routed mode retains at least 90% of always-strong success on the trusted set at meaningfully lower cost

#### Milestone 5: Default Promotion and Legacy Demotion

Objective:

- Promote the new architecture without losing comparison value too early.

Scope:

- Make the structured-state desktop adapter the documented primary desktop path.
- Reword reporting/docs/config so `openai_cu` is clearly a comparison lane, not the main direction.
- Remove or downgrade any repo defaults that still imply screenshot-first desktop execution.
- Keep legacy baselines available behind explicit adapter selection until there is enough historical comparison data.
- Do one explicit legacy-surface audit during promotion so stale assumptions do not linger in:
  - adapter registry/defaults
  - CLI help and run configs
  - report labels and comparison sections
  - README and planning docs
  - tests that still encode screenshot-first-as-primary assumptions

Dependencies:

- Milestone 4 comparison evidence.

Exit gate:

- The new path is the default desktop experiment path.
- Legacy screenshot-first desktop path remains intentionally available, but is no longer treated as the investment target.
- No shared reporting or CLI behavior breaks legacy runs.
- No stale defaults or docs still imply that screenshot-first desktop execution is the main path.

### 6. Testing Strategy

Per milestone, add tests before widening scope.

Milestone 1 tests:

- Unit tests for AX stable ID generation and pruning behavior.
- Unit tests for semantic action parsing and adapter prompt shaping.
- Unit tests for macOS semantic target resolution and coordinate fallback.
- Validation tests proving unsupported grader/check expressions fail fast.
- `llm_judge` unit tests with mocked model responses.

Milestone 2 tests:

- Task loader tests for optional milestone schema.
- Runner tests for milestone evaluation ordering and trace persistence.
- Report tests for milestone rendering and new semantic-action accounting.
- Failure-category tests only where the category is explicitly derivable from evidence.

Milestone 3 tests:

- Capture tests verifying AX snapshots are written alongside screenshots.
- Intent extraction tests verifying bounded AX context enters prompts correctly.
- Backward-compatibility tests with AX capture disabled.

Milestone 4 tests:

- Adapter tests for routing heuristic selection.
- Tests for fallback request behavior when AX coverage is sparse.
- Comparison-report tests for new adapter naming and metrics.

Manual / smoke verification:

- One real macOS smoke path for the new adapter on `desktop_textedit_save`.
- One manual capture/authoring session with AX enabled.
- Comparison run matrix kept intentionally small until milestone trust is established.

Legacy test handling:

- Keep current `openai_cu`, `codex_subscription`, browser, and deterministic tests initially.
- Update only the tests whose assumptions are truly obsolete.
- Do not rewrite legacy adapter tests into the new architecture; add dedicated tests for the new path first.

### 7. Migration and Rollback

Migration strategy:

- Add new capabilities behind new files and optional fields first.
- Keep existing task files loadable without milestones.
- Keep existing adapters registered and runnable during Milestones 1-4.
- Promote the new adapter only after comparison evidence exists.

Rollback strategy per phase:

- Milestone 1 rollback:
  - unregister the new adapter
  - ignore structured AX evidence artifacts
  - leave legacy runner/adapters untouched
- Milestone 2 rollback:
  - keep milestone fields optional and ignore them in the runner if needed
- Milestone 3 rollback:
  - disable AX capture and fall back to current screenshot/event flow
- Milestone 4 rollback:
  - disable cheap-first routing and use the stronger model only
  - disable screenshot fallback if it introduces noise

Comparison-preservation rule:

- Do not remove `openai_cu` until the repo has enough runs to compare “old screenshot-first desktop” vs “new structured-state desktop” on the same trusted tasks.
- Before demoting or removing any legacy path, preserve at least one stable comparison config and one representative comparison report artifact so later cleanup does not erase the learning record.

### 8. Manual Setup Tasks

- Choose a single strong structured-state model and a single cheap tier before Milestone 4; keep this configurable rather than hard-coding the research matrix into the architecture.
- Confirm macOS Accessibility and Screen Recording permissions for the terminal/Python process used by the harness.
- Prepare a stable local desktop task setup for:
  - TextEdit
  - Finder
  - one browser app already represented in the repo
- Add or update run configs for structured-state, comparison, and routed runs once the new adapter exists.
- Decide whether `tasks/my-test-task/task.yaml` becomes:
  - a repaired trusted task
  - or an explicitly experimental/non-comparable task

### 9. Risks

- AX coverage on target macOS apps may be worse than expected.
  - Mitigation: probe anchor apps early and gate fallback work on real coverage data.
- Stable AX IDs may not remain reliable across state changes.
  - Mitigation: start with pragmatic hash/path IDs plus coordinate fallback, and prove they work on short flows before investing in diffing.
- The current environment/action boundary may still assume coordinates too often.
  - Mitigation: keep semantic targeting inside `Action.params` first and resolve it inside the macOS environment rather than redesigning the global action model immediately.
- Existing tasks and graders may be more invalid than they look.
  - Mitigation: add validation early and treat unsupported tasks as untrusted until repaired.
- Reporting may still be too thin to debug the new path.
  - Mitigation: persist only decision-point evidence first, then widen if debugging still fails.
- Existing tests may encode screenshot-first assumptions.
  - Mitigation: preserve legacy tests where they still describe legacy behavior; add new tests instead of mutating unrelated ones.
- Demoting `openai_cu` may have ripple effects in reporting and docs.
  - Mitigation: defer naming/default changes until after comparison evidence exists.
- Prompting and pruning choices may dominate outcomes more than model choice.
  - Mitigation: keep the initial model matrix intentionally small and iterate on state quality first.
- macOS focus, permission, and windowing issues may create misleading failures.
  - Mitigation: keep initial tasks short, controlled, and explicitly milestone-checked.
- Retina/HiDPI coordinate mismatch between AX tree and pyautogui.
  - AXPosition and AXSize report in screen points. pyautogui on macOS should also use points, but this has not been verified empirically on the target hardware. If they diverge (e.g., pyautogui uses pixels on some configurations), every click silently misses by a 2x offset. Mitigation: include an explicit coordinate-space validation test in the M1 target resolution work — click a known AX element's center and verify the click lands on the correct control.
- AX tree capture latency on complex apps.
  - `_get_ax_tree()` traverses the full element tree via IPC to the target app. On simple apps (TextEdit) this is likely <100ms, but on complex apps (Chrome with many tabs) it could be >1s, which meaningfully slows the per-step loop. Mitigation: measure and log AX capture time during M1 serializer work. If latency is a problem, consider caching the tree within a single observe-then-resolve step or pruning depth more aggressively.
- Product-direction enthusiasm could pull the harness into speculative abstractions.
  - Mitigation: every added component must improve what the harness can prove now, not what a future product might need later.

### 10. Open Questions

- Should the first structured-state adapter use the existing `openai` dependency only, or introduce `anthropic`? Context: the research recommends Claude Sonnet 4.6, but the structured-state approach is model-agnostic — the prompt is text, the response is JSON, and any model with good structured-output handling can drive it. Using `openai` for M1 avoids a new dependency and lets the adapter work with GPT-4.1 or GPT-4.1-mini immediately. If the adapter's LLM call is cleanly abstracted (model name + client as constructor args), swapping to Anthropic later is a small change. The tradeoff: using OpenAI in M1 means the first comparison is structured-state-via-OpenAI vs screenshot-via-OpenAI, not a cross-provider comparison — which may actually be a cleaner experiment since it isolates the state-representation variable.
- Is the cleanest milestone-check surface an extension of `VerificationCheck`, or a separate milestone-only predicate model?
- Should `desktop_textedit_save` be upgraded in place to `version: "2.0"`, or should a separate v2 task file exist while the migration settles?
- Is `tasks/my-test-task/task.yaml` worth repairing now, or should it be excluded from trusted evaluation until the new verifier/task schema lands?
- Does `src/harness/reporting.py` need a dedicated evidence summary section, or is a link/reference to step evidence enough for the first pass?

## Explicit Defer-Until-Later List

- Cross-platform UIA / AT-SPI abstractions.
- Full branch/recovery task graphs.
- Learned routing or escalation classifiers.
- Video-first capture overhaul and ScreenCaptureKit integration.
- Vision-object parsers or OCR augmentation beyond a simple screenshot fallback.
- Shared structured-state abstraction across browser and desktop adapters, unless duplication becomes painful after the desktop path proves out.
- Removal of `openai_cu` or aggressive cleanup of legacy report structures.

## Recommended First Implementation Milestone

Start with Milestone 1: Trustworthy AX Desktop Slice.

That is the cleanest, lowest-complexity first build because it:

- validates the structured-state hypothesis directly
- keeps the current harness spine intact
- fixes the minimum trust gaps that would otherwise invalidate the experiment
- preserves old baselines for comparison
- avoids spending early on routing, fallback vision, or recorder/platform work before the core desktop path proves itself
