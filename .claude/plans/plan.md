# Desktop Agent Eval Harness — Implementation Plan

## Contract

### 1. Problem

We need a macOS-first proof-of-concept eval harness that helps us understand whether agents can reliably execute browser and desktop workflows from user intent, where and why they fail, and which harness choices materially improve or degrade performance.

This is not a product build. It is a controlled learning system. Its job is to reduce unknown-unknowns around:

1. observation format,
2. grounding quality,
3. execution reliability,
4. task specification quality,
5. provider/runtime choice,
6. cost and iteration speed,
7. failure explainability.

The plan must support two separate experiment families without conflating them:

1. **Provider-native computer-use evals**
   - true screenshot-to-action loops using a provider’s computer-use interface,
   - initially OpenAI `computer-use-preview` via the Responses API.
2. **Subscription-backed Codex evals**
   - ChatGPT/Codex-authenticated runs using Codex as a constrained decision-maker over harness-provided state,
   - initially browser-only and semantic-action-first,
   - explicitly *not* treated as equivalent to provider-native computer-use.

### 2. Requirements

**P0** (must have for PoC):

1. Build a thin local harness around explicit core objects:
   - `task`,
   - `trial`,
   - `trace`,
   - `grader`,
   - `report`.
2. Define a minimal canonical task package as YAML plus assets.
3. Run one deterministic browser task end to end without any paid model dependency.
4. Persist readable run artifacts to disk so failures can be inspected after the fact.
5. Support fast local iteration on macOS.

**P1** (must have before this is a useful experimental tool):

6. Add a provider-native computer-use track using OpenAI `computer-use-preview`.
7. Add a separate Codex subscription-backed track using ChatGPT/Codex login on a trusted local machine.
8. Keep those tracks behind a shared harness core but report them separately.
9. Support a small browser-first task suite with programmatic grading.
10. Capture enough trace data to explain failures, not just score them.

**P2** (add only if earlier milestones show signal):

11. Add richer observation modes such as screenshot plus structured browser state.
12. Add native macOS desktop tasks.
13. Add VM isolation if host noise becomes the binding constraint.
14. Add evidence or recording ingestion for task authoring.

### 3. Acceptance Criteria

- **Given** a task package and a deterministic adapter, **when** `run` is invoked, **then** the harness executes the task, writes a trace, and produces a pass/fail grade with no model dependency.
- **Given** the same browser task under the paid computer-use track and the Codex subscription track, **when** a comparison report is generated, **then** the report clearly labels them as different experiment families and shows outcome, step count, cost or quota metadata, and failure categories for each.
- **Given** a failed trial, **when** the trace is inspected, **then** the failure can be classified into one of:
  - perception,
  - planning,
  - execution,
  - context,
  - environment,
  - tool-choice,
  - harness.
- **Given** no API key and no Codex login, **when** the deterministic baseline runs, **then** it still validates the harness core in isolation.

### 4. Non-goals

1. Building the user-facing recording product.
2. Designing a generic benchmark platform or workflow DSL.
3. Cross-OS support beyond keeping future expansion possible.
4. Large-scale infrastructure, hosted orchestration, or database-heavy architecture.
5. Full factorial benchmarking across many providers and many conditions at once.
6. Treating raw recordings as the canonical replay format.
7. Treating Codex subscription-backed runs as apples-to-apples replacements for provider-native computer-use.

### 5. Constraints

1. **Greenfield repo** — only research and idea docs exist.
2. **macOS AX coverage is incomplete** — we cannot build critical paths that depend entirely on accessibility APIs.
3. **OpenAI `computer-use-preview` is only available via the Responses API** — it is an API-priced path, not a ChatGPT subscription path.
4. **Codex supports ChatGPT-managed auth**, but OpenAI documents API keys as the recommended default for automation. ChatGPT-managed Codex auth for automation is an advanced workflow and should be treated as experimental and local-first.
5. **Codex is not a provider-native computer-use model path** — it should not be used as if it were a drop-in replacement for screenshot-to-action API evals.
6. **Eval speed matters** — we should not add framework or infrastructure overhead before the shared harness loop proves useful.
7. **Tooling must stay swappable** until actual runs show which path is informative.

---

## Implementation Plan

### 1. Summary

Build a thin, file-based Python harness that answers one question well:

**Given a normalized task package, can different agent setups complete a workflow reliably, and can we explain why they fail when they do not?**

To minimize risk, the plan uses one shared harness core and two clearly separated live tracks:

1. **Track A: Provider-native computer-use**
   - browser-first,
   - screenshot-driven,
   - paid API,
   - first backend: OpenAI `computer-use-preview`.
2. **Track B: Codex subscription-backed**
   - browser-first,
   - structured browser state first, screenshots optional later,
   - ChatGPT/Codex login on a trusted local machine,
   - action space constrained to semantic browser actions.

The plan stays intentionally simple by making several explicit choices now:

1. **Canonical artifact**
   - the source of truth is a normalized task package, not a recording.
2. **Storage**
   - file-based only: YAML tasks, JSON traces, PNG screenshots when needed, Markdown reports.
3. **Execution environment**
   - host macOS initially, with deterministic setup and cleanup scripts.
4. **Browser platform**
   - Playwright for all early tasks, because it gives us both screenshots and structured browser state.
5. **Baseline**
   - deterministic scripted baseline before any live model path.
6. **Live provider scope**
   - only one paid provider-native path in the initial build: OpenAI.
   - Anthropic is deferred unless the first comparison leaves important questions unanswered.

This is the lowest-risk path because it keeps the initial work focused on the actual unknowns rather than multiplying providers, environments, or abstractions.

### Longevity

This harness is designed to outlive the PoC. The five core objects — task, trial, trace, grader, report — are generic enough to support any computer-use eval scenario indefinitely. Growth happens by adding new adapters, tasks, observation modes, and grading strategies, not by rebuilding the core. As long as the shared harness boundary stays stable and the adapter contract stays clean, the system can absorb new providers, new environments, and new comparison dimensions without structural changes. The PoC is the first use, not the only use.

### 2. Current State

The repo currently contains:

1. `docs/research-consolidated.md`
2. `docs/init-idea.md`
3. `.plans/plan.md`

There is no implementation scaffold, no package config, no tests, no task corpus, and no CLI.

Implications:

1. The first milestone must establish the harness core and task artifact before any provider integration.
2. We should not start with desktop-native automation or recording ingestion.
3. The first useful result should be a readable run directory from one deterministic browser task.

### 3. Key Technical Findings

These findings materially affect architecture and are strong enough to bake into the plan now.

#### OpenAI computer-use

1. `computer-use-preview` is a specialized model for the computer-use tool.
2. It is only available through the Responses API.
3. That means it is an API-priced track, not a ChatGPT subscription-backed track.

Planning implication:

1. If we want true provider-native OpenAI computer-use evals, we must budget for API usage.
2. This track is still worth having because it is the cleanest test of screenshot-to-action harness behavior.

#### Codex auth and automation

1. Codex supports both API-key auth and ChatGPT-managed auth.
2. OpenAI documents API keys as the recommended default for automation.
3. ChatGPT-managed Codex auth for non-interactive use exists, but it is the advanced path and should be used only when we intentionally want account-backed execution or rate limits.
4. Codex caches auth locally in `~/.codex/auth.json` or the OS credential store, and that credential material must be treated as sensitive.

Planning implication:

1. A subscription-backed Codex eval track is possible.
2. It should be local-first and trusted-machine-only in the PoC.
3. It should not be the only live path, because it does not test the same thing as provider-native computer-use.

#### Browser observation

1. Playwright provides browser screenshots.
2. Playwright also provides structured browser state such as `ariaSnapshot()`.

Planning implication:

1. Browser-first is the right low-risk starting point.
2. It gives us a clean way to support both tracks from one environment:
   - Track A can consume screenshots,
   - Track B can start with structured browser state and semantic actions.

#### Overhead and orchestration

1. A larger eval framework is not required to learn the first important lessons.
2. We can add framework integration later if the shared harness core proves stable and useful.

Planning implication:

1. Do not make Inspect AI or another framework the backbone of M1-M3.
2. Keep framework integration optional and removable.

### 4. Design Principles

These principles should govern implementation decisions during build-out.

1. **One shared harness core, two separate live tracks**
   - shared task loading, trial execution, grading, trace storage, and reporting,
   - separate adapters, separate labels, separate interpretation.
2. **Task packages are the product of the harness, not recordings**
   - recordings are authoring evidence later, not runtime truth now.
3. **Outcome-first grading**
   - prefer programmatic verification over path checking or judge-based grading.
4. **Browser-first**
   - do not jump to native desktop until browser evals expose real gaps that desktop tasks are needed to answer.
5. **Semantic-first for the Codex track**
   - if the subscription-backed track starts requiring pixel-coordinate control, stop and reassess instead of silently growing a second computer-use stack.
6. **Stop complexity before it compounds**
   - if supporting both tracks starts slowing the shared core materially, prioritize the provider-native track and park the Codex track after the interface boundary is established.
7. **Adapter protocol must accommodate different shapes**
   - the three adapters have fundamentally different input/output contracts:
     - deterministic: receives task definition → returns Playwright selector actions (no observation needed),
     - OpenAI CU: receives screenshot bytes → returns batched pixel-coordinate actions,
     - Codex subscription: receives serialized browser state (ARIA, URL, etc.) → returns semantic locator actions.
   - the runner must not assume a single observation-action shape; instead, each adapter declares what observation it needs and what action format it returns,
   - the runner collects the requested observation, hands it to the adapter, and dispatches the returned actions through the appropriate executor (pixel or semantic),
   - keep this protocol minimal — a Python protocol class with `observation_request()` and `decide(observation) → actions` is enough; do not build an abstract base class hierarchy.

### 5. Files To Create

Keep the first implementation small.

**Core**

1. `README.md`
2. `pyproject.toml`
3. `.gitignore`
4. `src/harness/__init__.py`
5. `src/harness/types.py`
6. `src/harness/task_loader.py`
7. `src/harness/runner.py`
8. `src/harness/reporting.py`
9. `src/harness/failures.py`
10. `src/harness/cli.py`

**Environment and observations**

11. `src/harness/environments/browser.py`
12. `src/harness/observation.py`

**Adapters**

13. `src/harness/adapters/deterministic.py`
14. `src/harness/adapters/openai_cu.py`
15. `src/harness/adapters/codex_subscription.py`

**Grading**

16. `src/harness/graders.py`

**Tasks and configs**

17. `tasks/browser_download/task.yaml`
18. `tasks/browser_download/setup.py`
19. `tasks/browser_form_fill/task.yaml`
20. `tasks/browser_form_fill/setup.py`
21. `run_configs/openai_browser.yaml`
22. `run_configs/codex_browser.yaml`
23. `run_configs/deterministic.yaml`

**Tests and docs**

24. `tests/test_task_loader.py`
25. `tests/test_graders.py`
26. `tests/test_deterministic_smoke.py`
27. `docs/decisions.md`

**Later only if earned**

28. `src/harness/environments/macos.py`
29. `src/harness/capture.py`
30. `src/harness/intent_extract.py`
31. `scripts/author_task.py`
32. `evidence/.gitkeep`

### 6. Milestone Outline

- [x] M1: Shared Harness Core + Deterministic Baseline
  - [x] Step 1 — Bootstrap project (pyproject.toml, .gitignore, py.typed, src layout) → verify: `uv sync && uv run pytest --co -q`
  - [x] Step 2 — Define core types (types.py, failures.py) with Pydantic models + adapter Protocol → verify: `uv run mypy src/harness/types.py src/harness/failures.py`
  - [x] Step 3 — Implement task_loader.py + test_task_loader.py → verify: `uv run pytest tests/test_task_loader.py -v`
  - [x] Step 4 — Implement graders.py + test_graders.py, create task YAML + fixtures → verify: `uv run pytest tests/test_graders.py -v`
  - [x] Step 5 — Implement browser env, deterministic adapter, runner, reporting, CLI → verify: `uv run pytest tests/test_deterministic_smoke.py -v`
  Commit: "feat: M1 shared harness core with deterministic browser baseline"

**Goal**

Prove the task package, run directory, grading contract, and reporting loop with no live provider dependency.

**Exit criteria**

1. `python -m harness run tasks/browser_download/task.yaml --adapter deterministic` succeeds.
2. The run directory contains resolved task, action trace, grader output, summary report.
3. A failing deterministic run is clearly inspectable.
4. All six verification commands pass: uv sync, unit tests, smoke test, manual run, ruff, mypy.

- [x] M2: Track A — OpenAI Provider-Native Computer-Use
  - [x] Step 1 — Add openai dep to pyproject.toml, add metadata field to Trace → verify: `uv sync && uv run mypy src/harness/types.py`
  - [x] Step 2 — Implement OpenAI computer-use adapter (openai_cu.py) with Responses API → verify: `uv run pytest tests/test_openai_adapter.py -v`
  - [x] Step 3 — Register adapter in runner.py, add cost metadata extraction → verify: `uv run pytest tests/test_deterministic_smoke.py -v`
  - [x] Step 4 — Create browser-form-fill task (fixtures, setup, grader, deterministic script) → verify: `uv run pytest tests/test_deterministic_smoke.py::test_deterministic_browser_form_fill -v`
  - [x] Step 5 — Add comparison reporting + CLI compare command + tests → verify: `uv run pytest tests/test_comparison_report.py -v`
  Commit: "feat: OpenAI computer-use adapter, form-fill task, comparison reporting"

- [x] M3: Track B — Codex Subscription-Backed Browser Evals
  - [x] Step 1 — Spike: verify Codex CLI returns parseable JSON from ARIA state prompt → verify: manual inspection of CLI output
  - [x] Step 2 — Create codex_subscription adapter + register in runner → verify: `uv run mypy src/harness/adapters/codex_subscription.py`
  - [x] Step 3 — Write unit tests (mocked subprocess) → verify: `uv run pytest tests/test_codex_adapter.py -v`
  - [x] Step 4 — Create run_configs/codex_browser.yaml → verify: `uv run pytest -v`
  Commit: "feat: Codex subscription adapter with ARIA-state browser evals"

- [x] M4: Observation Refinement And Comparison
  - [x] Step 1 — Add hybrid=True flag to OpenAIComputerUseAdapter + register openai_cu_hybrid in runner → verify: `uv run mypy src/harness/adapters/openai_cu.py src/harness/runner.py`
  - [x] Step 2 — Write mocked tests for hybrid adapter behavior → verify: `uv run pytest tests/test_openai_adapter.py -v`
  - [x] Step 3 — Add detailed metrics functions + generate_detailed_report() to reporting.py → verify: `uv run mypy src/harness/reporting.py`
  - [x] Step 4 — Wire --detailed flag to CLI compare command → verify: `uv run mypy src/harness/cli.py`
  - [x] Step 5 — Write tests for detailed metrics computation → verify: `uv run pytest tests/test_detailed_report.py -v`
  Commit: "feat: hybrid OpenAI adapter variant and detailed comparison metrics"

- [x] M5: Native macOS Desktop Expansion
  - [x] Step 1 — Add pyobjc/pyautogui deps, SHELL ActionType, environment field to Task, Observation fields → verify: `uv sync && uv run mypy src/harness/types.py`
  - [x] Step 2 — Implement MacOSDesktopEnvironment (screenshots, AX tree, actions, permissions) → verify: `uv run mypy src/harness/environments/macos.py`
  - [x] Step 3 — Wire environment selection in runner.py based on task.environment → verify: `uv run mypy src/harness/runner.py`
  - [x] Step 4 — Add file_contains grader, TextEdit task YAML/setup, deterministic script → verify: `uv run pytest tests/test_graders.py -v`
  - [x] Step 5 — Write mocked tests for macOS environment, run full suite → verify: `uv run pytest -v`
  Commit: "feat: macOS desktop environment with TextEdit task"

#### M2: Track A — OpenAI Provider-Native Computer-Use

**Goal**

Add the first live computer-use path using OpenAI `computer-use-preview`.

**Scope**

1. Add the OpenAI adapter.
2. Add screenshot capture and any required resizing or coordinate translation logic.
3. Add hard `max_steps` and per-run cost accounting.
4. Add one additional browser task:
   - browser form fill.
5. Add comparison reporting for:
   - deterministic,
   - OpenAI computer-use.

**Default observation mode**

Start with screenshot-only for Track A.

Reason:

1. It is the simplest true test of the provider-native computer-use path.
2. It avoids introducing hybrid observation complexity before we have baseline signal.

**OpenAI-specific implementation notes**

These details affect the adapter and runner and should not be discovered mid-build:

1. **Action batching**: `computer-use-preview` returns an `actions[]` array — multiple actions per turn, not one. The runner must execute all actions in sequence before taking the next screenshot. Each action in the batch is logged as a sub-step in the trace so that failures within a batch are attributable.
2. **Server-side history**: OpenAI uses `previous_response_id` to manage conversation history server-side, unlike Anthropic which requires the full message history in each request. The adapter should store and forward this ID, not rebuild message history manually.
3. **Safety checks**: The API may return `pending_safety_checks` requiring explicit `acknowledged_safety_checks` in the next request. For unattended eval runs, the adapter must handle this programmatically — either by acknowledging automatically (with logging) or by terminating the trial with a `harness` failure category. Decide which behavior at implementation time; do not silently swallow the check.
4. **Screenshot resolution**: API image cap guidance should be verified against current OpenAI docs at implementation time. Coordinate scaling between Playwright viewport and the declared `display_width_px`/`display_height_px` is the harness's responsibility.

**Exit criteria**

1. OpenAI computer-use completes at least one real browser task end to end.
2. Reports show:
   - task success,
   - steps,
   - cost estimate,
   - failure category.
3. We can compare deterministic vs OpenAI on the same tasks.

#### M3: Track B — Codex Subscription-Backed Browser Evals

**Goal**

Add a second live track that uses ChatGPT/Codex-authenticated Codex as a constrained browser-state decision-maker.

**Important constraint**

This is not a provider-native computer-use benchmark. It is a lower-cash-cost, harness-shaped experiment family.

**Scope**

1. Add a Codex subscription adapter.
2. Require the adapter to operate on structured browser state first:
   - ARIA snapshot,
   - page URL and title,
   - focused element,
   - relevant task variables,
   - limited screenshot reference only if clearly supported and useful.
3. Constrain the action space to semantic browser actions such as:
   - click locator,
   - type into locator,
   - press key,
   - wait,
   - done,
   - fail.
4. Add separate run configs and separate report labels for the Codex track.

**Codex invocation mechanism — requires a spike**

The adapter must invoke Codex using the ChatGPT subscription, not API billing. The invocation mechanism is not yet determined. Known options:

1. Shell out to `codex` CLI per step with browser state serialized in the prompt. Simplest to implement, but latency per step may be high and output parsing may be fragile.
2. Use `codex` as an MCP server (`npx codex mcp-server`) with structured tool calls. Lower latency for multi-step sessions, but adds MCP protocol complexity.
3. Use the OpenAI API directly with a chat model and structured output schema. Cleanest interface, but this is API-billed, not subscription-backed — it would not be the Codex subscription track.

**Recommendation**: Spike option 1 first (CLI invocation) because it is the simplest path to validating whether the subscription-backed track produces useful signal. If CLI latency or output parsing becomes a real problem, migrate to option 2. Do not start with option 3 — it changes the billing model and defeats the purpose of the track.

**Subscription rate limit constraint**

ChatGPT Plus allows 45–225 messages per 5-hour window. If each eval step is one Codex invocation and a 15-step task consumes 15 messages, then 15 trials across 2 tasks = 450 messages — potentially exceeding the window on Plus. Mitigations:

1. Keep initial trial count low (3–5 per task) until message consumption is measured.
2. Log message count per run so we can see quota pressure.
3. If Plus quota is insufficient, document the finding and either reduce trial scope or note that Pro ($200/mo) is required for meaningful experiment volume.

**Critical stop rule**

If this track starts requiring bespoke screenshot-to-coordinate logic or begins to mirror a second computer-use stack, stop and keep it browser-semantic-only.

**Why this comes after M2**

1. The shared harness core must already exist.
2. We need the deterministic and provider-native paths first so we have something real to compare it against.
3. Otherwise the subscription-backed track can distort the architecture before we know whether it is informative.

**Exit criteria**

1. Codex subscription-backed runs can complete at least one browser task.
2. Reports clearly separate:
   - deterministic baseline,
   - OpenAI computer-use,
   - Codex subscription-backed.
3. We can answer whether the subscription-backed track is informative enough to keep.

#### M4: Observation Refinement And Comparison

**Goal**

Test whether additional state representation changes results enough to justify the extra complexity.

**Scope**

1. Add optional hybrid observation for Track A:
   - screenshot plus structured browser state.
2. Refine Track B prompts and state serialization.
3. Track:
   - step success rate,
   - failure taxonomy,
   - cost or quota metadata,
   - context growth,
   - semantic action ratio where applicable.

**Decision gate**

After M4, review:

1. Is the OpenAI track giving useful real computer-use signal?
2. Is the Codex subscription track giving useful lower-cost harness signal?
3. Does hybrid observation materially help enough to keep?

If one live track is clearly low value, stop investing in it.

#### M5: Native macOS Desktop Expansion Only If Earned

**Goal**

Expand beyond browser tasks only if browser evals fail to answer the next important questions.

**Scope**

1. Add a native macOS environment adapter.
2. Add AX and screenshot collection.
3. Add 1 to 2 desktop tasks only if needed.
4. Reassess whether VM isolation is necessary.

**Default assumption**

Desktop expansion should happen first for the provider-native track and deterministic baseline.

Reason:

1. That path is a more natural fit for screenshot-driven computer-use.
2. The Codex subscription track should not be forced into desktop-native scope unless browser results strongly justify it.

**Critical stop rule**

If desktop complexity becomes high before browser learnings are exhausted, pause desktop work and keep the PoC browser-first.

#### M6: Evidence Or Recording-Ingest Prototype

**Goal**

Connect the eventual recording vision back to the harness without turning the project into a product build.

**Scope**

1. Add an `evidence/` layout for screenshots, notes, transcript snippets, and optional metadata.
2. Add a manual or semi-assisted authoring flow that turns evidence into a task package.
3. Keep recordings as authoring evidence, not runtime truth.

**This milestone is conditional**

Only do this after the eval harness is already producing useful signal.

### 7. Testing Strategy

Keep testing cheap, layered, and outcome-focused.

| Layer | What | Runs in CI? |
|---|---|---|
| Schema validation | Task loading, versioning, variable substitution | Yes |
| Deterministic smoke | End-to-end browser run with no live model | Yes |
| Grader tests | Known inputs to expected grades | Yes |
| Golden outputs | Deterministic trace and report snapshots | Yes |
| Live integration | OpenAI and Codex adapters on real browser tasks | No |
| Manual eval protocol | Reset env, run N trials, inspect, tag failures, record notes | No |

Important rules:

1. Do not make flaky live browser or desktop runs mandatory CI gates.
2. CI protects harness correctness.
3. Manual or scheduled runs answer agent-performance questions.

### 8. Migration And Rollback

1. Version the task package from the start.
2. Keep run artifacts append-only under `runs/`.
3. Do not add a database in the initial build.
4. If an adapter proves weak, disable it by config rather than reshaping the task artifact.
5. Keep the deterministic path permanently available.
6. If later framework integration is added, keep it removable and outside the artifact boundary.

### 9. Manual Setup Tasks

These are required and should be documented explicitly.

1. Install Python and `uv`.
2. Install Playwright browser dependencies.
3. Prepare local test fixtures and local HTTP serving for task assets.
4. Grant macOS permissions as needed for later desktop work:
   - accessibility,
   - screen recording,
   - automation.
5. Log in to Codex with ChatGPT/Codex on the trusted local machine if the subscription-backed track is enabled.
6. Treat `~/.codex/auth.json` or equivalent stored credentials as sensitive and never commit or share them.
7. Decide where run artifacts live locally.
8. Retain all artifacts initially; optimize storage only if volume becomes a real problem.

### 10. Metrics

**Track-agnostic metrics**

1. task success rate,
2. steps to completion,
3. failure taxonomy distribution,
4. repeatability across trials.

**Track A metrics**

1. estimated API cost per run,
2. step success rate,
3. screenshot-driven failure patterns,
4. safety interruption rate if applicable.

**Track B metrics**

1. Codex quota or rate-limit pressure where visible,
2. semantic action success rate,
3. context growth from structured browser state,
4. divergence between Codex decisions and deterministic or provider-native paths.

### 11. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Over-engineering two live tracks at once | High | Keep one shared core, add Track B only after Track A and deterministic baseline exist |
| Conflating Codex subscription runs with true computer-use | High | Separate labels, separate configs, separate reporting, explicit documentation |
| API cost runaway in Track A | High | Hard `max_steps`, local fixtures, small task suite, per-run cost accounting |
| Subscription auth fragility in Track B | Medium | Trusted local-machine only, local-first workflow, keep deterministic and API paths intact |
| Codex subscription rate limits constrain Track B trial volume | Medium | Start with 3–5 trials per task, log message consumption, document if Plus quota is insufficient |
| Codex invocation mechanism is unvalidated | Medium | Spike CLI invocation early in M3 before committing adapter design; keep scope small enough to pivot |
| Task schema growing into a workflow DSL | Medium | Keep task package minimal and outcome-focused |
| Host-environment flakiness | Medium | Deterministic setup and cleanup scripts, browser-first, defer desktop |
| Desktop complexity arriving too early | Medium | Keep macOS-native scope behind an explicit gate after browser learnings |
| Weak graders | Medium | Prefer programmatic verification, keep judge-based grading optional |
| Recording ingestion distracting the project | Medium | Defer until the harness already teaches us something useful |

### 12. Open Questions

Only keep the questions that are still genuinely unresolved.

1. Is the available ChatGPT/Codex subscription quota sufficient for repeated local experiments, or will the subscription-backed track need tighter trial limits?
2. After OpenAI computer-use vs Codex subscription-backed comparisons, do we still need a second paid provider-native backend such as Anthropic?
3. If browser-only results are already highly informative, do we want to stop there for the first PoC instead of expanding to desktop?

## Recommended First Build Order

1. M1 shared harness core plus deterministic baseline.
2. M2 OpenAI provider-native computer-use on browser tasks.
3. M3 Codex subscription-backed browser evals.
4. M4 observation refinements and comparison.
5. M5 desktop expansion only if browser results leave important questions unanswered.
6. M6 evidence or recording authoring only if the harness is already useful.

## Build Discipline

Do not start with:

1. VM isolation,
2. hosted eval tooling,
3. native desktop automation,
4. recording ingestion,
5. multiple paid providers,
6. a second screenshot-to-coordinate stack for Codex.

Start with one shared harness core, one deterministic baseline, one paid provider-native browser track, and one separate subscription-backed browser-state track. That is the smallest plan that still answers the real question.
