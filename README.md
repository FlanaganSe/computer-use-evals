# Desktop Agent Eval Harness

A macOS-first eval harness for desktop and browser agent workflows. Tests whether AI agents can reliably complete tasks like downloading files, filling forms, and operating desktop apps — and helps understand *why* they fail.

## Setup

```bash
# Install dependencies
uv sync

# Install Playwright browser
uv run playwright install chromium
```

## Usage

### Run the deterministic baseline

```bash
uv run python -m harness run tasks/browser_download/task.yaml --adapter deterministic
```

### Inspect results

Each run creates a timestamped directory under `runs/`:

```
runs/<timestamp>_<task>_<adapter>/
  task.yaml      # Resolved task definition
  config.json    # Run config
  trace.json     # Step-by-step action trace
  grade.json     # Pass/fail with explanation
  report.md      # Human-readable summary
  artifacts/     # Downloaded files, etc.
  screenshots/   # Screenshots (empty for deterministic)
```

## Testing

```bash
# Unit tests (fast, no browser)
uv run pytest tests/test_task_loader.py tests/test_graders.py -v

# End-to-end smoke test (launches a real browser)
uv run pytest tests/test_deterministic_smoke.py -v

# All tests
uv run pytest -v

# Linting and type checks
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
```

## Architecture

The harness is built around five core objects:

- **Task** — normalized task definition (YAML)
- **Trial** — one isolated execution attempt
- **Trace** — per-step action and result record
- **Grader** — scoring logic (outcome-first)
- **Report** — human-readable summary

Adapters plug into the harness via a `Protocol` with two methods:

- `observation_request()` — what the adapter needs from the environment
- `decide(observation, task)` — returns actions to execute

This protocol accommodates adapters with fundamentally different shapes:

| Adapter | Observation | Actions |
|---|---|---|
| Deterministic (M1) | None | Selector-based |
| OpenAI CU (M2) | Screenshot | Pixel-coordinate |
| Codex (M3) | ARIA state | Semantic locator |


Hello