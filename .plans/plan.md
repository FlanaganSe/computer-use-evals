# Implementation Plan

## Contract

### 1. Problem

We need a macOS-first proof-of-concept eval system for desktop and browser agent workflows. The system should help us understand whether recorded user activity and normalized task artifacts can be turned into reliable agent-executable tasks, and which factors most affect performance: observation format, grounding strategy, model choice, tool access, execution method, and harness design.

The immediate goal is not to build the end-user product. The immediate goal is to build enough harness infrastructure to run repeatable experiments, inspect failures, and compare conditions quickly so we can reduce unknown-unknowns in this problem space.

### 2. Requirements

1. Define a minimal canonical task artifact for the PoC that represents user intent and task setup without overcommitting to a full workflow language.
2. Support a small, real task suite for macOS-first desktop and browser flows, focused on repeatable tasks with objective outcome checks.
3. Model the harness around explicit core objects: `task`, `trial`, `trace`, `grader`, and `report`.
4. Allow independent comparison of at least these dimensions:
   - observation mode,
   - grounding strategy,
   - model/runtime choice,
   - execution method/tooling.
5. Capture enough trace data to explain failures, not just score them.
6. Prioritize fast local iteration, simple setup, and reversible design choices over completeness.
7. Keep task grading outcome-focused where possible, with programmatic checks preferred over path checking or judge-based scoring.
8. Make unknowns visible through metrics and failure taxonomy rather than hiding them behind opaque abstractions.
9. Preserve room to evaluate Codex OAuth / local CLI usage, but do not make the PoC dependent on one provider or runtime before that is validated.

### 3. Acceptance Criteria

1. There is a documented plan for a first implementation that can run a small starter task suite on macOS with isolated trials and reproducible scoring.
2. The plan defines the first artifact schema, harness boundaries, and the minimal storage layout for tasks, traces, evidence, and reports.
3. The plan includes at least one milestone dedicated to proving the harness loop end-to-end before adding capture/recording sophistication.
4. The plan makes comparison conditions explicit so we can answer questions such as:
   - whether screenshots alone are sufficient,
   - whether accessibility data materially helps,
   - whether tool-assisted execution outperforms raw GUI control,
   - whether harness/context changes matter more than model swaps.
5. The plan includes success metrics, failure categories, and verification strategy for the first task families.
6. The plan identifies manual setup and decision gates that require operator input rather than assuming they can be automated away.
7. The plan stays intentionally small enough to ship as a PoC without creating a custom framework we would need to unwind later.

### 4. Non-goals

1. Building the full user-facing recording product.
2. Solving cross-platform support beyond keeping future expansion possible.
3. Designing a universal benchmark platform or generalized workflow DSL.
4. Optimizing for enterprise scale, hosted orchestration, or multi-tenant infrastructure.
5. Achieving production-grade safety, auth, or permissions handling beyond what is necessary for controlled PoC experiments.
6. Assuming that screen recordings themselves are the canonical replay format; for the PoC they are evidence for authoring normalized tasks.

### 5. Constraints

1. Current repository state is minimal: only research and ideation documents exist, with no implementation scaffold yet.
2. The plan must be grounded in `docs/research-consolidated.md` and the repo’s current state, not in a speculative architecture detached from what we can realistically build next.
3. The PoC should remain simple, inspectable, and easy to change; custom abstractions must earn their keep.
4. macOS is the initial platform, and accessibility coverage on macOS is known to be incomplete, so the design cannot depend on AX alone.
5. Eval speed matters; the system must support quick iteration and not require heavyweight infrastructure for every experiment.
6. We should prefer local/open tooling where practical, but tool choices must stay swappable until we validate their fit.
7. Some decisions are inherently manual at this stage, including task curation, environment setup policy, and acceptable initial runtime/tooling tradeoffs.

## Implementation Plan

### 1. Summary

This PoC should be built as a thin local harness, not as a large framework integration and not as a recording product. The harness should answer one question well: given a normalized task package, can different agent setups complete desktop and browser workflows reliably, and can we explain why they fail when they do not?

The highest-leverage decision is to make the harness boundary explicit and stable early:

1. **Canonical artifact**: a normalized task package stored as YAML plus assets, not a raw recording.
2. **Execution core**: a small custom local runner with narrow interfaces for observation, model adapter, executor, grader, and reporting.
3. **Environment policy**: start on host macOS with deterministic setup/cleanup scripts for speed; add VM isolation only after the host loop exposes what actually needs isolation.
4. **Provider policy**: keep model/runtime access behind adapters so we can compare OpenAI Responses computer-use, Codex CLI-driven runs, and deterministic/non-LLM baselines without rewriting the harness.
5. **Capture policy**: defer full user recording ingestion until the eval loop exists; in the PoC, recordings are evidence for task authoring, not the primary runtime format.

These decisions minimize risk because they keep the first implementation close to the real unknowns:

1. task specification quality,
2. observation quality,
3. execution reliability,
4. grading reliability,
5. failure explainability.

### 2. Current State

As of April 4, 2026, the repository contains only ideation and research documents:

1. `docs/research-consolidated.md`
2. `docs/init-idea.md`

There is currently no implementation scaffold, no package manager, no runtime code, no tests, no CLI, and no task corpus. That means the first implementation plan needs to establish the project skeleton as well as the eval model.

Grounded implications from the current state and research:

1. We should not commit to a framework-heavy architecture before we have even one end-to-end passing task.
2. The first harness must be inspectable from the filesystem and CLI, since understanding "what happened and why" is part of the product value.
3. The plan should produce useful results before the recording subsystem exists.
4. The plan should let us compare harness choices, not just model choices.

### 3. Files To Change

Existing files that should likely be updated during implementation:

1. `docs/research-consolidated.md`
   - only if implementation uncovers factual mismatches or if we add a short "validated assumptions" appendix.
2. `docs/init-idea.md`
   - only if we want to trim outdated wording once the first scaffold exists.
3. `.plans/plan.md`
   - this file, as milestones complete or decisions materially change.

No existing source files currently constrain implementation because no source tree exists yet.

### 4. Files To Create

Recommended initial project shape:

1. `README.md`
2. `pyproject.toml`
3. `.gitignore`
4. `src/harness_evals/__init__.py`
5. `src/harness_evals/cli.py`
6. `src/harness_evals/config.py`
7. `src/harness_evals/models/task.py`
8. `src/harness_evals/models/trial.py`
9. `src/harness_evals/models/trace.py`
10. `src/harness_evals/models/report.py`
11. `src/harness_evals/task_loader.py`
12. `src/harness_evals/runner.py`
13. `src/harness_evals/reporting.py`
14. `src/harness_evals/failures.py`
15. `src/harness_evals/adapters/model/base.py`
16. `src/harness_evals/adapters/model/deterministic.py`
17. `src/harness_evals/adapters/model/openai_responses.py`
18. `src/harness_evals/adapters/model/codex_cli.py`
19. `src/harness_evals/adapters/observation/base.py`
20. `src/harness_evals/adapters/observation/screenshot.py`
21. `src/harness_evals/adapters/observation/macos_ax.py`
22. `src/harness_evals/adapters/observation/filesystem.py`
23. `src/harness_evals/adapters/executor/base.py`
24. `src/harness_evals/adapters/executor/browser.py`
25. `src/harness_evals/adapters/executor/macos_ui.py`
26. `src/harness_evals/adapters/environment/base.py`
27. `src/harness_evals/adapters/environment/host_macos.py`
28. `src/harness_evals/graders/base.py`
29. `src/harness_evals/graders/programmatic.py`
30. `src/harness_evals/graders/judge.py`
31. `tests/test_task_loader.py`
32. `tests/test_runner_smoke.py`
33. `tests/test_graders.py`
34. `tests/test_reports.py`
35. `tasks/browser/download_rename/task.yaml`
36. `tasks/browser/form_fill/task.yaml`
37. `tasks/desktop/file_export/task.yaml`
38. `tasks/_shared/`
39. `scripts/setup/`
40. `scripts/cleanup/`
41. `scripts/manual/`
42. `runs/.gitkeep`
43. `docs/architecture.md`
44. `docs/decisions.md`

Notes on these choices:

1. Python is the lowest-risk starting language here because the likely ecosystem touchpoints are Python-heavy: desktop automation libraries, Playwright support, grading libraries, and Inspect AI if we later integrate it.
2. The directory names intentionally mirror the explicit research objects and comparison dimensions.
3. `tasks/` and `runs/` are first-class project assets, not test fixtures hidden under `tests/`.

### 5. Milestone Outline

#### Milestone 1: Establish The Minimal Harness Loop

Goal: prove that a normalized task package can be loaded, executed through a local runner, graded, and written to disk with a trace and report.

Scope:

1. Bootstrap the Python project and CLI.
2. Define versioned Pydantic models for `task`, `trial`, `trace`, and `report`.
3. Define the first task package schema as YAML plus optional assets.
4. Implement a deterministic runner path with no live model dependency.
5. Implement run directories that persist:
   - resolved task input,
   - step trace,
   - artifacts metadata,
   - grader output,
   - summary report.
6. Add one trivial end-to-end task that can pass deterministically.

Why first:

1. This milestone proves the artifact model and filesystem layout before we add desktop complexity.
2. It gives us a debugging surface for every later milestone.
3. It avoids premature commitment to any external agent framework.

Exit criteria:

1. `run task` works locally on one deterministic sample task.
2. A completed run produces a readable trace and report on disk.
3. A failing run is visibly classifiable as setup, execution, or grader failure.

#### Milestone 2: Author A Small, Outcome-Graded Starter Suite

Goal: stand up a small real task suite with objective verification and deterministic environment setup.

Scope:

1. Create 3 initial tasks:
   - browser download plus rename,
   - browser form fill,
   - desktop file transform or export.
2. Add task setup and cleanup scripts.
3. Add programmatic graders for all 3 tasks.
4. Add a failure taxonomy and attach failure labels to grader and runner outputs.
5. Add pass/fail and per-step reporting across repeated trials.

Why second:

1. The harness is not useful until it can compare more than one task.
2. Real tasks expose schema gaps faster than speculative schema design.
3. Programmatic verification gives us a strong baseline before judge-based grading is introduced.

Exit criteria:

1. At least 3 tasks run from the CLI with setup, execution, and verification.
2. Reports aggregate multiple trials per task.
3. The task schema has survived contact with real workflows without becoming a mini-DSL.

#### Milestone 3: Add Baselines And Swappable Agent Adapters

Goal: compare harness and model choices without changing the harness core.

Scope:

1. Implement the first model/runtime adapter interface.
2. Ship at least 3 runners:
   - deterministic baseline,
   - OpenAI Responses computer-use adapter,
   - Codex CLI adapter.
3. Add a comparison matrix CLI mode that runs the same task suite across conditions.
4. Record model/tool metadata, token or cost metadata where available, and action summaries.
5. Add run configuration files so experiments are reproducible.

Critical decision made here:

1. **Do not make Inspect AI the day-1 runtime backbone.**
2. Instead, keep a thin internal runner and make Inspect AI a later integration candidate if it helps with orchestration or dataset handling.

Reasoning:

1. OpenAI’s current computer-use guidance is still harness-oriented: the model inspects screenshots and returns structured actions for *your harness* to execute.
2. The core unknown for this project is the harness boundary itself.
3. If we start inside a framework, we risk confusing framework behavior with agent behavior and losing observability into the exact place where the PoC needs clarity.

Exit criteria:

1. The same task can run under at least 2 materially different adapters.
2. Reports make cross-condition comparison easy.
3. Failures can be separated into model errors versus harness or environment errors with reasonable confidence.

#### Milestone 4: Add Observation Layers And Execution Telemetry

Goal: move from "did it pass?" to "what inputs and execution choices changed the outcome?"

Scope:

1. Add screenshot observation capture.
2. Add macOS AX capture as optional structured context.
3. Add window metadata, filesystem deltas, and clipboard/download observations where feasible.
4. Implement semantic action ratio, AX availability rate, fallback rate, and step success reporting.
5. Record raw model requests and action outputs when safe and practical.
6. Add an initial hybrid observation mode:
   - screenshot-only,
   - AX-only where available,
   - hybrid.

Why this is a separate milestone:

1. Research strongly suggests harness and observation design matter more than raw model choice.
2. We should measure that directly instead of assuming it.

Exit criteria:

1. The harness can compare at least 2 observation modes on the same task suite.
2. Reports can show whether structured context is helping or not.
3. We can quantify when the system falls back to pixels or coordinates.

#### Milestone 5: Add Environment Controls And Regression Detection

Goal: reduce noise and make repeated comparisons trustworthy.

Scope:

1. Standardize environment setup and cleanup contracts per task.
2. Add trial repetition and seedable run configuration.
3. Introduce golden comparison outputs for traces and report summaries.
4. Add lightweight regression checks suitable for CI for deterministic paths.
5. Evaluate whether host macOS is still sufficient or whether we need isolated VM execution for specific task families.

Decision gate:

1. If host execution noise is low enough for the starter suite, stay on host for the PoC and defer virtualization.
2. If environment noise is blocking evaluation quality, add a VM-backed environment adapter next, potentially using Cua or another macOS VM layer.

Reasoning:

1. VM isolation is attractive, but it adds setup and debugging overhead.
2. We should earn that complexity by proving host execution is the limiting factor.

Exit criteria:

1. We can repeat the same configuration enough times to trust trend comparisons.
2. Regression checks catch obvious harness breakage before manual reruns.
3. The virtualization decision is explicit and documented.

#### Milestone 6: Add Evidence-Ingest Prototypes For Task Authoring

Goal: connect the eventual recording vision back to the eval harness without turning the project into a product build.

Scope:

1. Define an `evidence/` layout for screenshots, notes, transcript snippets, and optional structured metadata.
2. Add a manual or semi-assisted flow that converts evidence into a normalized task package.
3. Support optional narration and post-hoc intent editing at the artifact level.
4. Evaluate whether a lightweight capture import is enough, or whether a dedicated recording tool integration is justified.
5. Keep runtime execution fully task-package-based; evidence should not become the direct replay medium.

Why this comes late:

1. Without a working eval loop, evidence capture tells us little.
2. Once the loop exists, we can evaluate which evidence actually improves task authoring or runtime performance.

Exit criteria:

1. At least one task package can be authored from recorded evidence.
2. The evidence format remains auxiliary and does not distort the core harness architecture.
3. The cost and value of richer capture are visible enough to decide whether further productization is justified.

### 6. Testing Strategy

Testing should follow the same philosophy as the harness: outcome-first, layered, and cheap by default.

1. **Schema tests**
   - validate task, trial, trace, and report models,
   - validate versioning and backwards-compatibility behavior for the first schema revision.
2. **Pure unit tests**
   - task loading,
   - variable substitution,
   - failure taxonomy mapping,
   - report aggregation.
3. **Deterministic smoke tests**
   - run a no-model task end to end in CI,
   - ensure run artifacts are written with stable structure.
4. **Gated integration tests**
   - browser executor tests,
   - macOS-specific executor tests,
   - live model adapter tests behind explicit env flags and never as mandatory default CI.
5. **Golden-output tests**
   - compare normalized trace/report outputs for deterministic runs,
   - use these for regression detection when harness code changes.
6. **Manual eval protocol**
   - for live-agent conditions, use a small fixed runbook:
     - reset environment,
     - run N repeated trials,
     - inspect failures,
     - tag taxonomy,
     - record decision notes.

Important constraint:

1. Do not try to make flaky live desktop execution a mandatory default CI gate in the first implementation.
2. CI should protect harness correctness; manual or scheduled runs should answer agent performance questions.

### 7. Migration And Rollback

This project starts greenfield, so migration risk is low, but artifact stability still matters.

1. Version the task schema from the start.
2. Keep run artifacts append-only under `runs/` so historical evidence is preserved.
3. Avoid hidden state in SQLite or a hosted service in the first version unless the value is immediate and obvious.
4. If an adapter or observation path proves weak, disable it via config rather than removing core interfaces.
5. If a framework integration such as Inspect AI is added later, keep it as an adapter layer that can be removed without changing task artifacts.

Rollback strategy:

1. The runner should degrade to the deterministic baseline even if live model adapters fail.
2. The host-environment path should remain available even if VM integration is attempted and abandoned.
3. Judge-based grading should always be optional and never the sole source of truth for starter tasks.

### 8. Manual Setup Tasks

These should be documented explicitly because hidden setup destroys eval credibility.

1. Install Python and the chosen project manager, preferably `uv`.
2. Install browser dependencies if the browser executor uses Playwright.
3. Grant macOS permissions required for:
   - accessibility,
   - screen recording,
   - automation where needed.
4. Choose and configure provider credentials for any live adapters.
5. Decide which initial model conditions to compare.
6. Decide whether Codex CLI runs should be authenticated through local login, API-backed provider config, or both.
7. Prepare reproducible local fixtures for task assets such as test PDFs, CSVs, and target directories.
8. Decide where run artifacts are stored locally and whether large screenshots should be ignored, compressed, or retained selectively.
9. If VM isolation is later introduced, document the provisioning and reset steps separately instead of mixing them into the host setup path.

### 9. Risks

1. **Premature framework lock-in**
   - If we start with a large eval framework, we may learn less about the actual harness problem.
2. **Task schema overgrowth**
   - The task artifact can easily become a workflow DSL. That would slow iteration and hide intent.
3. **Observation bloat**
   - Capturing too much screenshot history or metadata can increase cost and latency without enough quality gain.
4. **Host-environment flakiness**
   - Desktop state, permissions, and timing issues can create false negatives that look like model failures.
5. **macOS AX incompleteness**
   - AX may be unavailable or incomplete on important screens, forcing visual fallbacks.
6. **Provider coupling**
   - Codex CLI output conventions, OpenAI API behaviors, or model pricing and limits can change; adapters must absorb that change.
7. **Weak graders**
   - If grading is not objective, we will end up benchmarking grader noise rather than agent quality.
8. **Task-suite bias**
   - A narrow starter suite can overfit the harness to easy tasks and mislead us about general feasibility.
9. **Recording-to-task complexity**
   - Translating user evidence into clean intent may be substantially harder than executing authored tasks.
10. **Invisible manual work**
   - If task cleanup, permissions, or environment resets rely on undocumented operator behavior, the evals will not be trustworthy.

### 10. Open Questions

1. Should the first live model condition be OpenAI Responses computer-use, Codex CLI, or both in the same milestone?
2. What is the exact minimum initial task suite size: 3 tasks for speed, or 5 to 6 tasks for better comparison coverage?
3. Which browser and desktop automation primitives are least risky on macOS for the first executor implementation?
4. Should the first task package permit embedded evidence references, or should evidence stay fully outside the schema until Milestone 6?
5. When do we promote VM isolation from optional to required?
6. Do we need an explicit human-baseline protocol in the PoC, or is agent-to-agent and baseline-to-agent comparison sufficient initially?
7. Should judge-based graders be introduced only after the starter suite is exhausted programmatically, or earlier for specific UX-heavy tasks?
8. How much effort should go into Codex OAuth and local CLI support before we have evidence that those paths are materially useful for the eval questions?
9. Do we want run artifact persistence to remain file-based only, or do we need a lightweight local database once trial volume grows?

## Recommended First Implementation Order

If we start implementation immediately after this plan, the safest order is:

1. Milestone 1
2. Milestone 2
3. Milestone 3 with only one live adapter first
4. Milestone 4
5. Milestone 5
6. Milestone 6

The most important discipline is this: do not start with recording ingestion, VM isolation, hosted eval tooling, or generalized workflow abstractions. Start with one harness loop, a few real tasks, and explicit comparisons. That is the shortest path to learning what matters.
