# Consolidated Research: Desktop Agent Eval Harness

> Consolidated 2026-04-04 from 9 research documents (docs/research/*, docs/init-idea.md, .claude/plans/research*.md). Redundancy removed, actionable items prioritized.

---

## What We're Building

A user records their screen (optionally narrating with voice) while performing desktop tasks. The system extracts structured intent from that recording. An agent replays those actions on the user's computer. **The eval harness tests whether agents can reliably execute these workflows.**

The PoC answers one question: **Is this feasible, and where does it break?**

We need simple scripts and flows that can run evaluation tests. They should be simple, understandable, and flexible. macOS-first.

### What the first system is, and is not

The first system is:

- a small eval harness,
- a way to normalize user evidence into tasks,
- and a way to compare different harness choices and execution strategies.

It is not:

- the full desktop product,
- a generic benchmark platform,
- or a full cross-OS agent architecture.

The practical implication is important: **recordings should be treated as task-authoring evidence, not as the canonical replay format.** The canonical artifact for the PoC should be a normalized task definition plus the traces and grader results from each run.

---

## 1. The Core Insight: The Harness Is the Product

The most important finding across all research: **how you present screen state to an agent determines performance more than which model you use.**

Evidence:
- Structured UI representations reduce context by 33%+ with negligible accuracy loss (ShowUI, 2026)
- Including historical screenshots increases compute 3.4x but improves performance only 3.0% (SimpAgent)
- A 7B model with good context management matches a 139B model without it (Memory Equalization Hypothesis)
- Per-step reliability of 95% yields only 36% success over 20 steps (compounding error)

This means eval design is the real engineering challenge, not model selection.

### How to approach building evals

Three principles from Anthropic's eval guidance and the Eval-Driven Development pattern (EDD, [arxiv 2411.13768](https://arxiv.org/abs/2411.13768)):

- **Write evals before agent code.** The eval tasks, verification checks, and scoring define what "working" means. Build these first, then wire up the agent. Teams with evals upgrade models in days; teams without face weeks of manual testing.
- **Start with one real failure mode, not a comprehensive taxonomy.** Pick a task that fails today. Make it pass. Add the next one. 20-50 simple tasks drawn from real failures is enough to start — a comprehensive benchmark is not the goal.
- **Grade outcomes, not paths.** Check whether the file was downloaded, not whether the agent clicked the exact buttons you expected. Agents find valid routes you didn't anticipate. Use a known-good reference solution + clean environment per trial to separate agent failure from harness noise.

### Planning foundation

To keep the PoC flexible without over-engineering it, the harness should be built around five explicit objects:

- `task` — the normalized task definition
- `trial` — one isolated attempt
- `trace` — the per-step observation and action record
- `grader` — the scoring logic for outcome and, when needed, trajectory
- `report` — aggregated comparison output across runs

This is enough structure to support planning and iteration. It is deliberately smaller than a full framework design.

---

## 2. Current State of the Art (Key Numbers)

| Benchmark | Best Model | Score | Human Score | Date |
|---|---|---|---|---|
| OSWorld (desktop, open-ended) | OSAgent | 76.3% | ~72% | Oct 2025 |
| Windows Agent Arena | Navi | 19.5% | 74.5% | Sep 2024 |
| ScreenSpot-Pro (dense UI grounding) | ScreenSeekeR | 48.1% | — | ICLR 2025 |
| macOS AX tree availability | — | 33% full support | — | Screen2AX, Jul 2025 |

**Key takeaway**: Desktop automation crossed the human baseline on structured benchmarks (OSWorld) but remains far behind on Windows and on grounding in professional/dense UIs. The 33% macOS AX coverage finding is critical — any macOS-first harness cannot rely solely on accessibility APIs.

---

## 3. Architecture: Four Independent Dimensions

Every research source converges on the same decomposition. The eval harness should let you vary each dimension independently:

```
Observation          →  Grounding           →  Model              →  Execution
(what agent sees)       (find elements)         (decide action)       (do it)
─────────────           ─────────────           ──────────            ─────────
Screenshot              Graph match             Claude                Click/type/scroll
A11y tree               LLM vision              OpenAI                Semantic invoke
Hybrid (both)           LLM structured           Local (Qwen-VL)      Deterministic replay
                        Hybrid                   Deterministic         Dry run
```

Each combination is a test condition. Comparing conditions reveals where value comes from.

### Observation (what the agent sees each step)

```
Observation = {
  screenshot: bytes           # always available, ~50K tokens/frame
  a11y_tree?: UITree          # AX/UIA/AT-SPI — ~4K tokens, but only 33% macOS coverage
  window_metadata: WindowInfo # focused app, window list
  events: UIEvent[]           # what changed since last step
  artifacts: ArtifactState    # downloads, clipboard, filesystem changes
}
```

Research consensus: **a11y tree primary, screenshot fallback**. But the 33% AX coverage gap means the harness must gracefully handle missing trees and track AX availability as a metric.

### Actions (what the agent can do)

Use a layered approach — try semantic first, fall back as needed:

1. **Semantic**: `invoke(element_ref)`, `set_text(element_ref, text)`, `select(element_ref, value)`
2. **Grounded visual**: click/drag on a bounding box returned by grounding
3. **Raw fallback**: coordinate click, hotkey, drag path
4. **Non-GUI tools**: file ops, clipboard, terminal commands, MCP tools

**Key metric**: *semantic action ratio* — what fraction of steps used semantic vs. pixel fallback. If low, the approach is too screenshot-dependent for production.

---

## 4. Task Specification

Define tasks by **outcome, not path**. Agents find valid routes you didn't anticipate.

For planning purposes, keep the schema intentionally small. The first version only needs enough information to set up the task, run it, and score it reliably. Do not turn the task format into a full workflow language yet.

```yaml
task_id: "download-and-rename"
version: "1.0"
apps: ["chrome", "finder"]
estimated_steps: 5
difficulty: "low"

goal:
  description: "Download the test PDF from {{url}} and rename it to {{new_name}}"
  variables:
    url: { type: "url", default: "https://test.example.com/report.pdf" }
    new_name: { type: "string", default: "q3-report.pdf" }

preconditions:
  - "Chrome is open"
  - "Downloads folder is empty"
  setup_script: "scripts/setup_download_test.py"

verification:
  primary:
    method: "programmatic"
    check: "file_exists('~/Downloads/{{new_name}}')"
  fallback:
    method: "llm_judge"
    prompt: "Does the Downloads folder contain a file named {{new_name}}?"
    threshold: 0.85

sensors_required: [a11y, screenshot, filesystem]
risk_tier: "low"
```

### Starter Task Families (12 tasks)

| Family | Example | Why it matters | Difficulty |
|---|---|---|---|
| Browser download + local file op | Download PDF, rename in Finder | Tests browser↔desktop boundary | Low |
| Form fill | Fill a web form with 5 fields | Tests text input grounding | Low |
| Cross-app copy/paste | Copy from Preview to Numbers | Tests clipboard + multi-app | Med |
| Multi-window settings | Change 2 system preferences | Tests a11y on native macOS | Med |
| File transform | Open CSV in Numbers, export as PDF | Tests multi-step with verification | Med |
| Long-horizon (10+ steps) | Multi-step spreadsheet edit | Tests memory/context degradation | High |
| Dense UI grounding | Click small toolbar button in pro app | Tests ScreenSpot-Pro problem | High |
| Popup interruption | Handle unexpected dialog mid-task | Tests robustness to unknowns | High |
| MCP tool choice | Task solvable via GUI or MCP tool | Tests whether agent uses tools | Med |
| Prompt injection | Doc with injected instructions | Tests safety (OS-Harm) | Med |
| DPI/scale change | Change display scaling mid-task | Tests visual grounding resilience | High |
| Authentication gate | Log into test app with test credentials | Tests auth flow handling | Med-High |

---

## 5. Metrics That Matter

### Primary (must have for PoC)

| Metric | What it tells you |
|---|---|
| **Task success rate** | Does it work? (pass@3 across repeated trials) |
| **Step success rate** | Where in the workflow does it break? |
| **Semantic action ratio** | Can we use a11y, or are we stuck on pixels? |
| **A11y availability rate** | What fraction of screens had usable AX trees? |
| **Cost per successful run** | Is this economically viable? |
| **Failure taxonomy** | *Why* things fail (perception, planning, execution, context, environment) |

### Secondary (add after initial runs)

| Metric | What it tells you |
|---|---|
| Human-step ratio | Efficiency vs. human baseline (agents take 1.4-2.7x more steps) |
| Context growth rate | Is context management working? (slope should be ~0) |
| Fallback rate + fallback success rate | When AI fallback fires, does it help? |
| Tool invocation rate (TIR) | Does agent use MCP tools when available? |
| Context-rot slope | Does success degrade as workflows lengthen? |
| Latency (p50/p95) | Is it fast enough for real use? |

### Failure Categories

```
PERCEPTION     — couldn't find/identify the right element
PLANNING       — chose wrong action given correct perception
EXECUTION      — right action, wrong execution (misclick, timing)
CONTEXT        — forgot earlier information, repeated action
ENVIRONMENT    — page timeout, network error, unexpected popup
SAFETY         — unsafe action, prompt injection compliance
TOOL_CHOICE    — used GUI when tool was better, or vice versa
```

---

## 6. Baselines to Compare

Run these 4 approaches on the same task suite to learn where AI adds value:

| Baseline | What it is | What it tests |
|---|---|---|
| **A: Deterministic scripted** | No LLM. AX/Playwright selectors. | Automation ceiling without AI |
| **B: One-demo compiled** | GPA-style: record once, replay deterministically with readiness checks | Value of "taught workflows" |
| **C: Screenshot-only agent** | Plain computer-use loop (screenshot → model → action) | Current default approach |
| **D: Hybrid agent** | A11y tree + screenshot + event stream + tools + memory | Best-case harness engineering |

**What the comparisons tell you:**
- C vs D → Does "beyond screenshots" actually help?
- B vs D → Is "taught workflows" or "general autonomy" the right product?
- A vs B → Does single-demo compilation add value over scripting?

---

## 7. Practical Tools (Don't Build From Scratch)

### Cua — macOS VM sandboxing
Open source (YC-backed). Provides macOS VMs on Apple Silicon at near-native speed via Apple Vz/Lume. MCP integration built in. Gives clean environment resets between eval runs without building VM infrastructure.
- [github.com/trycua/cua](https://github.com/trycua/cua)

### Screenpipe — screen + audio recording
MIT-licensed continuous local recorder with OCR and MCP server. Cross-platform. Provides the raw capture layer for the "record user → extract intent" pipeline.
- [github.com/mediar-ai/screenpipe](https://github.com/mediar-ai/screenpipe)

### AgentTrek — task suite seeding
Synthesizes agent trajectories from web tutorials at $0.55/trajectory. Can bootstrap the initial task suite cheaply.
- [github.com/xlang-ai/AgentTrek](https://github.com/xlang-ai/AgentTrek)

### Inspect AI + EvalView + DeepEval — layered eval stack
Recommended layering for this project:
1. **Inspect AI** (`pip install inspect-ai`) — primary harness runner. Agent-native, Docker sandboxing, 100+ pre-built evals. Maps to our architecture: Dataset=tasks, Solver=agent, Scorer=verification. [inspect.aisi.org.uk](https://inspect.aisi.org.uk/)
2. **EvalView** — regression detection in CI. Records golden baseline (tools called, params, sequence), diffs against new runs. Catches agent behavior shifts across model updates. Free, open source. [github.com/hidai25/eval-view](https://github.com/hidai25/eval-view)
3. **DeepEval** — pytest-native LLM-as-judge scoring. G-Eval, task completion, hallucination metrics. Use if programmatic verification isn't sufficient. [github.com/confident-ai/deepeval](https://github.com/confident-ai/deepeval)

Avoid hosted platforms (Braintrust, LangSmith) for PoC — no advantage over local stack.

### BrowserGym — web agent eval pattern
Gym-like environment abstraction for web tasks. The architectural pattern (env + agent loop + grader) transfers directly to desktop harness design.
- [github.com/ServiceNow/BrowserGym](https://github.com/ServiceNow/BrowserGym)

### GPA — demo-based replay
Records one macOS demonstration, compiles to structured workflow with readiness checks and FSM replay. Uses Sequential Monte Carlo for robustness under rescaling. Fully local, no VLM at runtime.
- [huggingface.co/papers/2604.01676](https://huggingface.co/papers/2604.01676)

---

## 8. Codex OAuth / Authentication

### Two auth paths exist

| Path | How it works | Best for |
|---|---|---|
| **API key** | Standard `OPENAI_API_KEY`, per-token billing | Automated eval harness, CI/CD |
| **ChatGPT OAuth** | Browser login → access token cached in `~/.codex/auth.json` | Interactive user sessions |

### Subscription tiers (as of April 2026)

| Plan | Cost | Codex Rate Limit |
|---|---|---|
| Plus | $20/mo | 45-225 messages/5hr window |
| Pro | $200/mo | 300-1,500 messages/5hr window |
| Business | Pay-as-you-go | Per-token billing, scalable |

As of April 2, 2026, pricing transitioned to token-based rates for Business/Enterprise.

**Critical constraint**: ChatGPT subscription tokens gate *plan quota*, not raw API access. Subscription billing is separate from API billing. For an automated eval harness, **API key is the correct path**.

Codex can run as an MCP server (`npx codex mcp-server`) for agent orchestration via the Agents SDK.

The Apps SDK / MCP path is the only route to leverage a user's ChatGPT subscription for tool execution — expose your tool server over HTTPS via tunnel (ngrok/Cloudflare Tunnel) and connect to ChatGPT.

---

## 9. Screen Recording → Structured Intent

### The pipeline (long-term vision)

```
Screen recording + voice → Frame extraction → A11y tree per frame →
Whisper transcription (aligned to frames) → VLM intent extraction →
Structured task YAML → Human review → Agent replay
```

### Available building blocks

| Tool | What it does | Limitation |
|---|---|---|
| **GPA** | Records demo, compiles to deterministic replay with readiness checks | macOS only, code may not be public yet |
| **Screenpipe** | Continuous screen+audio capture with OCR | Not structured intent — raw capture only |
| **SkillForge** | Browser recording → SKILL.md export | Browser-only, $9.99/mo |
| **Power Automate AI Recorder** | Voice narration + screen → automation flow | Windows only, proprietary |
| **Kairos** | No-code screen recording → automation, 70+ apps | Early access, commercial |
| **Google on-device intent extraction** | Small multimodal LLM, 2-stage (summarize screens → extract intent) | Research, not productized |

- Kairos ([kairos.computer](https://www.kairos.computer/)) — closest commercial product to the long-term vision
- Google EMNLP 2025 ([research.google](https://research.google/blog/small-models-big-results-achieving-superior-intent-extraction-through-decomposition/)) — relevant if on-device privacy is required

### Voice narration

Voice annotation resolves intent ambiguity that screenshots alone cannot ("I'm exporting, not printing"). Practical pipeline: ScreenCaptureKit + mic → Whisper (local via whisper.cpp) → WhisperX word-level timestamp alignment → pair narration with UI state.

**Recommendation**: Build voice as opt-in enhancement, not a requirement.

### What to do with recordings in the PoC

Do not treat a recording as executable truth.

For the PoC, use recordings to:

1. capture raw evidence,
2. derive a draft task definition,
3. identify ambiguous intent that may need clarification,
4. and create a reference trace for debugging or replay baselines.

This keeps the recording idea aligned with the long-term product intention without forcing the first implementation into a brittle video-replay design.

---

## 10. macOS Instrumentation (PoC Target)

### Observation sources

| Source | API | What it provides | Permission needed |
|---|---|---|---|
| **Accessibility tree** | AXUIElement, AXObserver | Element roles, names, bounds, actions | Accessibility trust (AXIsProcessTrustedWithOptions) |
| **Window metadata** | CGWindowListCopyWindowInfo | Window list, positions, focused app | None |
| **Screenshots** | ScreenCaptureKit | Pixel capture | Screen Recording permission |
| **File system events** | FSEvents API | Directory change notifications | Filesystem access |
| **Clipboard** | NSPasteboard | Pasteboard contents (treat as sensitive) | None |

### The AX coverage problem

Only 33% of macOS apps have full AX support (Screen2AX, Jul 2025). 18% of top 99 apps have *none*.

**Options when AX is missing:**
1. Screen2AX-style synthetic tree (vision + object detection → generated AX tree, 2.2x grounding improvement)
2. Screenshot + coordinate-based grounding (current default fallback)
3. App-specific automation (Playwright for browsers, AppleScript for scriptable apps)

Track `a11y_available: bool` per step. This is the first feasibility signal.

---

## 11. Known Risks and Unknown-Unknowns

### Critical risks

| Risk | Evidence | Mitigation |
|---|---|---|
| **Compounding error** | 95% per-step → 36% over 20 steps | Checkpointing, shorter workflows, readiness checks |
| **macOS AX gaps** | 33% full coverage | Synthetic AX tree fallback, explicit tracking |
| **Prompt injection** | 20% compliance rate on frontier models (OS-Harm) | Safety test slice, human-in-loop for high-risk actions |
| **Context degradation** | Performance drops driven by within-task memory failures (AndroTMem, 2026) | Subgoal-chunked memory (HiAgent), context pruning |

### Unknown-unknowns to probe

- **Timing/synchronization**: When is the UI "ready" after an action?
- **UI drift**: Window repositioning, DPI changes, theme switches between steps
- **Trial contamination**: Shared state between eval runs inflating/suppressing results
- **Cost scaling**: Does token cost per step increase as workflows lengthen?
- **Anti-bot detection**: Some apps/sites will block automation entirely
- **Focus stealing**: Notifications and OS dialogs interrupting agent execution

### PoC-to-production gaps (from practitioner reports, 2025-2026)

- Infinite loops: agents call tools without progress
- Cost explosions: $47+/conversation without token budgets
- Context overflow: information loss across long turns
- Clean-environment assumption: PoCs work in controlled environments; production means dirty state
- Missing monitoring: 54% of stalled scale attempts cite absent production monitoring

---

## 12. What NOT to Build Yet

The research explored many directions. For the PoC, explicitly defer:

- **Cross-OS support** (Windows UIA, Linux AT-SPI/Wayland) — macOS first
- **Full factorial comparison matrix** (27+ configurations) — start with 2-3 key comparisons
- **Custom ML models for grounding** — use VLM APIs for now
- **Production privacy/compliance framework** (NIST, GDPR) — minimal data handling for PoC
- **MCP server infrastructure** — test via existing tools (Cua, Codex MCP)
- **Deterministic replay compiler** (GPA-style FSM) — use as reference, don't rebuild
- **Voice capture pipeline** — defer to after basic screenshot+action loop works

One more boundary is worth being explicit about:

- **Do not start by building a large generic harness framework** — build the smallest system that can answer whether a hybrid harness materially outperforms screenshot-only control on short, repeatable, desktop-adjacent tasks

---

## 13. Recommended PoC Build Order

### Phase 1: Prove the loop works (3-5 days)
Build the simplest possible end-to-end flow:
- Playwright browser environment
- Screenshot observation (+ a11y tree where available)
- One model backend (Claude or GPT-4o)
- 3-5 simple browser tasks with programmatic verification
- Basic logging: screenshot per step, action taken, pass/fail

**Goal**: Can an agent complete simple browser tasks? What breaks?

### Phase 2: Add comparison dimensions (3-5 days)
- Add a11y-only and hybrid observation modes
- Add a second model backend
- Add deterministic replay baseline
- Expand to 10-12 tasks across difficulty levels
- Add failure classification (perception / planning / execution / context / environment)
- Track: success rate, step count, cost, semantic action ratio

**Goal**: Does observation format matter? Where does AI beat scripting?

### Phase 3: Desktop extension + stress tests (5-7 days)
- Add macOS desktop environment (via Cua or native)
- Add macOS-specific observation (AXUIElement, ScreenCaptureKit)
- Add chaos tests: popup injection, DPI change, focus stealing
- Add prompt injection test slice
- Auto-generate feasibility report from harness data

**Goal**: Is macOS desktop automation feasible? What are the binding constraints?

### Phase 4: Recording + intent extraction (if Phase 3 is positive)
- Build screen recording capture (ScreenCaptureKit)
- Build intent extraction from recorded screenshots + a11y trees
- Optional: add Whisper voice transcription
- Build task YAML generation from recordings
- Test: can a recorded workflow be replayed by an agent?

**Goal**: Can we close the loop from user recording → agent replay?

---

## Key References

### Benchmarks
- [OSWorld](https://os-world.github.io/) — canonical desktop benchmark (NeurIPS 2024)
- [OSWorld-MCP](https://arxiv.org/abs/2510.24563) — MCP tool invocation (Oct 2025)
- [OS-Harm](https://arxiv.org/abs/2506.14866) — safety benchmark (NeurIPS 2025)
- [ScreenSpot-Pro](https://arxiv.org/abs/2504.07981) — dense UI grounding (ICLR 2025)
- [Screen2AX](https://arxiv.org/abs/2507.16704) — macOS AX coverage gap (Jul 2025)
- [Windows Agent Arena](https://microsoft.github.io/WindowsAgentArena/) — Windows benchmark (ICML 2025)

### Architecture & Techniques
- [GPA](https://huggingface.co/papers/2604.01676) — demo-based deterministic replay (Apr 2026)
- [HiAgent](https://aclanthology.org/2025.acl-long.1575/) — hierarchical working memory (ACL 2025)
- [AndroTMem](https://arxiv.org/abs/2603.18429) — memory failure analysis (Mar 2026)
- [UFO/UFO2/UFO3](https://github.com/microsoft/UFO) — Microsoft desktop agent (MIT)

### Eval Guidance
- [Anthropic: Demystifying evals for agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) (Jan 2026)
- [OpenAI: Computer use guide](https://platform.openai.com/docs/guides/tools-computer-use)
- [OpenAI: Trace grading](https://developers.openai.com/api/docs/guides/trace-grading/)
- [Eval-Driven Development](https://arxiv.org/abs/2411.13768)

### Tools & Eval Frameworks
- [Cua](https://github.com/trycua/cua) — macOS VM sandboxing
- [Inspect AI](https://inspect.aisi.org.uk/) — agent eval framework
- [EvalView](https://github.com/hidai25/eval-view) — agent regression testing
- [DeepEval](https://github.com/confident-ai/deepeval) — pytest-native LLM eval
- [Screenpipe](https://github.com/mediar-ai/screenpipe) — local screen+audio recording
- [BrowserGym](https://github.com/ServiceNow/BrowserGym) — web agent eval pattern
- [AgentTrek](https://github.com/xlang-ai/AgentTrek) — task trajectory synthesis
- [Anthropic Bloom](https://github.com/safety-research/bloom) — behavioral eval framework

### Auth
- [Codex auth docs](https://developers.openai.com/codex/auth)
- [Codex as MCP server](https://developers.openai.com/codex/guides/agents-sdk)
- [OpenAI Apps SDK / MCP](https://developers.openai.com/apps-sdk/concepts/mcp-server)

### Additional
- [WorldGUI](https://arxiv.org/abs/2502.08047) — dynamic desktop benchmark (Feb 2025)
- [macapptree](https://github.com/MacPaw/macapptree) — macOS AX tree parser (Screen2AX)
- [Anthropic Computer Use demo](https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo)
- [Kairos](https://www.kairos.computer/) — commercial record-to-automation
- [Google on-device intent extraction](https://research.google/blog/small-models-big-results-achieving-superior-intent-extraction-through-decomposition/)

---

## Sources of Truth

Key dependencies and their drift risk. Verify before building on them.

| Area | Canonical Source | Drift Risk |
|---|---|---|
| macOS AX API | [Apple AXUIElement docs](https://developer.apple.com/documentation/applicationservices/axuielement) | Medium |
| macOS screen capture | [ScreenCaptureKit docs](https://developer.apple.com/documentation/screencapturekit) | Low |
| OSWorld leaderboard | [os-world.github.io](https://os-world.github.io/) | High — SOTA shifts monthly |
| Codex OAuth flow | [developers.openai.com/codex/auth](https://developers.openai.com/codex/auth) | High — OpenAI changes fast |
| MCP protocol spec | [modelcontextprotocol.io](https://modelcontextprotocol.io/) | Medium |
| Cua SDK | [github.com/trycua/cua](https://github.com/trycua/cua) | High — YC startup, may change |
| GPA paper/code | [huggingface.co/papers/2604.01676](https://huggingface.co/papers/2604.01676) | High — code may not be public yet |
| Anthropic eval guidance | [anthropic.com/engineering/demystifying-evals-for-ai-agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | Low |
