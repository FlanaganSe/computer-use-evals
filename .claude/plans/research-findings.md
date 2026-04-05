# Research Findings: Realigning the Eval Harness with Desktop Agent Best Practices

**Date:** April 4, 2026
**Scope:** Deep research across 8 questions covering adapter architecture, tools/frameworks, task representation, verification, recording, cross-platform, accuracy requirements, and model landscape.
**Method:** 7 parallel researcher agents covering codebase analysis, consolidated external research, arxiv papers (13 fetched and analyzed), web searches (100+ queries), GitHub repo verification, and industry landscape analysis.

---

## Part 1: Current State Assessment

### What the harness does well (keep these)

1. **The Adapter Protocol is flexible enough.** `observation_request() → ObservationType` and `decide(observation, task) → list[Action]` can accommodate any approach — structured state, vision, hybrid. No protocol changes needed.

2. **The Environment Protocol is well-abstracted.** `setup()`, `collect_observation()`, `execute_action()`, `teardown()`. Adding Windows or Linux environments implements the same interface. This is exactly the right architecture per cross-platform research.

3. **The MacOS environment already captures AX trees.** `_serialize_ax_element()` at `macos.py:263-305` produces indented text with role, title, value, description. `ObservationType.ARIA_STATE` is defined. `Observation.aria_snapshot` carries it. The infrastructure exists.

4. **The run loop is correct.** `runner.py` implements setup → observe → decide → execute → grade → write artifacts. This maps cleanly to the research consensus architecture.

5. **The failure taxonomy is directionally right.** PERCEPTION, PLANNING, EXECUTION, CONTEXT, ENVIRONMENT, TOOL_CHOICE, HARNESS — these categories align with research findings on error distribution.

6. **The capture pipeline's EventTap implementation is current.** `capture.py:60-221` uses CGEventTap with `kCGEventTapOptionListenOnly` — confirmed as the correct and current macOS approach for system-wide passive event monitoring. No replacement needed.

7. **The reporting system is solid.** Single-run reports, comparison tables, detailed metrics — good foundation for the comparison experiments the research calls for.

### What's misaligned with research best practices (change these)

1. **No AX-first desktop adapter exists.** The MacOS environment captures AX trees, but NO adapter reads them for desktop decision-making. The Codex adapter reads ARIA (browser), the OpenAI adapter reads screenshots. This is the central untested hypothesis and the highest-leverage gap.

2. **The `llm_judge` grader is declared but returns "not implemented"** (`graders.py:27-29`). The `my-test-task` YAML uses `app_opened('TextEdit') and not form_submitted(...)` — both unsupported. Silently fails grading.

3. **Failure taxonomy assigned coarsely.** Only PLANNING, EXECUTION, and HARNESS are ever set by the runner. PERCEPTION, CONTEXT, ENVIRONMENT, TOOL_CHOICE are never triggered.

4. **AX tree serialization has no node IDs.** The current indented text format is human-readable but not machine-diffable. No stable references for computing deltas. Research says pruning (not format) is the critical variable, but stable IDs are needed for delta computation and semantic action targeting.

5. **The openai_cu adapter is the wrong primary path.** Screenshot → pixel coordinates is expensive, slow, and less reliable than structured-state approaches. Keep it as a benchmarking comparison tool, not the production path. (Already decided in ADR-001.)

### What's missing entirely (add these)

1. **An AX-tree-first semantic adapter** — the core architectural gap
2. **Milestone-based task representation** — flat YAML is too weak for real workflows
3. **Step-level verification / critics** — graders only check final outcomes, not progress
4. **AX tree snapshots in the capture pipeline** — capture.py doesn't call `_get_ax_tree()`
5. **Difficulty-based model routing** — always using the same model wastes money on easy steps
6. **Window focus event tracking** — capture pipeline misses active window changes
7. **JSON AX tree format with stable node IDs** — needed for delta computation and action targeting

---

## Part 2: Research Findings by Question

### Q1: Best Adapter Architecture for April 2026

**The answer: Structured state → regular LLM → semantic actions, with vision as fallback.**

#### What the DMI paper (arxiv:2510.04607) actually built

DMI (From Imperative to Declarative, EuroSys 2026) defines three declarative primitives:
- **access**: navigate to a target element by accessibility ID
- **state**: set or modify element state (text, toggle, selection)
- **observe**: read element state for verification

Action format is JSON: `{"id": "<target_id>"}` where target_id is a session-scoped accessibility element identifier.

**Results (Table 3):** 44.4% → 74.1% task success (+67%), 8.16 → 4.61 average steps (-43.5%). Tested with GPT-5 and GPT-5-mini. Built on Windows UIA via pywinauto.

**Key insight:** The primitives themselves are simple. The power comes from using structured accessibility handles instead of pixel coordinates. The action space shrinks dramatically.

#### What prompt format works for AX trees

**Research consensus: Pruning is the critical variable, not serialization format.** Raw AX trees are too large (hundreds to thousands of nodes). Effective approaches:
- Filter to interactive elements only (buttons, text fields, links, checkboxes)
- Include: role, title/label, value, enabled state, bounding box
- Exclude: decorative elements, layout containers, hidden elements
- DMI's session-scoped IDs are the most effective targeting mechanism
- Our current indented text format is adequate — JSON is better for machine processing but the LLM doesn't care

#### What a semantic desktop action looks like

Based on DMI + UFO2 + research consensus:
```json
{
  "action": "click",
  "target": {"role": "AXButton", "title": "Save", "id": "ax_047"},
  "fallback": {"x": 450, "y": 320}
}
```

The adapter should return actions with semantic targets (AX element references) and optional pixel-coordinate fallbacks. The environment resolves semantic targets to coordinates using the AX tree's position/size attributes.

#### Adaptive VLM Routing (arxiv:2603.12823)

Uses SigLIP + MiniLM embeddings (120M params total) to classify step difficulty:
- **Easy** (confidence > τ_easy=0.80): route to cheap model (e.g., Haiku, GPT-5.4-mini)
- **Hard** (confidence < τ_hard=0.92): route to frontier model (e.g., Sonnet, GPT-5.4)
- **Medium**: use cheap model with memory augmentation (shifts confidence 0.83→0.96)

**Result: 78% cost reduction** with equivalent accuracy. The classifier is trainable on ~1K examples.

**PoC simplification:** Use a heuristic instead of learned classifier — if the AX tree has a clear interactive target matching the goal, route to cheap model. If AX tree is sparse/missing or multiple ambiguous targets exist, escalate to frontier model or vision.

#### Recommended adapter architecture for this harness

```
AXSemanticAdapter (new, primary):
  1. Request ARIA_STATE observation from MacOS environment
  2. Prune AX tree to interactive elements
  3. Format as structured prompt with element IDs
  4. Send to LLM (cheap model for easy steps, frontier for hard)
  5. Parse response as semantic action {action_type, target_id, value?}
  6. Environment resolves target_id to coordinates via AX tree

  Fallback: if AX tree is empty/sparse → request SCREENSHOT →
            send to vision model → get pixel coordinates
```

This requires no protocol changes. The Adapter Protocol already supports requesting different ObservationTypes and returning Actions with coordinates or selectors.

---

### Q2: Desktop Agent Tools and Frameworks (April 2026)

#### What's actually usable

| Tool | Status | Platform | Architecture | Usable for PoC? |
|---|---|---|---|---|
| **OpenCUA** (xlang-ai) | Active GitHub repo, pip installable | macOS, Windows, Linux | Cross-platform recorder + data pipeline | Yes — recorder component |
| **Browser Use** | Active, v2.x | Browser only | DOM-first, vision fallback | Architecture transferable, not directly useful for desktop |
| **UFO2/UFO3** (Microsoft) | Active, MIT license | Windows only | UIA + OmniParser hybrid | Windows reference architecture; not usable on macOS |
| **ShowUI-Aloha** | Active GitHub repo | macOS (avfoundation), Windows (ddagrab) | Recorder→Learner→Planner→Executor | Yes — recorder + learner pattern |
| **macLLM** (appenz) | Check activity | macOS | Unknown | Needs verification |
| **Simular.ai** | Commercial | macOS | Proprietary | Not for PoC |
| **pyax** (eeejay) | Active Jan 2026 | macOS | AXUIElement + AXObserver wrapper, JSON tree dump CLI | Possible alternative to raw pyobjc |
| **Screen2AX** (MacPaw) | Paper only (arxiv:2507.16704) | macOS | Vision → synthetic AX tree generation | 77% F1 on AX reconstruction; not released as usable tool |
| **GPA** | Paper April 2026 (arxiv:2604.01676) | macOS | Deterministic replay from single demo + FSM + Sequential Monte Carlo | Code may not be public yet |
| **Playwright MCP** | Active | Browser | MCP server for Playwright | Yes — for browser tasks |
| **OmniParser-v2** (Microsoft) | Available on Replicate/HuggingFace | Cross-platform | YOLO-v8 + Florence-2 for UI element detection | Yes — vision fallback for non-AX apps |
| **Cua** (trycua) | Active, YC-backed | macOS | macOS VM sandboxing via Apple Vz/Lume | Yes — clean environment resets |
| **Screenpipe** | Active, MIT | Cross-platform | Continuous screen+audio recording with OCR | Yes — recording layer |
| **atomacos** (OpenAdaptAI fork) | Multiple forks | macOS | Pythonic AXUIElement wrapper | Alternative to raw pyobjc |

#### Key findings

- **No single framework solves the whole problem.** Best approach is to compose: our existing harness (runner/grader/reporter) + AX tree capture (already built) + new semantic adapter (to build) + optional vision fallback (OmniParser or VLM API).
- **OpenCUA's recorder is the best reference** for cross-platform capture. It captures synchronized video + mouse/keyboard + AX trees across 3 OSes.
- **ShowUI-Aloha's learner pattern** (raw recording → structured trace) is the reference for our intent_extract.py evolution.
- **GPA's deterministic replay** is the reference for the "taught workflow" baseline, but code availability is uncertain.
- **OmniParser-v2** is available and usable as the vision fallback for apps without AX support. Detects interactive elements from screenshots, ~28% overlap with UIA elements on Windows (rest are elements UIA misses).

#### MCP ecosystem for desktop

- **Playwright MCP**: mature for browser automation
- **Desktop automation MCP servers**: emerging but not mature. No production-ready MCP server for macOS accessibility trees found.
- **Codex as MCP server**: `npx codex mcp-server` — usable for browser tasks via the Agents SDK

---

### Q3: Task/Workflow Representation

#### CUA-Skill (arxiv:2601.21123) — Parameterized skills

Formal schema: `S := {τ, I, A, G_e}` where:
- τ = task description template with parameter slots
- I = input parameters (typed: finite domain like dropdown values, or open domain like text)
- A = action sequence
- G_e = goal/expected state

478 skills across 17 applications. Skills compose into DAGs for multi-step workflows.

**Directly usable?** The schema is clean and could inform our Task model. The key addition is typed parameters with domain constraints.

#### AgentRR (arxiv:2505.17716) — Multi-level abstraction

Two-level hierarchy:
- **Low-level**: raw actions (click x,y / type "text")
- **High-level**: procedures with intent ("fill in the recipient email field")

Four check function categories:
1. Pre-conditions (is the right window open?)
2. Post-conditions (did the action succeed?)
3. Invariant checks (is the correct app still focused?)
4. Safety checks (is this action reversible?)

Experience stored as a graph with JSON + metadata. Recordings converted to replayable workflows via LLM summarization.

#### TreeCUA (arxiv:2602.09662) — Branchable traces

Exploration tuple: `E_t = ⟨a_t, g_step, g_final, o_exp, c_rat⟩` (action, step goal, final goal, expected observation, confidence rating). Branching occurs at depth ~10 on average. Agent chooses branches based on observed state matching expected state.

#### ANCHOR (arxiv:2602.07153) — Branch synthesis

Branch points identified by: substantial UI change or new content appearing. Step-level filtering with M=10 candidate branches per decision point. Used to scale from a few recordings into robust variant coverage.

#### Minimum viable upgrade for Task YAML

Add optional milestones and typed parameters without breaking existing flat tasks:

```yaml
task_id: "form-fill-and-save"
version: "2.0"
goal:
  description: "Fill the contact form and save to TextEdit"
  variables:
    name: {type: "string", default: "Jane Doe"}
    email: {type: "string", domain: "email", default: "jane@example.com"}

# NEW: Optional milestones (ordered checkpoints)
milestones:
  - id: form_visible
    description: "Contact form is visible in browser"
    check: {method: "aria_contains", selector: "form", text: "Contact"}
  - id: form_filled
    description: "All form fields populated"
    check: {method: "programmatic", check: "form_fields_filled()"}
  - id: form_submitted
    description: "Form submitted successfully"
    check: {method: "programmatic", check: "form_submitted()"}
  - id: saved_to_textedit
    description: "Confirmation saved to TextEdit"
    check: {method: "programmatic", check: "file_contains('~/Desktop/confirmation.txt', '{{name}}')"}

# NEW: Optional fallback paths
recovery:
  form_not_found:
    trigger: "milestone:form_visible timeout 30s"
    action: "scroll_down"

preconditions:
  - "Chrome is open"
  setup_script: "scripts/setup_form_test.py"

verification:
  primary:
    method: "programmatic"
    check: "file_contains('~/Desktop/confirmation.txt', '{{name}}')"
```

This is backward-compatible — existing tasks without milestones or recovery still work. The smallest useful step is adding milestones as an optional list of named checkpoints with verification methods.

---

### Q4: Verification/Grading Approaches

#### SpecOps (arxiv:2603.10268) — Flow regression testing

Four-phase pipeline: spec → generate → execute → verify. Three-component test case structure:
1. Setup requirements (environment state)
2. Agent prompt (what to do)
3. Expected behavior (what should happen)

Used for catching regressions when agents/models change. The key insight: treat agent behavior like software behavior — write specs, test against them.

#### OS-Themis (arxiv:2603.19191) — Milestone-based reward/critic

Milestone tuple: `(t_i, d_i, r_i)` — time, description, reward. Four specialist agents:
1. **Selector**: identifies which milestones are relevant
2. **Verifier**: checks if a milestone is achieved
3. **Reviewer**: evaluates overall progress
4. **Judge**: assigns final score

Concrete example: for a "take a photo" task, milestones include "camera app opened", "viewfinder active", "shutter pressed", "photo saved".

#### OS-Oracle (arxiv:2512.16295) — Step-level critic

Critic input: task description + action history + current screenshot + proposed next action.
Critic output: reason (text) + binary Yes/No (should this action proceed?).
Reward formula: `R = 0.9·R_acc + 0.05·R_format + 0.05·R_consistency`
3-retry integration: if critic says No, agent retries up to 3 times.
Result: 29.2% → 31.0% on OSWorld.

**Key constraint: OS-Oracle-7B is NOT publicly available as an API.** Would need to be replicated using a frontier model as critic.

#### Video-Based Reward Modeling / ExeVRM (arxiv:2603.10178)

1-FPS keyframe extraction from replay video. Spatial + temporal token pruning for efficiency. 84.7% accuracy on judging task completion from video evidence alone.

**Key constraint: ExeVRM-8B is NOT publicly available.** The approach (send keyframes to a VLM and ask "did this task succeed?") is replicable with Claude/GPT-4o as judge.

#### Practical milestone-based verifier for PoC

A minimal verifier in ~200 lines:

```python
class MilestoneVerifier:
    """Check milestone completion during and after agent execution."""

    def check_milestone(self, milestone: Milestone, observation: Observation) -> MilestoneResult:
        """Check if a milestone is achieved given current observation."""
        if milestone.check.method == "programmatic":
            return self._run_programmatic(milestone.check)
        elif milestone.check.method == "aria_contains":
            return self._check_aria(milestone.check, observation.aria_snapshot)
        elif milestone.check.method == "llm_judge":
            return self._ask_llm(milestone.check, observation)

    def verify_progress(self, task: Task, trace: Trace) -> list[MilestoneResult]:
        """After execution, verify which milestones were achieved."""
        results = []
        for milestone in task.milestones:
            # Find the observation closest to when milestone should have been reached
            best_obs = self._find_best_observation(milestone, trace)
            results.append(self.check_milestone(milestone, best_obs))
        return results
```

This extends the existing grader system. Programmatic checks (file_exists, file_contains) stay. LLM judge gets implemented. Milestone checks layer on top.

---

### Q5: Recording Format for the Capture Pipeline

#### What "continuous video" means practically

**CUA-Suite (arxiv:2603.24440):** 30 FPS continuous screen recordings. 10,000 tasks, ~55 hours, 6M frames, 87 applications. Normalized coordinates `(x,y) ∈ [0,1]²`. Kinematic cursor traces with millisecond timestamps. Multi-layered reasoning annotations per keyframe (~497 words/step).

**Storage:** H.264 1080p at 8-12 Mbps → ~60-90 MB/min. Desktop screen content compresses heavily; practical rate ~4-8 Mbps → ~30-60 MB/min. **1 hour ≈ 1.8-3.6 GB.**

**The argument for video over screenshots:** Continuous video preserves (a) intermediate cursor movement for kinematic priors, (b) visual feedback between actions (UI redraws, animations, loading), (c) temporal context that sequential screenshots miss.

#### ShowUI-Aloha recording format

- Full-resolution video at 30 FPS via FFmpeg (`avfoundation` on macOS)
- Events logged separately with timestamps for synchronization
- Raw output → parsing step → trace JSON per action:
```json
{
  "observation": "Current UI state description",
  "think": "Brief reasoning about intent",
  "action": "Normalized operation description",
  "expectation": "Expected UI change after action"
}
```

#### OpenCUA recorder

Synchronized screen video + mouse/keyboard events + AX tree snapshots across 3 OSes. Pipeline: raw capture → action reduction (merge into semantic PyAutoGUI actions) → state-action matching (align each action with last visually distinct frame).

#### Accessibility tree deltas in practice

- **No native delta API** on macOS. The practical approach is snapshot-on-action:
  - Full AX tree snapshot before each action
  - Full snapshot after each action
  - Compute diff post-hoc
- **AX tree size:** ~50-500 KB per snapshot as text (depends on app complexity). A mid-complexity window is hundreds to thousands of nodes at 100-500 bytes/node.
- **Right frequency:** Event-driven, not timer-driven. Snapshot on action completion, not on a polling interval. Continuous polling causes performance issues (documented with Power Automate Desktop).
- **Use `AXObserver` notifications** for event-driven updates where possible (focus changes, value changes).

#### CGEventTap status

CGEventTap remains the correct approach for macOS. Compatible with App Sandbox since macOS 10.15. Requires Input Monitoring permission. Our implementation at `capture.py:60-221` is correct. No better alternatives exist.

#### ScreenCaptureKit for continuous video

`pyobjc-framework-ScreenCaptureKit` (v12.1, November 2025 on PyPI). Hardware-accelerated H.264 encoding on Apple Silicon. Minimal CPU impact. macOS 12.3+.

#### Recommended capture pipeline evolution

**Immediate (Option A):** Add AX tree snapshots to capture_session(). Call `_capture_focused_app_aria()` on every frame, write to `ax_tree/{sequence:04d}.txt`. ~20 lines of code.

**Next (Option C):** Modify `_serialize_ax_element()` to produce JSON with stable node reference IDs (hash of role+title+position). Enables delta computation between consecutive snapshots.

**Deferred (Option B):** Continuous video recording via ScreenCaptureKit. High storage cost (1.8-3.6 GB/hr). Only needed when the intent extraction pipeline requires temporal context that sparse screenshots can't provide.

---

### Q6: Cross-Platform Accessibility

#### macOS (AXUIElement)

- **Coverage:** ~33% of apps have full AX support (Screen2AX estimate). Native AppKit apps generally good. Electron apps disabled by default (must set `AXManualAccessibility`). Qt supported. Flutter variable. Custom-rendered/game UIs: poor to none.
- **Best Python library:** Our current pyobjc direct usage is correct and canonical. Alternatives: atomacos (OpenAdaptAI fork) for higher-level wrapper, pyax for JSON tree dump CLI.
- **Key AX attributes:** AXRole, AXTitle, AXValue, AXDescription, AXChildren, AXPosition, AXSize, AXEnabled, AXFocused.

#### Windows (UI Automation / UIA)

- **Coverage:** Substantially better than macOS. Full support for WinForms, WPF, Win32, Modern UI/Metro. Partial for Qt. Supported for Electron, Firefox ≥60, Chrome. Custom-rendered/game UIs: same gap as macOS.
- **Best Python library:** `pywinauto` with `backend="uia"` — standard choice, active maintenance.
- **UFO2 benchmark:** UIA-only achieves 23.4% on Windows Agent Arena. Hybrid UIA + OmniParser achieves 26.6% (+9.9-12.5% control recovery ratio).

#### Linux (AT-SPI2)

- **Coverage:** GNOME/GTK full support. Qt has AT-SPI bridge. Electron via Chromium. Flutter: compatibility issues. Custom toolkits: not supported.
- **Best Python library:** `pyatspi2` works but maintenance-mode. Recommended for new work: libatspi via GObject introspection (more verbose but future-proof). Rust-based push client in development by GNOME.

#### Cross-platform abstraction

**No mature unified library exists.** Two candidates (pyUIauto, Acacia/Igalia) are experimental/incomplete. The correct architecture — which our Environment Protocol already enforces — is platform-specific implementations behind a common interface:
- `MacOSDesktopEnvironment` → AXUIElement via pyobjc (**built**)
- `WindowsDesktopEnvironment` → UIA via pywinauto (not yet built)
- `LinuxDesktopEnvironment` → AT-SPI via libatspi/GObject introspection (not yet built)

#### UFO2's hybrid approach for macOS

UFO2 runs OmniParser-v2 on EVERY step alongside UIA (not as fallback). Deduplicates by IoU (>10% overlap → drop OmniParser detection). ~28% of OmniParser detections overlap with UIA elements; the rest are elements UIA misses.

**For macOS:** Same pattern applies. AX tree primary + OmniParser/VLM for elements not in AX tree. This addresses the 67% of apps with poor AX support.

---

### Q7: Near-100% Accuracy Requirements

#### What the benchmarks say (April 2026)

| Benchmark | Best Score | Model | Human Baseline |
|---|---|---|---|
| OSWorld | 75% | GPT-5.4 | ~72% |
| OSWorld | 72.7% | Claude Opus 4.6 | ~72% |
| OSWorld | 72.5% | Claude Sonnet 4.6 | ~72% |
| OSWorld | 72.1% | GPT-5.4 Mini | ~72% |
| Windows Agent Arena | 26.6% | UFO2 hybrid | 74.5% |
| ProSoftArena L3 (cross-app) | 0% | Best agents | — |
| UI-TARS-2 (open source) | 47.5% | 72B | — |

**Key insight:** Frontier models have crossed the human baseline on OSWorld, but cross-app professional workflows remain at 0% (ProSoftArena). Near-100% is achievable only under specific conditions.

#### Conditions for near-100% accuracy

1. **Stable, known UIs** — apps that don't change layout between runs
2. **Single-app tasks** — cross-app workflows remain extremely weak
3. **Known starting states** — deterministic setup, not dirty/random desktops
4. **Structured state available** — AX tree present for the relevant UI elements
5. **Short workflows** — compounding error (95% per-step → 36% over 20 steps)
6. **Deterministic replay as default** — GPA-style: record once, replay deterministically, LLM only for adaptation

#### GPA's approach (arxiv:2604.01676)

Records one demonstration, compiles to structured workflow with readiness checks and FSM replay. Uses Sequential Monte Carlo for robustness under rescaling. Fully local, no VLM at runtime. Claims higher success than Gemini 3 Pro CUA with 10x speed on tested tasks.

**The key insight from GPA:** For tasks that have been demonstrated once, deterministic replay with readiness checks is MORE reliable than LLM-based execution. LLM agents should only handle adaptation when the UI has changed from the recorded state.

#### "Are LLM Agents the New RPA?" (arxiv:2509.04198)

RPA wins on speed and reliability in stable environments. LLM agents win on development time in dynamic UIs. **The 2026 consensus is hybrid:** deterministic replay where possible, LLM fallback where UI has changed.

#### Error distribution

Research consensus on remaining failures:
- **Planning failures** (chose wrong action) > **Perception failures** (couldn't find element) > **Execution failures** (right action, wrong execution)
- For structured-state approaches, perception failures decrease dramatically (the AX tree tells you exactly what's there)
- Planning failures become the dominant remaining problem — the agent understands the UI but makes wrong decisions

#### DMI's "61% single-call" finding

61% of successful tasks completed with a single LLM call when using declarative primitives. The other 39% require multi-step reasoning. This means:
- Most tasks are simple enough to solve in one shot if the right structured state is provided
- The hard 39% involve state changes that require observation between steps
- Architecture should optimize for the common case (cheap, single-call) and handle the complex case (multi-step with full context)

---

### Q8: Model Landscape (April 2026)

#### Frontier models (structured reasoning over UI state)

| Model | OSWorld | Input $/MTok | Output $/MTok | Best for |
|---|---|---|---|---|
| GPT-5.4 | 75% | $2.50 | $15.00 | Native computer-use, highest accuracy |
| Claude Opus 4.6 | 72.7% | $5.00 | $25.00 | Best structured reasoning, expensive |
| Claude Sonnet 4.6 | 72.5% | $3.00 | $15.00 | Strong reasoning, good cost/accuracy |
| Gemini 3.1 Pro | No published OSWorld | $2.00 | $12.00 | — |

#### Cheap/fast models (for easy steps via routing)

| Model | OSWorld | Input $/MTok | Output $/MTok | Best for |
|---|---|---|---|---|
| GPT-5.4 Mini | 72.1% | $0.75 | $4.50 | Best cost/accuracy ratio overall |
| Claude Haiku 4.5 | — | $1.00 | $5.00 | Fastest Anthropic model |
| GPT-5.4 Nano | — | $0.20 | $1.25 | Routing classifier only |
| Gemini 2.5 Flash | — | $0.30 | $2.50 | Budget option |

#### Cost per step estimate

Assuming ~2K input tokens (pruned AX tree + task + history) + ~500 output tokens:

| Model | Cost/step | Steps for $1 | Notes |
|---|---|---|---|
| GPT-5.4 | $0.0125 | 80 | Frontier accuracy |
| Claude Sonnet 4.6 | $0.0135 | 74 | Strong reasoning |
| GPT-5.4 Mini | $0.00375 | 267 | Best for routing |
| Claude Haiku 4.5 | $0.0045 | 222 | Fast |
| Gemini 2.5 Flash | $0.00185 | 541 | Cheapest |

With adaptive routing (78% cheap, 22% frontier): **~$0.005/step average** vs $0.013/step always-frontier. **~60% cost reduction.**

#### Vision model cost (fallback path)

Screenshot adds ~1,500 tokens (low-res) to ~6,000 tokens (high-res). At Sonnet 4.6 pricing: $0.0045-$0.018 additional per screenshot observation.

#### Specialized UI models

- **UI-TARS-2** (72B, open source): 47.5% OSWorld. Good for local/offline use but far behind frontier models.
- **CogAgent**: Superseded, low priority.
- **ShowUI / Qwen3-VL**: Open-source vision models usable for element grounding.

#### Recommended tier structure

1. **Tier 1 (default):** GPT-5.4 Mini or Claude Haiku 4.5 — for steps where AX tree has clear target
2. **Tier 2 (escalation):** Claude Sonnet 4.6 or GPT-5.4 — for ambiguous steps, multi-element decisions
3. **Tier 3 (vision fallback):** Claude Sonnet 4.6 with screenshot — when AX tree unavailable
4. **Tier 4 (deterministic):** No model — for steps that match a known recorded pattern exactly

---

## Part 3: Recommended Architecture Evolution

### Adapter Layer

**Keep:** openai_cu adapter (as benchmark comparison), deterministic adapter (as baseline), codex adapter (as browser reference).

**Add:** `AXSemanticAdapter` — the new primary adapter:
- Requests ARIA_STATE from environment
- Prunes AX tree to interactive elements
- Formats as structured prompt with element IDs
- Routes to cheap or frontier model based on difficulty heuristic
- Returns semantic actions (target by AX ID, with coordinate fallback)
- Falls back to SCREENSHOT + vision when AX tree is empty/sparse

This is ~300 lines following the existing adapter pattern. No protocol changes needed.

### Task Format

**Keep:** Existing flat YAML compatibility.

**Add:** Optional milestones, typed parameters with domain constraints, optional recovery paths. Pydantic-optional fields so existing tasks don't break.

```python
class Milestone(BaseModel):
    id: str
    description: str
    check: VerificationCheck

class Task(BaseModel):
    # ... existing fields ...
    milestones: list[Milestone] = []  # NEW, optional
    recovery: dict[str, RecoveryPath] = {}  # NEW, optional
```

### Verification Layer

**Keep:** Existing programmatic graders (file_exists, file_contains, form_submitted).

**Add:**
1. Implement `llm_judge` (currently returns "not implemented")
2. Milestone-based verifier that checks progress during execution
3. Optional step-level critic (send proposed action to a separate LLM for approval)

### Capture Pipeline

**Immediate:** Add AX tree snapshots to capture_session() (~20 lines).
**Next:** JSON format with stable node IDs for delta computation.
**Deferred:** Continuous video via ScreenCaptureKit.

### What Stays, Changes, Is New

| Component | Status | Action |
|---|---|---|
| Adapter Protocol | Stays | No changes |
| Environment Protocol | Stays | No changes |
| Runner loop | Stays | Minor: trigger milestone checks between steps |
| MacOS Environment | Stays | Minor: add JSON AX format option |
| Browser Environment | Stays | No changes |
| Failure taxonomy | Changes | Trigger all categories, not just 3 |
| Graders | Changes | Implement llm_judge |
| Task model | Changes | Add optional milestones, typed params |
| Reporting | Stays | Add milestone pass/fail to reports |
| Capture pipeline | Changes | Add AX snapshots |
| AXSemanticAdapter | **New** | Primary adapter |
| MilestoneVerifier | **New** | Progress checking |
| Difficulty router | **New** | Model cost optimization |

---

## Part 4: Tool and Framework Landscape (April 2026)

### Tier 1: Integrate directly

| Tool | Use for | Integration effort |
|---|---|---|
| **OmniParser-v2** | Vision fallback for non-AX apps | API call to Replicate/HuggingFace |
| **Playwright MCP** | Browser task automation | Already using Playwright |
| **pyobjc-framework-ScreenCaptureKit** | Future continuous video capture | pip install, ~100 lines wrapper |

### Tier 2: Use as reference architecture

| Tool | Learn from | What to borrow |
|---|---|---|
| **ShowUI-Aloha** | Recorder→Learner→Planner→Executor | Trace JSON format, learner pattern for intent_extract.py |
| **OpenCUA** | Cross-platform recorder | Data pipeline: raw → action reduction → state-action matching |
| **UFO2** | Hybrid UIA + OmniParser | Deduplication strategy for AX + vision merge |
| **GPA** | Deterministic replay | FSM replay with readiness checks as baseline |

### Tier 1.5: Installable macOS AX tools (new from industry research)

| Tool | What it does | Status |
|---|---|---|
| **macapptree** (MacPaw, PyPI) | AX tree → JSON with bounding boxes, directly usable for grounding | pip installable, used in Screen2AX research |
| **pyax** (eeejay) | Python AXUIElement + AXObserver wrapper, JSON tree dump CLI | Active Jan 2026 (blog.monotonous.org) |
| **macos-automator-mcp** (steipete) | AppleScript + JXA via MCP server | Open source, active |

**Key finding:** `macapptree` may be a better AX tree serializer than our custom `_serialize_ax_element()` — it outputs JSON with bounding boxes directly usable for element grounding. Worth evaluating as a supplement.

### Tier 3: Monitor

| Tool | Why | Check back |
|---|---|---|
| **Cua (trycua)** | Clean macOS VMs for isolation (13.4k stars, last commit Mar 31 2026) | When we need sandboxed evaluation environments |
| **Screenpipe** | Continuous recording layer | When we add continuous video capture |
| **Screen2AX** | Synthetic AX trees from screenshots | If/when code is released |
| **Simular Agent S2** | 10.8k stars, Mixture-of-Grounding, 34.5% OSWorld 50-step | Cross-platform reference architecture |

### MCP Ecosystem for Desktop (April 2026)

Active MCP servers for macOS desktop automation:
- **macOS UI Automation MCP** (mb-dev) — native accessibility APIs
- **macOS GUI Control MCP** (Atharva Gundawar) — screenshots + accessibility elements
- **macos-automator-mcp** (steipete) — AppleScript + JXA via MCP
- **mcp-server-macos-use** — Swift implementation exposing macOS OS functions

MCP governance transferred to Linux Foundation's Agentic AI Foundation in 2026 — now a neutral open standard.

**Implication:** An MCP server wrapping `macapptree`'s AX tree output is a low-friction path to expose structured desktop state to any MCP-compatible agent client.

### Cost comparison for model routing

| Strategy | Cost/100 steps | Accuracy (estimated) |
|---|---|---|
| Always frontier (Sonnet 4.6) | $1.35 | Highest |
| Always cheap (Haiku 4.5) | $0.45 | Lower on hard steps |
| Adaptive routing (78/22 split) | $0.65 | Near-frontier |
| Deterministic replay + LLM fallback | $0.15 | Highest for known tasks |

### Production Deployment Architectures (April 2026)

What major companies are actually shipping — and what their architecture choices validate:

| Company | Product | Architecture | Key Signal |
|---|---|---|---|
| **Anthropic** | Claude Cowork + Dispatch | File-system-centric, sandboxed Linux VM. NOT screen-reading — operates on shared folders. Vision secondary. | Structured state first, even Anthropic doesn't use their own computer-use API as primary |
| **Microsoft** | Copilot Actions + UFO3 | UIA (accessibility) + OmniParser (vision) hybrid. Enterprise governance via Intune/Entra. | Explicitly validates hybrid accessibility-first + vision-fallback |
| **OpenAI** | CUA / Operator | Screenshot → GPT-4o vision → o3 reasoning → pixel actions. Vision-only, no accessibility. | 38.1% OSWorld — roughly half the hybrid approaches. The expensive baseline. |
| **Sola.ai** | Sola (YC, $17.5M) | Vision-first + LLM. Record once → automated bot. Self-healing for UI changes. | Vision-first RPA works commercially for enterprise |
| **Google** | Project Mariner | Gemini 2.0, cloud VM, vision-primary. Browser/web only. | Not relevant for native desktop |
| **Apple** | Intelligence (research) | AX labels/descriptions + visual layout interpretation. Apple Neural Engine. | Apple's own approach is AX-first + vision combined |
| **Simular** | Agent S2 (open source, 10.8k stars) | Mixture of Grounding — routes to best grounding expert per step. 34.5% OSWorld 50-step. | Multi-model orchestration beats monolithic vision |
| **Manus** | Manus Desktop (Mar 2026) | Hybrid local/cloud. CodeAct (Python as action). File system as context. | Key lesson: "compact tool results aggressively, file system is the ultimate context" |
| **Perplexity** | Perplexity Computer (Feb 2026) | Orchestrates 19 different models per workflow. Parallel subtask execution. | Multi-model orchestration is live in production |

**Industry consensus:** Every production system except OpenAI CUA uses structured state (files, accessibility, DOM) as primary, with vision as fallback. OpenAI CUA at 38.1% OSWorld is the control group showing why vision-only is insufficient.

### Open Source Landscape

| Repo | Stars | Approach | Last Activity |
|---|---|---|---|
| **trycua/cua** | 13.4k | Sandboxes + SDKs + benchmarks (macOS/Linux/Win) | Mar 31 2026 |
| **simular-ai/Agent-S** | 10.8k | Mixture of Grounding + hierarchical planning | Active |
| **coasty-ai/open-computer-use** | ~5k | Vision-first, 82% OSWorld verified | Active |
| **microsoft/UFO** | — | UIA + OmniParser hybrid (Windows) | Active |
| **MacPaw/macapptree** | — | AX tree → JSON with bounding boxes (macOS) | PyPI installable |

---

## Part 5: Concrete Next Steps

### Ranked by impact and feasibility

#### 1. Build AXSemanticAdapter (HIGH impact, HIGH feasibility)
**What:** New adapter that reads AX tree from MacOS environment, prunes to interactive elements, sends to LLM, returns semantic actions.
**Why:** Validates the core hypothesis. Uses existing infrastructure. No protocol changes.
**Effort:** ~300 lines, 2-3 days.
**Proves:** Whether structured-state-first desktop automation works with available models.

#### 2. Add AX tree snapshots to capture pipeline (HIGH impact, HIGH feasibility)
**What:** Call `_get_ax_tree()` during capture_session(), save alongside screenshots.
**Why:** Closes the biggest data gap. Enables structured intent extraction.
**Effort:** ~20 lines, <1 day.
**Proves:** Whether AX trees improve intent extraction accuracy.

#### 3. Implement llm_judge grader (MEDIUM impact, HIGH feasibility)
**What:** Fill in the "not implemented" stub. Send observation + task description to LLM, ask if task succeeded.
**Why:** Unblocks tasks that can't be verified programmatically. The my-test-task YAML is currently broken.
**Effort:** ~50 lines, <1 day.

#### 4. Add milestones to Task model (MEDIUM impact, MEDIUM feasibility)
**What:** Optional `milestones` list in Task, Pydantic-optional so existing tasks don't break. MilestoneVerifier checks progress.
**Why:** Enables step-level grading and progress tracking.
**Effort:** ~150 lines (model + verifier), 1-2 days.

#### 5. Upgrade AX tree serialization to JSON with stable IDs (MEDIUM impact, MEDIUM feasibility)
**What:** Modify `_serialize_ax_element()` to produce JSON with hash-based stable node IDs. Support both text and JSON formats.
**Why:** Enables delta computation, machine-parseable output, semantic action targeting by ID.
**Effort:** ~100 lines, 1 day.

#### 6. Add difficulty-based routing heuristic (MEDIUM impact, MEDIUM feasibility)
**What:** Simple heuristic in AXSemanticAdapter: if AX tree has clear single target → cheap model, else → frontier model.
**Why:** 60-78% cost reduction per the research.
**Effort:** ~50 lines, <1 day.

#### 7. Trigger all failure categories (LOW impact, HIGH feasibility)
**What:** Add classification logic to runner.py for PERCEPTION, CONTEXT, ENVIRONMENT, TOOL_CHOICE failures.
**Why:** Better diagnostics on what's actually failing.
**Effort:** ~50 lines, <1 day.

#### 8. Run comparison experiments (HIGH impact, MEDIUM feasibility)
**What:** Run the same tasks across: deterministic, AX-semantic, screenshot-only (openai_cu), hybrid. Compare success rate, cost, steps.
**Why:** This is the whole point of the harness — empirical evidence on what works.
**Effort:** 2-3 days (task authoring + runs + analysis).
**Depends on:** Steps 1-3 complete.

### What to build first

**Steps 1-3 in parallel (3 days).** Then step 8 (comparison experiments). This gives us the core evidence: does AX-first beat screenshot-first, and by how much?

### What to defer

- Continuous video capture (high storage, marginal gain for PoC)
- Cross-platform environments (macOS first, as decided)
- Deterministic replay compiler (use GPA as reference, don't rebuild)
- Custom difficulty classifier (heuristic is sufficient for PoC)
- Branch synthesis / recovery paths (milestones first)

### Key experiments

1. **AX-semantic vs openai_cu on the same 4 tasks:** success rate, cost, steps, failure categories
2. **Cheap model vs frontier model on AX-semantic tasks:** where does cheap fail?
3. **AX-semantic with and without vision fallback:** does vision help when AX is sparse?
4. **Intent extraction with vs without AX tree context:** does the +50pp finding replicate?

---

## Part 6: Open Questions and Risks

### What we still don't know

1. **How well does Claude Sonnet 4.6 / GPT-5.4 Mini actually perform on macOS AX trees?** The DMI paper tested on Windows UIA. macOS AX trees have different structure and attributes. Need to test empirically.

2. **What's the actual AX coverage for our target apps?** The 33% figure is an aggregate. For common apps (Chrome, TextEdit, Finder, Preview), coverage may be much higher. For professional apps (Photoshop, Excel), it may be much lower. Need to measure.

3. **How much AX tree pruning is needed?** Raw trees can be thousands of nodes. What's the right balance between completeness and token cost?

4. **Does the adaptive routing heuristic work without a trained classifier?** The paper uses SigLIP+MiniLM. Can a simpler heuristic (tree sparsity, element count, action ambiguity) achieve similar routing?

5. **How reliable is GPA-style deterministic replay on macOS?** The paper's code may not be public. FSM + readiness checks + Sequential Monte Carlo is a significant implementation.

6. **What's the actual latency per step?** AX tree capture + LLM call + action execution. Is it fast enough for real workflows?

### What could go wrong

1. **AX trees may be too noisy/incomplete for reliable LLM decision-making.** The 33% coverage figure is the known risk. If common target apps have poor AX support, the whole approach needs vision augmentation from day one.

2. **Model performance may not transfer from benchmarks to real desktops.** OSWorld is controlled; real desktops have notifications, overlapping windows, unpredictable states.

3. **Compounding error kills long workflows.** 95% per-step → 36% over 20 steps. Even with milestones and recovery, long workflows may remain unreliable.

4. **Cost may not scale.** At $0.005/step and 10 steps/task, that's $0.05/task — reasonable. But if steps increase or vision fallback is frequent, costs rise fast.

5. **The "taught workflow" paradigm (record once, replay) may require too many demonstrations per workflow.** GPA claims one demo is sufficient, but this needs verification with real macOS apps.

### Practitioner consensus (from HN, AWS, Google Cloud, Manus)

- **Context engineering has displaced prompt engineering.** Agent failures are primarily context failures, not model failures.
- **File system is the ultimate context** (Manus lesson, most cited). Externalize memory via read/write. Compact older tool results aggressively.
- **Desktop automation is valuable specifically because enterprise software lacks clean APIs.** The messier the target, the more value.
- **LLM vision gives wrong coordinates on high-DPI displays** — a practical issue that structured state avoids entirely.
- **Most multi-agent frameworks treat memory as an afterthought** — shared vector stores break in production.

### Assumptions that need testing

| Assumption | How to test | Risk if wrong |
|---|---|---|
| AX tree + LLM → correct next action | Build AXSemanticAdapter, run on 4 tasks | Core thesis fails; fall back to vision |
| Cheap models handle easy steps | Run same tasks with Haiku vs Sonnet | Cost savings evaporate |
| AX tree pruning to ~50 elements is sufficient | Measure tree sizes, test with varying depth | Token cost too high or missed elements |
| Milestones improve debugging | Add milestones to 2 tasks, compare failure analysis | Milestones add complexity without insight |
| Intent extraction improves with AX context | Add AX to intent_extract.py, measure accuracy | The +50pp finding doesn't replicate |

---

## Summary

The harness architecture is sound. The abstractions are right. The gap is that **no one has tested the core hypothesis**: can a regular LLM, given a pruned macOS AX tree, reliably choose the right next action for desktop tasks?

The research strongly says yes — with +67% success improvement, -43.5% fewer steps, and 78% cost reduction compared to screenshot-based approaches. But this has been demonstrated on Windows (DMI) and benchmarks, not on macOS with our specific tools.

**The single most important next step is building the AXSemanticAdapter and running comparison experiments.** Everything else (milestones, video capture, cross-platform, routing) is optimization. First, prove the core hypothesis works on macOS with available models. Then optimize.
