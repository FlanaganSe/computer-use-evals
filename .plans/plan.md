# Implementation Plan: Desktop Agent Eval Harness

Date: 2026-04-05

Based on `.plans/research.md` — a comprehensive analysis of the codebase, run traces, and external research (April 2026). That document contains verified bug evidence, design gap analysis, and the full research bibliography. This plan assumes the reader does not need to read it, but it is the source of truth for any disputed finding.

## Research Context

The ordering and scope of this plan are driven by specific research findings:

- **Harness quality dominates model quality.** A 7B model with good state representation matches a 139B model without it (SimpAgent / Memory Equalization Hypothesis). Improving how the harness presents state and reports results is higher-leverage than changing models. This is why Milestones 2-3 focus on the run loop before anything else.
- **Per-step accuracy compounds.** At 95% per-step accuracy, a 20-step workflow succeeds only 36% of the time. Every false negative (reporting failure when the action succeeded) and every wasted retry (looping on an already-successful action) compounds against end-to-end success. This is why closing the feedback loop and verifying by state change are the critical fixes.
- **AX-first is validated but not uniform.** Structured accessibility state outperforms screenshot-first (+67% success, -43.5% steps, 78% cost reduction — DMI EuroSys 2026). But macOS AX coverage varies: some apps report zero bounds, AXPress can return errors on success. The harness must measure and adapt to AX quality per-step, not assume it.
- **Readiness checks beat fixed delays.** GPA (April 2026) uses readiness calibration — polling for actual state change before proceeding. This is strictly better than the current fixed 0.5s sleep: faster when apps respond quickly, more reliable when they respond slowly.
- **Recordings are evidence, not replay plans.** Microsoft deprecated Record with Copilot (January 2026) because direct replay is too brittle. AgentRR and GPA both compile demonstrations into structured experience with verification anchors. This validates the author→compile separation in Milestone 4.

## Contract

### 1. Problem

The current harness proves that an AX-first desktop agent path is viable, but the end-to-end workflow is not yet trustworthy enough for research.

Today the pipeline is:

```text
capture -> author -> task load -> run -> grade -> compare
```

The main failures are not isolated to one stage:

- authored tasks can be schema-valid but still not executable
- the runner does not close the action-result loop back into the adapter
- the macOS environment treats execution as stringly-typed success/failure with fixed delays and weak post-action verification
- reports and configs do not yet support the research workflow the repo claims to support

The project intent is not “desktop automation” in the product sense. It is a small, flexible research harness that should make it easy to answer:

- how harness changes affect agent behavior on macOS desktop tasks
- how prompt / task-compilation changes affect agent performance
- which tool, environment, and orchestration improvements materially improve outcomes

### 2. Requirements

- Keep the system macOS-first and AX-first unless evidence justifies another substrate.
- Treat recordings as evidence for task understanding, not as direct replay plans.
- Make runtime behavior observable enough to diagnose loops, false negatives, and partial progress from artifacts alone.
- Ensure authored tasks are validated against the runtime contract before they are treated as runnable evals.
- Separate human-authored / VLM-authored intent from the runtime-ready task representation enough to support prompt and compilation experiments cleanly.
- Keep milestones independently verifiable and small enough to ship one at a time.
- Keep the repo type-safe, testable, and moving toward lint-clean rather than widening debt.

### 3. Acceptance Criteria

- A generated task cannot become a “runnable eval” without strict validation against the real runtime contract.
- The adapter can see the actual result of prior actions and the harness can stop obvious stagnation loops.
- The macOS environment reports structured execution outcomes with state-change evidence instead of only opaque strings.
- The harness records enough AX-quality and state-change metadata to support research conclusions about app tractability and action reliability.
- The primary desktop task path remains runnable end-to-end after each milestone.
- The documented experiment workflow is executable from the CLI and the core comparison metrics are trustworthy.

Concrete examples that must be true by the relevant milestones:

- A `press_keys` action with `key: ["CMD", "SHIFT", "S"]` executes without crashing the macOS environment.
- Milestone checks containing `{{directory}}/{{filename}}` inside list-nested dict structures load with variables resolved.
- After an executed action, the structured-state adapter no longer sees `"pending"` for all prior steps; it sees the actual recorded outcome.
- An AXPress transport error does not automatically count as failure if post-action state evidence shows that the UI changed.

### 4. Non-goals

- Cross-platform support.
- Building a production RPA system.
- Adding screenshot fallback or full substrate routing in this phase.
- Preserving backward compatibility for task schema or trace shape if a cleaner contract is needed.
- Building a large benchmark corpus before the harness itself is trustworthy.

### 5. Constraints

- The plan must be grounded in the current codebase and `.plans/research.md`, not in speculative redesigns.
- `docs/architecture.md` is absent, so code and ADRs are the effective source of truth.
- Existing desktop tasks and tests should continue to provide a runnable regression surface during the refactor.
- `ruff check src tests` is already failing; milestones should not introduce new lint debt and should aim to leave touched areas cleaner.

## Implementation Plan

### 1. Summary

Build the next phase in six milestones:

1. repair known correctness bugs and doc/runtime mismatches
2. replace the stringly-typed execution loop with a structured action-result contract
3. add state-diff readiness checks, AX-quality measurement, and stagnation handling
4. split authoring from compilation and validate compiled tasks against the actual runtime
5. make experiment execution and reporting match the documented workflow
6. improve capture only after the harness can measure whether those changes help

This ordering is intentional. The current highest-risk issue is that the harness cannot reliably tell whether an action worked. Capture and compilation improvements matter, but they are lower leverage until the run loop is trustworthy.

Implementation guardrails for orchestrators:

- Do not combine Milestones 2 and 3 into one change set. First make action outcomes truthful and typed; only then add readiness, loop enforcement, and AX-quality logic on top.
- Keep Milestone 4 intentionally small. The goal is a draft-to-compiled seam, not a large new planning framework or benchmark DSL.
- Keep Milestone 6 opt-in and staged. Start with the minimum capture changes needed to test alignment value, not the maximum amount of new evidence.

### 2. Current State

- `capture`: periodic screenshots plus optional AX snapshots and input events in `src/harness/capture.py`
- `author`: VLM prompt assembly and YAML draft generation in `src/harness/intent_extract.py`
- `task load`: variable substitution and check validation in `src/harness/task_loader.py`
- `run`: adapter/environment orchestration in `src/harness/runner.py`
- `desktop runtime`: AX observation and action execution in `src/harness/environments/macos.py`
- `desktop planning`: structured-state adapter in `src/harness/adapters/structured_state_desktop.py`
- `grade/report`: outcome grading and comparison metrics in `src/harness/graders.py` and `src/harness/reporting.py`

Verified issues that should shape the implementation order:

- known correctness bugs exist in key normalization, milestone substitution, metric calculation, and silent milestone errors
- the adapter action history lies because every prior action remains `"pending"`
- execution outcomes are too weakly typed to support clean verification or instrumentation
- the author stage still writes the final runtime YAML directly
- `run_configs/` exist but are not executable from the CLI

### 3. Files to Change

- `src/harness/types.py`
- `src/harness/runner.py`
- `src/harness/environments/macos.py`
- `src/harness/adapters/structured_state_desktop.py`
- `src/harness/adapters/deterministic.py`
- `src/harness/adapters/openai_cu.py`
- `src/harness/adapters/codex_subscription.py`
- `src/harness/task_loader.py`
- `src/harness/intent_extract.py`
- `src/harness/capture.py`
- `src/harness/cli.py`
- `src/harness/reporting.py`
- `README.md`
- task fixtures under `tasks/`
- tests under `tests/`

### 4. Files to Create

- `src/harness/compiler.py`
- `src/harness/runtime_results.py`
- `tests/test_compiler.py`
- `tests/test_runtime_results.py`
- `tests/test_cli_run_config.py`

These new modules are intentionally small. The goal is to isolate the two weakest contracts in the repo:

- runtime action outcomes
- authored task -> compiled runnable task

### 5. Milestone Outline

#### ~~Milestone 1: Correctness and contract cleanup~~ ✓ COMPLETE (2026-04-05)

- [x] Step 1 — Add tests for 4 verified bugs → verify: `uv run pytest tests/test_macos_env.py tests/test_task_loader.py tests/test_detailed_report.py tests/test_milestones.py -q`
- [x] Step 2 — Fix the 4 bugs (normalize_keys, substitute_in_dict, semantic_action_ratio, milestone logging) → verify: `uv run pytest -q`
- [x] Step 3 — Fix 8 ruff lint issues → verify: `uv run ruff check src tests`
- [x] Step 4 — Update README to remove run_configs executability claim → verify: manual
- [x] Step 5 — Full gate: ruff + mypy + pytest → verify: 330 passed, mypy clean, ruff clean
Commit: "fix correctness bugs, lint debt, and README run_configs claim"

Scope:

- fix the known mechanical bugs from research (source locations from trace-verified analysis):
  - `environments/macos.py:70` — `_normalize_keys(raw: str)` calls `raw.replace()`, crashes when LLM returns a list like `["CMD", "SHIFT", "S"]`. Handle both string and list input.
  - `task_loader.py:134-137` — list branch in `_substitute_in_dict` substitutes strings but skips dicts. Milestones (dicts inside lists) keep `{{var}}` unresolved. Add dict recursion.
  - `reporting.py:180` — `semantic_action_ratio` checks `"selector" in s.action` but the structured-state adapter writes `"semantic_target"`. The primary metric always reports 0%.
  - `runner.py:195-196` — milestone evaluation wrapped in `except: pass`. Replace with `except Exception as e: logger.warning(...)`.
- fix the 8 existing ruff lint issues (in `structured_state_desktop.py`, `ax_state.py`, `environments/macos.py`) so subsequent milestones start from a clean baseline
- resolve the README / CLI mismatch around `run_configs/`: remove the docs claim and defer config execution to Milestone 5

Why first:

- these are high-confidence bugs with known sources
- they reduce noise before deeper refactors
- starting lint-clean prevents accumulation across later milestones

Exit criteria:

- unit tests cover each fixed bug
- `ruff check src tests` returns 0 errors
- `python -m mypy src` passes
- all 324+ existing tests still pass

#### ~~Milestone 2: Structured action-result contract~~ ✓ COMPLETE (2026-04-05)

- [x] Step 1 — Add `RuntimeResult`, `ResultStatus`, `ExecutionMethod` in `src/harness/runtime_results.py` with summary/serialization → verify: `uv run pytest tests/test_runtime_results.py -q` (30 passed)
- [x] Step 2 — Update `Environment` protocol + macOS/browser implementations to return `RuntimeResult` → verify: `uv run pytest tests/test_macos_env.py -q` (51 passed)
- [x] Step 3 — Update runner to use `RuntimeResult.summary` for `StepRecord.result`; add `notify_result` to `Adapter` protocol; implement in structured-state adapter (real feedback), no-op in others → verify: `uv run pytest tests/test_runner_feedback.py -q` (12 passed)
- [x] Step 4 — Fix all tests for contract change; add runner feedback + prompt verification tests → verify: `uv run pytest -q` (372 passed)
- [x] Step 5 — Full gate: ruff + mypy + pytest → verify: 372 passed, mypy clean, ruff clean
Commit: "structured action-result contract with adapter feedback"

Scope:

- replace the current `str` result returned by `Environment.execute_action()` with a typed runtime result model
- extend the adapter protocol with result feedback
- update the runner to pass actual action outcomes back to adapters
- migrate trace persistence and reporting to the structured result while preserving a compact human-readable status in `report.md`

Implemented model shape (in `src/harness/runtime_results.py`):

- `status`: `ok | error | no_op | done | fail`
- `message`: human-readable summary
- `execution_method`: `coordinates | ax_press | keyboard | shell | wait | selector | other`
- `target_resolved`: bool
- `state_changed`: bool | None (populated in M3)
- `expected_change_observed`: bool | None (populated in M3)
- `metadata`: dict for app-specific detail

Exit criteria (all met):

- all adapters conform to the updated protocol
- the structured-state adapter prompt history shows real previous outcomes (not "pending")
- trace and report artifacts expose structured execution results via `RuntimeResult.summary`

#### ~~Milestone 3: Post-action verification, readiness, and stagnation handling~~ ✓ COMPLETE (2026-04-05)

- [x] Step 1 — Add state-diff helpers, AXQuality dataclass, update RuntimeResult constructors → verify: `uv run pytest tests/test_runtime_results.py tests/test_macos_env.py -q` (80 passed)
- [x] Step 2 — Replace fixed settle with readiness polling; populate state_changed; treat AXPress errors as provisional when state changed → verify: `uv run pytest tests/test_macos_env.py -q` (58 passed)
- [x] Step 3 — Add runner-owned stagnation/loop detection with tests → verify: `uv run pytest tests/test_stagnation.py -q` (15 passed)
- [x] Step 4 — Record per-step AX-quality metrics; surface in reporting → verify: `uv run pytest tests/test_detailed_report.py tests/test_structured_state_desktop.py -q` (76 passed)
- [x] Step 5 — Full gate: ruff + mypy + pytest → verify: 414 passed, mypy clean, ruff clean
Commit: "post-action verification, readiness polling, and stagnation detection"

Scope:

- add pre/post observation comparison in the macOS environment or runner-owned runtime result pipeline
- replace fixed post-action sleep with bounded readiness polling driven by actual state change
- treat AXPress transport errors as provisional until post-action verification says whether the UI changed
- add stagnation / loop detection in the runner using repeated action signatures plus unchanged-state evidence
- record AX-quality metrics per step and aggregate them in reports

Minimal metrics to add:

- `interactive_elements_total`
- `interactive_elements_with_bounds`
- `interactive_elements_without_bounds`
- `target_found`
- `action_transport`
- `state_changed`
- `stagnation_detected`

Why this milestone is separate:

- this is the first milestone that changes runtime behavior materially
- it should land only after the result contract is clean and testable

Exit criteria:

- the runner can terminate obvious no-progress loops with an explicit reason
- action success is no longer determined solely by AXPress return codes
- reports can show whether a failure was caused by poor AX quality, no state change, or later planning failure

#### ~~Milestone 4: Split authoring from compilation~~ ✓ COMPLETE (2026-04-05)

- [x] Step 1 — Add `agent_brief` to `TaskGoal`; create `DraftTask` and `CompileMetadata` models in `compiler.py` → verify: `uv run pytest tests/test_compiler.py -q`
- [x] Step 2 — Implement `compile_draft()` and `compile_draft_file()` with compile-time validation (check expressions, variable refs, script paths) → verify: `uv run pytest tests/test_compiler.py -q` (36 passed)
- [x] Step 3 — Update `author_task()` to emit `DraftTask` with `compile_metadata`; update extraction prompt for `agent_brief` → verify: `uv run pytest tests/test_intent_extract.py -q` (37 passed)
- [x] Step 4 — Add `harness compile` CLI command; add CLI compile tests → verify: `uv run pytest tests/test_cli_compile.py -q` (10 passed)
- [x] Step 5 — Update adapters to prefer `agent_brief` over `description` → verify: `uv run pytest -q`
- [x] Step 6 — Full gate: ruff + mypy + pytest → verify: 460 passed, mypy clean, ruff clean
Commit: "split authoring from compilation with draft→compile pipeline"

Scope:

- introduce a separate compile step between authored evidence interpretation and runnable eval execution
- keep the authored artifact human-editable
- compile into the runtime task contract and reject tasks that fail strict validation
- stop overloading one field for all audiences by separating:
  - human-facing task description
  - agent-facing execution brief
  - verification contract
  - compile metadata derived from evidence

Recommended approach:

- keep the existing `Task` model as the compiled runtime shape or evolve it if needed
- add the smallest viable authored representation that `harness author` produces
- add `harness compile` to normalize, validate, and emit the runnable task
- call `load_task(strict=True)` as part of compilation, not only at runtime

Practical constraint:

- prefer a small draft artifact or companion metadata file over a large parallel schema tree
- only add new authored-model fields when they materially improve compile validation, prompt experiments, or human review

Why this is the right level of change:

- it addresses the real “multiple compiled versions” concern without building a large framework
- it preserves a clean seam for prompt experiments

Exit criteria:

- `author` no longer directly creates the final trusted runtime artifact
- invalid checks, unresolved compile-time issues, and schema/runtime mismatches fail during compile
- in-tree tasks are migrated to the compiled contract in the same milestone

#### ~~Milestone 5: Experiment execution and reporting workflow~~ ✓ COMPLETE (2026-04-05)

- [x] Step 1 — Add `--config` to `run` CLI with tests → verify: `uv run pytest tests/test_cli_run_config.py -q` (15 passed)
- [x] Step 2 — Add runtime verification section to detailed comparison report → verify: `uv run pytest tests/test_detailed_report.py -q` (44 passed)
- [x] Step 3 — Update README and plan docs → verify: manual
- [x] Step 4 — Full gate: ruff + mypy + pytest → verify: 479 passed, mypy clean, ruff clean
Commit: "config-driven experiment execution and reporting workflow"

Scope:

- make `run_configs/*.yaml` executable from the CLI
- ensure comparison reporting uses the corrected semantic-action and runtime-result data
- add report sections that answer the core research questions directly:
  - success / failure
  - where progress stopped
  - whether actions changed the UI
  - AX quality during the run
  - step count / cost / routing behavior
- clean up outdated docs so the repo’s documented workflow matches the actual workflow

Exit criteria:

- a documented config in `run_configs/` can be run without manual command translation
- reports support adapter/harness comparisons without misleading metrics

#### ~~Milestone 6: Capture improvements behind evidence gates~~ ✓ COMPLETE (2026-04-05)

- [x] Step 1 — Add aligned timeline types, EventTap trigger queue, app context helper → verify: `uv run pytest tests/test_capture.py -q`
- [x] Step 2 — Implement aligned capture mode in capture_session with poll-based loop, focus tracking, timeline building → verify: `uv run pytest tests/test_capture.py -q`
- [x] Step 3 — Add --aligned CLI flag; update intent_extract.py to load and prefer aligned timeline → verify: `uv run pytest tests/test_intent_extract.py -q`
- [x] Step 4 — Add focused tests for aligned capture, timeline structure, and authoring consumption → verify: `uv run pytest tests/test_capture.py tests/test_intent_extract.py -q`
- [x] Step 5 — Full gate: ruff + mypy + pytest; update docs → verify: 543 passed, mypy clean, ruff clean (on changed files)
Commit: "event-aligned capture mode with timeline evidence"

Scope:

- add event-aligned capture mode with aligned screenshots and AX snapshots
- capture explicit app/window focus transitions when available
- persist aligned timelines in the manifest so authoring can use them directly

Initial scope limit:

- start with alignment around clicks, app switches, and other high-signal transitions
- do not default to per-keystroke pre/post screenshots unless earlier milestones show that authoring quality is still bottlenecked by sparse evidence
- keep artifact growth and capture overhead measurable as part of the milestone

Important gate:

- only do this after Milestones 1–5 because the repo currently cannot cleanly measure whether capture changes improved authored-task quality or runtime outcomes
- if Milestone 4 already fixes the main authoring reliability issues, keep capture changes minimal

Exit criteria:

- aligned capture can be enabled intentionally for research runs
- authoring prompts can consume aligned evidence without bespoke manual correlation

### 6. Testing Strategy

Quality gate — every milestone must pass before committing:

```
ruff check src tests && python -m mypy src && python -m pytest tests/ -q
```

All three must return zero failures. This is not aspirational — it is a hard gate. The repo currently has 324 passing tests, strict mypy, and 8 ruff issues (fixed in Milestone 1).

For every milestone:

- update or add unit tests first for the contract being changed
- keep the fast test suite as the primary gate
- run targeted tests for touched areas before the full suite

Per milestone focus:

- Milestone 1: unit tests in task loader, reporting, macOS env, milestone handling
- Milestone 2: protocol and runner tests, adapter tests, trace serialization tests
- Milestone 3: macOS env tests for readiness polling, state-diff classification, loop detection tests in runner
- Milestone 4: compiler tests, author/compile CLI tests, migration tests for in-tree tasks
- Milestone 5: config-runner CLI tests and reporting golden-file tests
- Milestone 6: capture manifest tests and authoring prompt assembly tests

Manual verification after Milestones 3, 4, and 5:

- run the primary structured desktop task(s) end-to-end on macOS
- inspect `trace.json`, `grade.json`, `report.md`, and `evidence.json`
- confirm that the artifacts explain both success and failure without replaying the task

### 7. Migration and Rollback

Migration strategy:

- keep each milestone self-contained and repo-wide
- migrate in-tree tasks and tests in the same milestone that changes their contract
- prefer one-step migrations over prolonged compatibility shims

Rollback strategy:

- rollback at the milestone commit boundary, not with long-lived runtime flags
- if a milestone materially worsens end-to-end behavior, revert that milestone and keep the prior verified surface intact

The one exception is Milestone 2. During that refactor, it is reasonable to persist both:

- a structured runtime result for code and reports
- a compact string summary for easy human scanning

### 8. Manual Setup Tasks

- maintain Accessibility and Screen Recording permissions for the Python process used by the harness
- keep `OPENAI_API_KEY` available for authoring and LLM-backed adapters
- keep browser dependencies installed for browser-baseline regression tests
- verify AppleScript / app automation permissions for native-app setup and verification scripts where needed

### 9. Risks

- The typed runtime-result refactor touches every adapter and the main runner. This is the highest integration risk, but it is still the cleanest path.
- State-diff verification can misclassify success if the AX tree is noisy or unchanged for legitimate reasons. The design should preserve `state_changed = None` when the harness cannot tell.
- Splitting authoring from compilation can become over-engineered if it turns into a large schema system. The milestone should stay focused on one new seam: draft -> compiled runtime task.
- Event-aligned capture can increase artifact volume quickly. It should remain opt-in until it proves its value.
- The repo lacks a maintained architecture document. The implementation should update ADRs or docs as milestones land so the source of truth stays discoverable.

### 10. Resolved Design Decisions

These were open questions during planning. Each is resolved here so implementers don't need to re-derive the answer.

**Compiled task model**: Keep the existing `Task` model as the compiled runtime shape. Do not rename or create a parallel model. The compile step validates and normalizes into `Task`, but the model itself is sufficient. If Milestone 4 implementation reveals a genuine need to tighten it, that change happens within M4 — not speculatively.

**Stagnation detection ownership**: Runner-owned enforcement with adapter-visible feedback. The runner tracks action signatures and state-change evidence. When it detects stagnation, it injects context into the next observation (so the adapter/LLM can adapt) and terminates after sustained stagnation. This centralizes safety where it applies to all adapters, keeps adapters simple, and matches the architectural principle that the runner owns the loop.

**`expected_change` evaluation**: Start with binary state-diff only — did the AX tree change at all after the action? This is the minimum viable signal and avoids the complexity of semantic matching against free-text LLM descriptions. Binary change detection is cheap (compare interactive element IDs before/after) and sufficient for the key use cases: detecting no-ops, overriding false-negative AXPress errors, and feeding state-change evidence into the runtime result. Semantic matching against `expected_change` text is a future enhancement if binary proves insufficient.

**Config runner CLI shape**: Extend `run` with `--config <path>` rather than adding a new subcommand. Simpler, one fewer command to document, and the config file already specifies everything a dedicated subcommand would need as arguments.

**Author output after Milestone 4**: Yes, `author` writes a draft artifact (not the final runtime task). `compile` transforms the draft into a validated runtime task. This is the entire point of Milestone 4's authoring/compilation split.

## Recommended Execution Order

Start with Milestone 1, then Milestone 2. Do not begin Milestone 4 before Milestone 3 is verified on real desktop tasks. Capture improvements should be intentionally last unless new evidence shows authoring, not runtime, is the dominant remaining failure source.
