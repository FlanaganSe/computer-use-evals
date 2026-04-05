# Desktop Agent Eval Harness

A macOS-first eval harness for desktop and browser agent workflows. Tests whether AI agents can reliably complete tasks like saving files, filling forms, and operating desktop apps — and helps understand *why* they fail.

The primary desktop execution path uses **structured accessibility state** (macOS AX trees) fed to a regular LLM, which returns semantic actions resolved against the accessibility tree. This approach achieves significantly higher task success rates and lower costs than screenshot-first methods (see `docs/decisions.md` ADR-001 and `.plans/research-findings.md`).

## Setup

```bash
# Install dependencies
uv sync

# Install Playwright browser (for browser tasks)
uv run playwright install chromium
```

## Usage

### Run the primary desktop adapter

```bash
# Structured-state desktop (baseline — single strong model)
uv run python -m harness run tasks/desktop_textedit_save/task.yaml \
  --adapter structured_state_desktop

# Structured-state desktop with cheap-first routing
uv run python -m harness run tasks/desktop_textedit_save/task.yaml \
  --adapter structured_state_desktop_routed
```

### Run a browser task with the deterministic baseline

```bash
uv run python -m harness run tasks/browser_download/task.yaml --adapter deterministic
```

### Compare runs across adapters

```bash
uv run python -m harness compare --runs-dir runs
uv run python -m harness compare --runs-dir runs --detailed
```

### Run configs

Pre-built configurations live in `run_configs/`. Key configs:

| Config | Purpose |
|---|---|
| `structured_state_desktop.yaml` | Primary desktop path (baseline structured-state) |
| `structured_state_desktop_routed.yaml` | Primary desktop path with cheap-first routing |
| `desktop_comparison.yaml` | Cross-adapter desktop comparison (structured-state vs legacy screenshot) |
| `deterministic.yaml` | Permanent browser baseline |
| `openai_browser.yaml` | Legacy screenshot adapter — browser comparison lane |
| `openai_hybrid.yaml` | Legacy screenshot+ARIA hybrid — browser comparison lane |

### Inspect results

Each run creates a timestamped directory under `runs/`:

```
runs/<timestamp>_<task>_<adapter>/
  task.yaml      # Resolved task definition
  config.json    # Run config
  trace.json     # Step-by-step action trace
  grade.json     # Pass/fail with explanation
  report.md      # Human-readable summary
  evidence.json  # Decision-point evidence (structured-state adapter)
  artifacts/     # Downloaded files, etc.
  screenshots/   # Screenshots (if applicable)
```

## Architecture

The harness is built around five core objects:

- **Task** — normalized task definition (YAML), optionally with milestones
- **Trial** — one isolated execution attempt
- **Trace** — per-step action and result record with decision evidence
- **Grader** — scoring logic (outcome-first, with milestone-aware verification)
- **Report** — human-readable summary with comparison and routing metrics

Adapters plug into the harness via a `Protocol` with two methods:

- `observation_request()` — what the adapter needs from the environment
- `decide(observation, task)` — returns actions to execute

### Adapter landscape

| Adapter | Role | Observation | Actions |
|---|---|---|---|
| **structured_state_desktop** | Primary desktop path | AX tree (accessibility state) | Semantic (AX node targets + coordinate fallback) |
| **structured_state_desktop_routed** | Primary desktop path with cheap-first routing | AX tree | Semantic |
| deterministic | Permanent baseline | None | Selector-based |
| openai_cu | Legacy comparison lane (screenshot-first) | Screenshot | Pixel-coordinate |
| openai_cu_hybrid | Legacy comparison lane (screenshot+ARIA) | Screenshot + ARIA | Pixel-coordinate |
| codex_subscription | Legacy comparison lane (browser-only) | ARIA state | Semantic locator |

The structured-state desktop adapter reads macOS AX trees, prunes to interactive elements, sends structured state to an LLM, and resolves returned semantic targets to coordinates via the accessibility tree. The routed variant uses a cheap model for well-grounded decisions and escalates to a stronger model for ambiguous or post-failure steps.

Legacy screenshot-first adapters (`openai_cu`, `openai_cu_hybrid`) are retained as comparison lanes for benchmarking against the structured-state path. They are not the direction of investment.

## Testing

```bash
# Unit tests (fast, no browser)
uv run pytest tests/test_task_loader.py tests/test_graders.py -v

# Structured-state adapter tests
uv run pytest tests/test_structured_state_desktop.py -v

# End-to-end smoke test (launches a real browser)
uv run pytest tests/test_deterministic_smoke.py -v

# All tests
uv run pytest -v

# Linting and type checks
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
```
