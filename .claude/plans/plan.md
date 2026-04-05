# Implementation Plan: Accessibility-First Eval Harness Rewrite

## Contract

### Problem

The eval harness tests screenshot-based pixel-coordinate agents when the evidence (DMI: +67% success, -43.5% steps; GUIDE: +50pp intent accuracy with structured context) strongly favors accessibility-first structured-state agents. The harness already captures macOS AX trees (`macos.py:263-305`) but no adapter reads them for decision-making. Additionally, the `llm_judge` grader is stubbed out (`graders.py:27-31`), blocking the `my-test-task`, and there are no milestones for diagnosing partial progress in multi-step tasks.

### Requirements

| # | Requirement | Priority |
|---|---|---|
| R1 | New `StructuredStateDesktopAdapter` reads pruned AX tree, sends to LLM, returns semantic actions resolved to coordinates | P0 |
| R2 | AX JSON serializer with stable node IDs, bounding boxes, and interactive-element filtering alongside existing text serializer | P0 |
| R3 | Semantic target resolution in `MacOSDesktopEnvironment` — resolve `target: "ax_001"` to pixel coordinates via AX tree | P0 |
| R4 | `llm_judge` grader implementation — unblocks `my-test-task` and any task not verifiable by filesystem checks | P0 |
| R5 | Task-schema validation — reject/warn on unsupported grader expressions at load time | P1 |
| R6 | `Milestone` model and optional `milestones` field on `Task` — backward compatible | P1 |
| R7 | Milestone verifier in runner loop — check milestones between steps, persist results | P1 |
| R8 | Decision-point evidence persistence — save pruned AX state, focused app, milestone state, chosen action per step | P1 |
| R9 | AX snapshots in capture pipeline alongside screenshots | P2 |
| R10 | Model routing heuristic (cheap/frontier split based on AX tree richness) | P2 |
| R11 | Trigger remaining failure categories (PERCEPTION, CONTEXT, ENVIRONMENT, TOOL_CHOICE) where evidence supports them | P2 |

### Acceptance Criteria

**AC1 — Structured-state execution:**
Given `desktop_textedit_save` task, when `harness run tasks/desktop_textedit_save/task.yaml --adapter structured_state`, then the adapter reads AX tree, sends pruned state to LLM, executes semantic actions, and per-step evidence is saved to the run directory.

**AC2 — Backward compatibility:**
Given existing tasks and adapters, when run with `--adapter openai_cu` or `--adapter deterministic`, then behavior is identical to today.

**AC3 — Comparison reporting:**
Given runs from both `structured_state` and `openai_cu` adapters, when `harness compare`, then the report shows side-by-side success rate, step count, cost, and failure categories.

**AC4 — LLM judge:**
Given `my-test-task` (converted to `llm_judge`), when graded, then the judge produces a pass/fail verdict with explanation (not "not implemented").

**AC5 — Milestone diagnostics:**
Given a task with milestones, when the run fails, then the trace shows which milestone was last achieved.

**AC6 — Task validation:**
Given a task YAML with unsupported grader expressions, when loaded, then a warning is logged or a clear error is raised before execution.

### Non-Goals

- Deterministic skill executor / replay compiler
- Escalation policy framework
- Continuous video capture (ScreenCaptureKit)
- Branch synthesis / recovery paths in tasks
- Cross-platform environments (Windows, Linux)
- Trained difficulty classifier for model routing
- CUA-Skill composition DAGs
- MCP server for AX trees
- Full multi-agent verifier framework
- Redesign of `Action`/`Observation` models globally
- Replacing the existing text AX serializer

### Constraints

1. **Additive over destructive.** New adapter, new serializer, new task field — don't restructure working code. Keep `openai_cu` and `deterministic` as baselines.
2. **One model to start.** Claude Sonnet 4.6 via `anthropic` SDK. No routing until the basic path works.
3. **Semantic targeting stays in `Action.params`.** Don't split the action schema until it proves painful.
4. **Existing text AX serializer preserved.** JSON serializer is a parallel addition.
5. **Existing v1 task YAMLs must keep working.** `milestones` field is optional with empty list default.
6. **`anthropic` SDK must be added to `pyproject.toml` dependencies.**

---

## Implementation Plan

### Summary

Build a new `StructuredStateDesktopAdapter` that reads pruned AX trees (via a new JSON serializer with stable node IDs), formats them as structured prompts for Claude Sonnet 4.6, parses semantic action responses, and resolves AX node targets to screen coordinates. This is additive — the existing adapters, protocols, environments, and task formats remain unchanged. The adapter slots into the existing `Adapter` Protocol without modifications. Milestones and `llm_judge` are separate but complementary additions that improve task expressiveness and diagnostic quality.

The core architectural decision: **put structured-state intelligence in the adapter, coordinate resolution in the environment, and keep the protocol layer untouched.** This matches how the codebase already separates concerns (adapter decides, environment executes) and avoids cascading changes.

### Current State

**Adapter → Environment flow (`runner.py:111-168`):** The runner calls `adapter.observation_request()` → `env.collect_observation()` → `adapter.decide()` → `env.execute_action()` in a loop. This loop doesn't need structural changes.

**AX tree capture (`macos.py:263-305`):** `_serialize_ax_element()` produces indented text with role, title, value, description. No node IDs, no bounding boxes, no filtering. `_get_ax_tree()` takes a PID and returns the text. `collect_observation()` already routes `ARIA_STATE` requests to `_get_ax_tree()` and puts the result in `Observation.aria_snapshot: str | None`.

**Grading (`graders.py:24-41`):** Non-programmatic methods return "not implemented." Only `file_exists`, `file_contains`, `form_submitted` are supported. `my-test-task` uses `app_opened('TextEdit') and not form_submitted(...)` — a boolean expression over unsupported functions.

**Adapter registration (`runner.py:36-41`):** Dict mapping name to factory. Adding a new adapter is one line.

**Dependencies (`pyproject.toml:9-19`):** Has `openai>=1.0`, `pyobjc`, `pyautogui`. Missing `anthropic`.

### Files to Change

| File | Changes | Why |
|---|---|---|
| `src/harness/environments/macos.py` | Add `_serialize_ax_element_json()` (~80 lines), `_get_ax_tree_json()`, `_resolve_ax_target()` (~40 lines). Modify `execute_action()` to check for `target` in params and resolve to coordinates. Cache last AX tree for target resolution. | R2, R3 — JSON serializer with stable IDs and semantic target resolution |
| `src/harness/types.py` | Add `Milestone` model (~5 lines), add `milestones: list[Milestone] = []` to `Task` (~1 line). Add `ax_contains` to `VerificationCheck.method` literal. | R6 — Milestone support in task model |
| `src/harness/graders.py` | Implement `llm_judge` (~50 lines) using OpenAI API (already a dependency). Add `_check_app_focused()` helper. Add `_eval_bool_expr()` or convert `my-test-task` to use `llm_judge`. | R4 — LLM judge implementation |
| `src/harness/task_loader.py` | Add validation that grader check expressions reference only supported functions. Log warning for unknown functions. | R5 — Task-schema validation |
| `src/harness/runner.py` | Add `structured_state` to `ADAPTERS` dict. Add milestone checking between steps (~30 lines). Add evidence persistence per step (~20 lines). | R1 (registration), R7, R8 |
| `src/harness/capture.py` | In `capture_session()`, always call `_capture_focused_app_aria()` and save to `ax_tree/` dir alongside screenshots. | R9 — AX snapshots in capture |
| `src/harness/reporting.py` | Add milestone pass/fail data to single-run report and comparison tables. | R7 (reporting support) |
| `pyproject.toml` | Add `anthropic>=0.40` to dependencies. | Adapter dependency |
| `tasks/my-test-task/task.yaml` | Convert verification from boolean expression to `method: llm_judge` with a clear prompt. | R4 — Unblock broken task |

### Files to Create

| File | Purpose | Pattern follows |
|---|---|---|
| `src/harness/adapters/structured_state.py` (~300 lines) | New primary adapter. AX tree pruning, structured prompt formatting, LLM call via Anthropic SDK, semantic action parsing, response validation with retry. | `adapters/openai_cu.py` — same self-contained pattern with own API client, token tracking, cost metadata |
| `tests/test_structured_state.py` (~200 lines) | Tests for AX pruning, prompt formatting, semantic action parsing, target resolution, coordinate validation | `tests/test_macos_env.py` — mock-based, same fixture patterns |
| `tests/test_milestones.py` (~100 lines) | Tests for milestone model, verifier, task loading with milestones | `tests/test_graders.py` — same `_make_task()` pattern |
| `scripts/measure_ax_coverage.py` (~50 lines) | Quick script to measure AX tree coverage for target apps (TextEdit, Chrome, Finder). Run before building full adapter. Not part of the package — standalone diagnostic. | New, standalone |

### Milestone Outline

#### Phase 1: Core Hypothesis

- [ ] M1: AX JSON serializer — Parallel JSON serializer with stable node IDs, bounding boxes, interactive filtering, and pruning in `macos.py`. Includes timing measurement for AX capture latency. Tests for pruning logic and node ID stability.

- [ ] M2: AX coverage measurement — Run `scripts/measure_ax_coverage.py` against TextEdit, Chrome, Finder. **Exit gate:** ≥3 of 3 apps must have ≥5 interactive elements with task-critical controls present. If not, stop and reassess.

- [ ] M3: Structured-state adapter — `StructuredStateDesktopAdapter` with prompt formatting, Anthropic API call, semantic action parsing with retry/fallback, and cost tracking. Semantic target resolution in `MacOSDesktopEnvironment`. Register in runner. Tests for prompt construction, response parsing, target resolution, Retina coordinate validation.

- [ ] M4: LLM judge + task fixes — Implement `llm_judge` grader. Add task-schema validation warnings. Convert `my-test-task` to `llm_judge`. Tests for judge grading path.

- [ ] M5: Phase 1 exit gate — Run `desktop_textedit_save` with both `structured_state` and `openai_cu`. Compare results. Run Experiment 1 (AC1, AC2, AC3). **Decision point:** If `structured_state` fails where `openai_cu` succeeds on ≥2 tasks, investigate whether it's AX coverage (add vision fallback) or fundamental (abort AX-first approach).

#### Phase 2: Diagnostics & Optimization

- [ ] M6: Milestones — Add `Milestone` model to types. Add milestone verifier to runner. Add milestone data to reports. Upgrade `desktop_textedit_save` to v2 with milestones. Tests for milestone verification.

- [ ] M7: Evidence + failure categories — Persist decision-point evidence per step (pruned AX state, focused app, milestone state, action, result) to run directory. Trigger PERCEPTION, CONTEXT, ENVIRONMENT, TOOL_CHOICE failure categories where the adapter/runner has evidence to support them.

- [ ] M8: Capture + routing — Add AX snapshots to capture pipeline. Add routing heuristic (cheap model for AX-rich steps, frontier for sparse/ambiguous). Run Experiments 2 and 4.

### Testing Strategy

**M1:** Unit tests for `_serialize_ax_element_json()` — mock AX elements, verify stable node IDs across calls, verify pruning rules (only interactive elements), verify bounding box extraction, verify max-element cap. Test AX capture timing (log if >500ms).

**M3:** Unit tests for `StructuredStateDesktopAdapter` — mock Anthropic API responses, verify prompt includes pruned elements/task/history, verify semantic action parsing for all action types, verify malformed response handling (retry, fallback to FAIL). Integration test for `_resolve_ax_target()` — verify AX node ID maps to correct center coordinates. Retina coordinate test — verify AX points match pyautogui coordinate space.

**M4:** Unit tests for `llm_judge` — mock OpenAI API, verify pass/fail based on model response. Test that `my-test-task` loads and grades without error. Test task-schema validation warns on `app_opened()`.

**M6:** Unit tests for `Milestone` model validation. Test that v1 tasks (no milestones) still load. Test milestone verifier with programmatic checks. Test milestone data appears in report output.

**M7:** Test that step evidence files are written to run directory. Test that evidence is sufficient to explain a mock failure.

**All tests:** Follow existing patterns — `pytest`, mock-based for system calls, `tmp_path` for filesystem tests. Mark desktop-dependent tests with `@pytest.mark.desktop` (new marker, added to `pyproject.toml`). These need real macOS + Accessibility permissions and can't run in CI.

### Manual Setup Tasks

| Task | Depends on |
|---|---|
| Set `ANTHROPIC_API_KEY` environment variable | M3 (adapter needs it) |
| Ensure Accessibility permission granted for Python/terminal | M1 (AX tree capture), M2 (coverage measurement) |
| Ensure Screen Recording permission granted | M5 (end-to-end runs with screenshots for comparison) |

### Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **AX trees too sparse on target apps** | Medium | High — invalidates approach | M2 measures this before building full adapter. If <3 apps have usable trees, pivot to hybrid (AX + vision) from start. |
| **AX capture latency >1s** | Low | Medium — slows per-step loop | M1 includes timing measurement. If slow, consider caching tree across observe+resolve in same step. |
| **Retina coordinate mismatch** | Medium | High — every click misses silently | M3 includes explicit coordinate validation test. pyautogui on macOS uses points (same as AX), but verify empirically. |
| **LLM returns malformed JSON** | High | Medium — step fails | Adapter includes retry (up to 2 retries) with simplified prompt. Fallback to `ActionType.FAIL` with parse error. |
| **AX tree changes after action execution** | Medium | Low — stale tree used for next step | Each step re-observes. The 0.5s `_ACTION_SETTLE_DELAY` in `macos.py:21` provides settling time. If insufficient, increase for structured_state adapter. |
| **`anthropic` SDK version incompatibility** | Low | Low — fixable | Pin to `>=0.40` which covers current API. |
| **`my-test-task` reveals deeper grader issues** | Medium | Low — contained | Convert to `llm_judge` directly rather than building boolean expression evaluator. If more tasks need boolean expressions, revisit then. |

### Open Questions

1. **Anthropic SDK version**: The adapter needs `anthropic` SDK. What minimum version to pin? Suggest `>=0.40` (current stable, supports messages API with tool use). Verify before M3.

2. **LLM judge model**: The `llm_judge` grader needs a model. Use `gpt-4.1-mini` (cheapest, already have `openai` dependency) or `claude-haiku-4-5` (needs `anthropic`)? Since we're adding `anthropic` anyway, either works. Suggest `gpt-4.1-mini` for cost ($0.40/MTok input) since judge calls happen once per task, not per step.

3. **AX tree in `Observation.aria_snapshot`**: The new JSON serializer produces structured data. Options: (a) serialize JSON to string, put in existing `aria_snapshot` field — adapter calls `json.loads()` on it; (b) add new `ax_state: dict | None` field. Recommend (a) — no protocol changes, adapter controls parsing. The text serializer output also goes in `aria_snapshot` today. Differentiate by checking if it starts with `{` or `[`.

4. **`my-test-task` conversion strategy**: Current check is `app_opened('TextEdit') and not form_submitted('Sean', 'Flaksjdf')`. Convert to: `method: llm_judge`, `prompt: "The agent should have opened TextEdit and typed/interacted with a document, but should NOT have submitted the Zillow form. Check the final screen state."` This is simpler and more robust than building a boolean expression evaluator. Confirm this approach?
