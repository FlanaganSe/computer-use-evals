# M1 Handoff: Shared Harness Core + Deterministic Baseline

## How to use this prompt

This is a complete handoff for implementing Milestone 1 of the Desktop Agent Eval Harness. Read it fully before writing any code. The goal is not just to produce files — it is to build a foundation you understand well enough to explain every decision in.

---

## What this project is

We are building a macOS-first eval harness for desktop and browser agent workflows. The harness tests whether AI agents can reliably complete tasks like downloading files, filling forms, and operating desktop apps — and, critically, helps us understand *why* they fail when they don't.

This is a proof-of-concept learning system, not a product. It exists to reduce unknown-unknowns about observation formats, grounding quality, execution reliability, failure modes, and cost. The harness is designed to outlive the PoC — the five core objects (task, trial, trace, grader, report) are generic enough for any computer-use eval scenario. Growth is additive (new adapters, tasks, observation modes), not structural.

The full plan is at `.claude/plans/plan.md`. The consolidated research is at `docs/research-consolidated.md`. Validated technical research on APIs and tools is at `.claude/plans/research.md`. Read all three before starting.

---

## What M1 delivers

M1 builds the shared harness core and proves it works end-to-end with a deterministic browser baseline — no paid API, no model dependency. Everything after M1 (OpenAI computer-use in M2, Codex subscription track in M3, observation refinements in M4) plugs into this core.

**The exit criteria are specific:**

1. `python -m harness run tasks/browser_download/task.yaml --adapter deterministic` succeeds.
2. The run produces a timestamped directory under `runs/` containing:
   - resolved task input (the YAML with variables substituted),
   - action trace (JSON, one entry per step with action taken and result),
   - grader output (pass/fail with explanation),
   - summary report (human-readable).
3. A *failing* deterministic run is clearly inspectable — the trace shows what went wrong and where.

If you can demonstrate all three, M1 is done.

---

## Before you write code: reflect

Pause and answer these questions for yourself before implementation:

1. **Do you understand the adapter protocol?** Three future adapters have fundamentally different shapes:
   - Deterministic (M1): receives the task definition → returns Playwright selector-based actions. No observation needed from the environment.
   - OpenAI CU (M2): receives screenshot bytes → returns batched pixel-coordinate actions.
   - Codex subscription (M3): receives serialized ARIA state → returns semantic locator actions.

   The runner must not assume a single observation-action shape. Each adapter declares what observation it needs and what action format it returns. The runner collects the requested observation, hands it to the adapter, and dispatches the returned actions. Design this as a minimal Python `Protocol` class — `observation_request()` and `decide(observation) -> list[Action]` is sufficient. Do not build an abstract class hierarchy.

   **Think about this carefully before writing `types.py` and `runner.py`.** The types you define now must accommodate all three shapes without being rewritten. The deterministic adapter is the simplest case, but the protocol must not be so tightly fit to it that M2 or M3 requires reshaping the core.

2. **Do you understand the task YAML schema?** Keep it minimal and outcome-focused. The plan's research section (§4 of the consolidated research) shows an example. The first version needs:
   - `task_id`, `version`
   - `goal.description` and `goal.variables` (with defaults)
   - `preconditions` (human-readable) and `setup_script` (path to a Python script)
   - `verification.primary` (programmatic check, e.g., `file_exists`)
   - optional `verification.fallback` (for future LLM judge, not implemented in M1)
   - `cleanup_script` (path to cleanup, if needed)

   Do not add fields speculatively. If M2 needs a field, M2 adds it.

3. **Do you understand what the run directory layout is for?** Every run must be inspectable after the fact by a human who wasn't watching. The directory is the primary debugging surface for the entire project. A reader should be able to open the run directory, read the trace, and understand what the harness did, what the adapter decided, what happened, and whether the grader was correct. Design it with that reader in mind.

4. **Do you understand what the deterministic adapter actually does?** It is a scripted Playwright automation. It reads the task definition and executes a hardcoded sequence of Playwright actions (goto, click, waitForDownload, etc.) that complete the task. It does not look at screenshots. It does not call a model. It proves the harness plumbing works: task loading → setup → execution → grading → trace writing → reporting. It also serves as a permanent baseline for comparison with live model adapters in later milestones.

5. **Do you understand Playwright well enough?** You will use Playwright for Python (`playwright` package) to launch a Chromium browser, navigate to a URL, interact with elements, and handle downloads. Check the current Playwright Python docs for:
   - Browser launch and context creation
   - Download handling (`page.expect_download()`)
   - How to set a fixed viewport size
   - How `ariaSnapshot()` works (you won't use it in M1, but the browser environment should be designed knowing it exists for M2+)

   If uncertain about any Playwright API, look it up. Do not guess.

---

## Files to create in M1

Only create what M1 needs. Files for M2+ are listed in the plan but should NOT be created now.

```
pyproject.toml                            # uv-managed project
.gitignore                                # runs/, .env, __pycache__, *.pyc, .venv, etc.
README.md                                 # Setup + usage instructions for M1

src/harness/__init__.py                   # Package init (version, minimal exports)
src/harness/types.py                      # Pydantic models: Task, Trial, Step, GraderResult, Report, Action types, adapter Protocol
src/harness/task_loader.py                # Load YAML, validate schema, substitute variables
src/harness/runner.py                     # Core run loop: setup → adapter loop → grade → write artifacts
src/harness/reporting.py                  # Generate summary report from trial results
src/harness/failures.py                   # Failure taxonomy enum and classification helpers
src/harness/graders.py                    # Programmatic graders (file_exists, etc.)
src/harness/cli.py                        # CLI entry point (__main__.py wiring)
src/harness/__main__.py                   # python -m harness support

src/harness/environments/__init__.py
src/harness/environments/browser.py       # Playwright browser: launch, screenshot, execute actions, download handling, cleanup

src/harness/adapters/__init__.py
src/harness/adapters/deterministic.py     # Hardcoded Playwright selector actions for each task

tasks/browser_download/task.yaml          # First task definition
tasks/browser_download/setup.py           # Setup: start local HTTP server, prepare fixtures
tasks/browser_download/fixtures/test.pdf  # A small test PDF to serve

run_configs/deterministic.yaml            # Run config: adapter=deterministic, tasks, trial count

tests/test_task_loader.py                 # Unit tests: YAML loading, validation, variable substitution
tests/test_graders.py                     # Unit tests: grader functions with known inputs
tests/test_deterministic_smoke.py         # End-to-end: run deterministic baseline, check artifacts exist and structure is correct
```

---

## Technical decisions already made

These are not open for reconsideration in M1. They are explained and justified in the plan.

1. **Python with `uv`** for package management. Use `uv init` or set up `pyproject.toml` manually with a `[build-system]` and `[project]` section.
2. **Pydantic v2** for all data models. Type safety, validation, serialization.
3. **Playwright for Python** (`playwright`) for browser automation.
4. **File-based storage only.** YAML tasks, JSON traces, Markdown reports. No database.
5. **Local fixtures only.** The first task uses a local HTTP server (Python `http.server` or similar) to serve a test PDF. No external URLs.
6. **Outcome-first grading.** Check whether the expected file exists, not whether the agent clicked the right buttons. (For the deterministic baseline, the path is fixed, but the grader should still check outcome, not path.)
7. **Version the task schema from the start.** Include `version: "1.0"` in every task YAML.
8. **Run artifacts are append-only under `runs/`.** Each run gets a timestamped directory. Never overwrite a previous run.

---

## Run directory layout

When `python -m harness run tasks/browser_download/task.yaml --adapter deterministic` completes, it should produce a directory like:

```
runs/
  2026-04-04T15-30-00_browser-download_deterministic/
    task.yaml              # Resolved task (variables substituted)
    config.json            # Run config used (adapter, max_steps, etc.)
    trace.json             # Step-by-step action trace
    grade.json             # Grader result (pass/fail, method, explanation)
    report.md              # Human-readable summary
    artifacts/             # Any files produced (e.g., downloaded PDF)
    screenshots/           # Screenshots if captured (empty for deterministic in M1)
```

The `trace.json` structure should be something like:

```json
{
  "task_id": "browser-download",
  "adapter": "deterministic",
  "started_at": "2026-04-04T15:30:00Z",
  "completed_at": "2026-04-04T15:30:05Z",
  "steps": [
    {
      "step": 1,
      "action": { "type": "goto", "url": "http://localhost:8765/test.pdf" },
      "result": "ok",
      "timestamp": "..."
    },
    {
      "step": 2,
      "action": { "type": "click", "selector": "..." },
      "result": "ok",
      "timestamp": "..."
    }
  ],
  "outcome": "pass",
  "failure_category": null,
  "total_steps": 2
}
```

This is illustrative, not prescriptive. Design the schema to be clear, complete, and extensible for M2's needs (which will add `observation` per step, `cost` metadata, and `sub_steps` for batched actions).

---

## Quality expectations

**Type safety:**
- All data models use Pydantic v2 with strict typing.
- Add a `py.typed` marker.
- Use `mypy` or `pyright` for static type checking. Configure in `pyproject.toml`.
- The adapter protocol should use `typing.Protocol` so adapters are structurally typed, not inheritance-based.

**Testing:**
- Use `pytest`.
- `test_task_loader.py`: test YAML loading, validation errors for bad schemas, variable substitution.
- `test_graders.py`: test grader functions with known inputs (file exists / doesn't exist).
- `test_deterministic_smoke.py`: run the full M1 flow end-to-end, assert the run directory is created with expected structure. This test proves the harness plumbing works. It should be runnable without any API keys.
- Tests should be fast. The smoke test launches a real browser, so mark it appropriately (e.g., `@pytest.mark.slow` or similar) but it should still complete in under 30 seconds.

**Linting:**
- Use `ruff` for linting and formatting. Add a `[tool.ruff]` section to `pyproject.toml`.
- Target Python 3.12+.

**Code style:**
- Prefer immutable patterns and functional composition where natural.
- Keep modules small and focused. If a file is growing past ~200 lines, consider whether it's doing too much.
- Use clear names. The reader should understand the harness by reading the code, not by reading external documentation.

---

## What NOT to do

These are explicit guardrails. Violating them means the milestone is off-track.

1. **Do not create files for M2+.** No `openai_cu.py`, no `codex_subscription.py`, no `observation.py`. Those come later.
2. **Do not add model/API dependencies.** M1 has zero paid API calls. No `openai`, no `anthropic` in dependencies.
3. **Do not build a workflow DSL.** The task YAML is a data definition, not a programming language.
4. **Do not add an abstract base class hierarchy for adapters.** Use a `Protocol`. Two methods. That's it.
5. **Do not add screenshot capture in the deterministic adapter.** The deterministic path does not observe — it executes a known script. Screenshot support in the browser environment can exist as a capability, but the deterministic adapter does not use it.
6. **Do not add a database, message queue, or hosted service dependency.**
7. **Do not use an external eval framework** (Inspect AI, DeepEval, etc.). The harness is its own runner.
8. **Do not add Anthropic or OpenAI provider integrations.** Those are M2 and M3.
9. **Do not connect to external URLs for test fixtures.** Everything must work offline with local fixtures.
10. **Do not add features that aren't needed to meet the exit criteria.** No docstring-heavy boilerplate, no CLI help pages beyond what's functional, no configuration options that aren't used.

---

## Implementation order (suggested)

This order minimizes the risk of discovering a design problem late:

1. **`pyproject.toml` + `.gitignore` + project bootstrap** — get `uv sync` and `pytest` working with an empty test.
2. **`types.py`** — define all Pydantic models and the adapter Protocol. This is the most important file. Spend time here. Think about how M2's OpenAI adapter (screenshot in, batched pixel actions out) and M3's Codex adapter (ARIA state in, semantic actions out) will use the same Protocol. The deterministic adapter is the simplest case — make sure the Protocol isn't accidentally shaped to only fit it.
3. **`failures.py`** — define the failure taxonomy enum. Small file, but used by types and graders.
4. **`task_loader.py`** — load and validate task YAML, substitute variables. Write `test_task_loader.py` immediately after.
5. **`graders.py`** — implement `file_exists` grader. Write `test_graders.py` immediately after.
6. **`tasks/browser_download/task.yaml` + fixtures** — define the first real task. This forces you to validate your schema against a real use case.
7. **`environments/browser.py`** — Playwright browser lifecycle. Launch, navigate, execute actions, handle downloads, cleanup. The browser environment must support being asked for screenshots and ARIA state even if the deterministic adapter doesn't use them — M2 will.
8. **`adapters/deterministic.py`** — hardcoded Playwright steps for the download task.
9. **`runner.py`** — the core loop. Ties together: load task → run setup script → initialize adapter → loop (get observation request → collect observation → adapter decides → execute actions → record step) → grade → write artifacts. For the deterministic adapter, the "observation" step is a no-op, but the loop structure should accommodate it.
10. **`reporting.py`** — generate summary report from trial result.
11. **`cli.py` + `__main__.py`** — wire up the CLI entry point.
12. **`run_configs/deterministic.yaml`** — run config for the deterministic baseline.
13. **`test_deterministic_smoke.py`** — end-to-end test of the full flow.
14. **`README.md`** — setup and usage instructions.

At each step, run your tests. Do not proceed to the next step if the current one is broken.

---

## After implementation: how the user tests this

Include these instructions in the README, and mention them when you finish:

```bash
# 1. Install dependencies
uv sync
uv run playwright install chromium

# 2. Run the unit tests (fast, no browser needed)
uv run pytest tests/test_task_loader.py tests/test_graders.py -v

# 3. Run the full smoke test (launches a real browser)
uv run pytest tests/test_deterministic_smoke.py -v

# 4. Run the harness manually and inspect the output
uv run python -m harness run tasks/browser_download/task.yaml --adapter deterministic

# 5. Inspect the run directory
ls runs/
# Open the most recent run directory and read:
#   - trace.json   (what happened step by step)
#   - grade.json   (did it pass, why)
#   - report.md    (human-readable summary)

# 6. Run linting and type checks
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
```

The user should be able to run all six of these commands successfully after M1 is complete. If any fail, M1 is not done.

---

## Finishing up

When all exit criteria are met, all tests pass, linting is clean, and type checks pass:

1. Run `uv run ruff format src/ tests/` to ensure formatting is consistent.
2. Run the full test suite one more time: `uv run pytest -v`.
3. Stage the relevant files and create a single commit. Keep the message to one line. Do not include "Co-Authored-By" lines.
4. Tell the user what you built, what the key design decisions were, and provide the six testing commands above so they can verify the milestone themselves.
5. Note any decisions you made that weren't prescribed — especially around the adapter Protocol shape, the trace format, or the task schema. The user needs to understand these before M2 begins.

---

## A final note on intent

This harness exists to help us understand a problem space, not to ship a product. Every design choice should optimize for *legibility* — can a person who reads the code, the traces, and the reports understand what happened and why? If you find yourself adding complexity that makes the system harder to understand, stop and simplify. The right answer is almost always the simpler one.
