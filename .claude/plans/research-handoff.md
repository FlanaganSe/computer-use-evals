# Research Handoff: Realigning the Eval Harness with Desktop Agent Best Practices (April 2026)

## What this document is

This is a research task, not an implementation task. Do not write code. Your deliverable is a structured research document that will directly inform the next phase of this project's development.

Take your time. Read deeply. Verify claims. Be skeptical of anything older than 6 months — this field moves fast and April 2026 is materially different from October 2025. Prioritize what is true and working RIGHT NOW over what was promising in a paper 8 months ago.

---

## The concern that motivates this research

We have a working eval harness (~3,200 lines of Python) that was built milestone by milestone over M1-M6. It runs tasks, captures evidence, grades outcomes, generates reports. The infrastructure is sound. But **we may be testing the wrong things.**

The harness was originally designed around two tracks:

1. **Track A (OpenAI computer-use)**: Send screenshots to `computer-use-preview` via the Responses API → get pixel-coordinate actions back. Expensive. Slow. The model looks at images every step.
2. **Track B (Codex subscription)**: Send ARIA state to Codex CLI → get semantic browser actions back. Cheaper. Faster. But browser-only.

Recent research — particularly the consolidated research at `/Users/seanflanagan/caps/aa-research/docs/desktop/published-research/consolidated-research.md` — strongly suggests that:

- **Computer-use models (screenshot → pixel coordinates) are the wrong default for a product.** They're expensive, slow, and less reliable than structured-state approaches. They exist for benchmarking and for the narrow case where no structured state is available.
- **Accessibility-first execution is the highest-leverage architecture choice.** When you can use AX trees (macOS), UIA (Windows), AT-SPI (Linux), or DOM/ARIA (browser), you should. Success improves by +67%, steps decrease by 43%, and 61% of tasks complete in a single LLM call.
- **The right agent architecture is a hierarchy**: structured handles first → regular LLM with structured state → vision only as fallback → expensive model only on escalation.
- **Near-100% accuracy requires understanding intent, not just executing clicks.** Intent inference from recordings alone is only 44-55% accurate. Adding structured context improves it by +50 percentage points.

If this is true, then Track A (computer-use) was a research comparison tool, not the production path. And Track B (Codex/ARIA) is the right *pattern* but scoped too narrowly (browser-only, tied to one CLI tool).

**The question is: what should we actually be testing, building toward, and proving feasible with this harness?**

---

## What exists in the codebase (read these files)

Before researching externally, understand what's built. The architecture is sound — the abstractions are right — but the concrete adapters and task formats may need to evolve.

### Core abstractions (these are good)

| File | Lines | What it does |
|---|---|---|
| `src/harness/types.py` | 244 | Adapter Protocol, Environment Protocol, Task model, Observation/Action types, Trace/StepRecord |
| `src/harness/runner.py` | 260 | Core run loop: setup → observe → decide → execute → grade → write artifacts |
| `src/harness/reporting.py` | 387 | Single-run reports, comparison tables, detailed metrics |
| `src/harness/graders.py` | 199 | Programmatic graders: file_exists, file_contains, form_submitted |
| `src/harness/failures.py` | 28 | Failure taxonomy: PERCEPTION, PLANNING, EXECUTION, CONTEXT, ENVIRONMENT, TOOL_CHOICE, HARNESS |

### Adapters (these may need rethinking)

| File | Lines | Approach |
|---|---|---|
| `src/harness/adapters/openai_cu.py` | 325 | Screenshot → OpenAI computer-use-preview → pixel coordinates. Expensive, slow. |
| `src/harness/adapters/codex_subscription.py` | 260 | ARIA state → Codex CLI → semantic browser selectors. Right pattern, browser-only. |
| `src/harness/adapters/deterministic.py` | 126 | Hardcoded scripts. Free. Validates harness infrastructure. |

### Environments (these are directionally right)

| File | Lines | What it does |
|---|---|---|
| `src/harness/environments/browser.py` | 168 | Playwright. Screenshots + `ariaSnapshot()`. Viewport 1280x720. |
| `src/harness/environments/macos.py` | 342 | screencapture + AXUIElement + pyautogui. Permission checks. AX tree serialization. |

### Capture pipeline (M6-M7)

| File | Lines | What it does |
|---|---|---|
| `src/harness/capture.py` | 351 | Screenshots at intervals + optional ARIA + optional CGEventTap input events |
| `src/harness/intent_extract.py` | 374 | Sample frames → GPT-4o → draft Task YAML |

### Tasks

```
tasks/browser_download/task.yaml      — download a file via browser
tasks/browser_form_fill/task.yaml     — fill and submit a form
tasks/desktop_textedit_save/task.yaml — create+save a file in TextEdit
tasks/my-test-task/task.yaml          — cross-app: Zillow form + TextEdit (user-created)
```

### Key observations about the current state

1. **The Adapter Protocol is flexible enough.** `observation_request() → ObservationType` and `decide(observation, task) → list[Action]` can accommodate any approach — structured state, vision, hybrid. The protocol doesn't need to change.

2. **The Environment Protocol is well-abstracted.** `setup()`, `collect_observation()`, `execute_action()`, `teardown()`. Adding Windows or Linux environments implements the same interface.

3. **The `MacOSDesktopEnvironment` already captures AX trees.** The structured state is there — but no adapter uses it for desktop decision-making. The Codex adapter reads ARIA (browser), the OpenAI adapter reads screenshots. Nobody reads the AX tree and makes decisions from it.

4. **The Task model is flat.** No milestones, branches, recovery paths, skills, or typed sub-steps. The research says this is too weak for real workflows.

5. **The graders are trivial.** `file_exists`, `file_contains`, `form_submitted`. The research says verification needs milestone checks, step-level critics, and independent verification separate from the executor.

6. **The capture pipeline captures screenshots + input events.** The research says the minimum recorder should capture: continuous video OR high-frequency clips, cursor trajectory + timing, keyboard + scroll events, accessibility tree snapshots or deltas, active window + focus changes, optional user narration.

7. **Intent extraction sends screenshots to GPT-4o.** The research says intent prediction without structured context is 44-55% accurate. Adding structured context improves it by +50pp.

---

## The consolidated research (read this thoroughly)

**Read the full document at:** `/Users/seanflanagan/caps/aa-research/docs/desktop/published-research/consolidated-research.md`

This is a curated, opinionated synthesis of the best late-2025 and 2026 desktop agent research, filtered specifically for product relevance. It was written April 4, 2026. It contains:

- Selection criteria for which research matters
- Executive judgment on the product direction
- 6 consensus findings across the field
- A ranked table of the 21 most product-relevant papers
- Supporting papers for representation, execution, robustness, benchmarks
- Hard numbers that calibrate expectations
- A "what to borrow now" section
- A minimal research-backed product architecture
- Industry context (Anthropic Claude Cowork/Dispatch, Sola, etc.)

**This document is your starting point, not your ending point.** It tells you what the field said as of early April 2026. Your job is to go deeper, verify, update, and discover what's changed or emerged since.

---

## Research questions — answer these

### Q1: What is the actual best adapter architecture for April 2026?

The current harness has "screenshot → computer-use model → pixel actions" and "ARIA → Codex CLI → semantic selectors." The research says neither is the right primary path.

Research specifically:
- **What does a "structured state → regular LLM → semantic actions" adapter actually look like for desktop tasks?** Not browser (that's solved) — desktop. Where the structured state is an AX tree, not ARIA.
- **Which specific models perform best when given an AX tree / accessibility dump and asked to return the next action?** Claude Sonnet 4.6? GPT-4o? Something smaller and cheaper? What about Claude Haiku 4.5 or GPT-4o-mini for the 78% of easy steps?
- **What prompt format works best for feeding accessibility trees to LLMs?** Raw serialized text? JSON? Filtered/pruned trees? The current `_serialize_ax_element()` in `macos.py` produces indented text — is that the right format?
- **What action format should the adapter return for desktop?** The current `ActionType` enum has CLICK, TYPE, PRESS, SCROLL, etc. with either pixel coordinates or selectors. What does a "semantic desktop action" look like? "Click the Save button"? "Click the element with AXRole=AXButton, AXTitle=Save"?
- **How does the adaptive VLM routing paper (2603.12823) work in practice?** Can we implement difficulty-based routing where easy steps go to a cheap model and hard steps escalate?

### Q2: What desktop agent tools and frameworks exist RIGHT NOW in April 2026?

Do not rely on what papers described. Verify what is actually installable, maintained, and functional today.

Research specifically:
- **OpenCUA** (2508.09123) — is it a usable framework or just a paper? Can it be installed? Does it have a recorder? Does it work on macOS?
- **Browser Use 2.0** — DOM-first, vision fallback. Is the architecture transferable to desktop?
- **UFO2/UFO3** (Microsoft) — Windows-only? Is the hybrid UIA + OmniParser approach usable outside Windows?
- **macLLM** (github.com/appenz/macLLM) — is it maintained? What does it do?
- **Simular.ai** — commercial macOS agent. What's their approach?
- **pyax** (github.com/eeejay/pyax) — Python accessibility library. Better than raw pyobjc for AX trees?
- **Screen2AX** (MacPaw) — generates synthetic AX trees from screenshots. Is it released? Usable?
- **GPA** (2604.01676) — deterministic replay from a single demo. Is there code? Is it usable?
- **Playwright MCP / browser-use MCP** — any MCP-based tools that give agents clean access to desktop or browser state?
- **What NEW tools/frameworks have appeared in March-April 2026 that aren't in the consolidated research?** Search broadly. This field is moving weekly.

### Q3: What is the right task/workflow representation?

The current Task model is a flat YAML with a goal description, variables, preconditions, and a single verification check. The research says real workflows need milestones, branches, recovery paths, typed parameters, and reusable skills.

Research specifically:
- **What does CUA-Skill's (2601.21123) parameterized skill format look like?** Is it directly usable?
- **What does AgentRR's (2505.17716) multi-level abstraction (actions → procedures → intent) look like concretely?**
- **What does TreeCUA's (2602.09662) branchable tree/graph trace format look like?**
- **What does ANCHOR's (2602.07153) branch synthesis approach look like?**
- **What is the minimum viable upgrade from a flat Task YAML to something that supports milestones and fallbacks?** Not the full-blown format — what's the smallest useful step?

### Q4: What verification/grading approaches actually work?

The current graders check filesystem artifacts. The research says that's not enough.

Research specifically:
- **SpecOps (2603.10268)** — flow regression testing. What does this look like in practice?
- **OS-Themis (2603.19191)** — milestone-based reward/critic. How does it work?
- **OS-Oracle (2512.16295)** — step-level critic. What's the architecture?
- **Video-Based Reward Modeling (2603.10178)** — judge replay success from screen evidence. How?
- **What does a practical milestone-based verifier look like for a PoC?** Not a research system — something we could build in a few hundred lines.

### Q5: What is the right recording format for the capture pipeline?

The current pipeline captures: screenshots at intervals + optional ARIA state + optional CGEventTap input events. The research says that's too sparse.

Research specifically:
- **CUA-Suite (2603.24440)** says continuous video is the right recording primitive. What does that mean practically? Screen recording at 30fps? 5fps? How much storage?
- **ShowUI-Aloha (2601.07181)** captures "screen video alongside precise user interactions." What is their data format?
- **OpenCUA (2508.09123)** has a "cross-platform recorder." What does it capture? What's the data model?
- **What does "accessibility tree deltas" mean in practice?** Snapshot the full tree every N seconds? Diff between consecutive snapshots?
- **Is CGEventTap (macOS input event capture) the right approach or are there better/newer tools?**

### Q6: What does cross-platform actually look like?

macOS first, but Windows and Linux matter. The Environment Protocol already isolates platform-specific code.

Research specifically:
- **What is the actual state of accessibility APIs across platforms?**
  - macOS: AXUIElement — ~33% of apps have full support
  - Windows: UI Automation (UIA) — what's the actual coverage?
  - Linux: AT-SPI — what's the actual coverage?
- **What Python libraries exist for each?** pyobjc for macOS, pywinauto/uiautomation for Windows, pyatspi for Linux? Are there better options?
- **Is there a unified cross-platform accessibility abstraction?** Or does each platform need its own Environment implementation?
- **What does UFO2's hybrid UIA + OmniParser approach teach us about Windows specifically?**

### Q7: What does "near-100% accuracy" actually require?

The best agents hit ~72% on OSWorld (Anthropic), ~25% with open-source models. The user wants near-100%.

Research specifically:
- **What conditions make near-100% achievable?** Stable UIs? Single-app? Known starting states? Deterministic replay with agent fallback?
- **GPA claims higher success than Gemini 3 Pro CUA with 10x speed.** What's their actual accuracy? On what tasks?
- **"Are LLM Agents the New RPA?" (2509.04198)** says RPA wins on reliability in stable environments. Does this mean deterministic replay (like traditional RPA) is the right default, with LLM agents only for adaptation?
- **What is the error distribution?** Of the failures that remain, what percentage are: perception failures, planning failures, execution failures, environment failures? Where does the improvement come from?
- **What does the DMI paper's finding that "61% of successful tasks completed with a single LLM call" mean for architecture?** If most tasks are simple enough for one call, what makes the other 39% hard?

### Q8: What is the actual state of models in April 2026?

Models matter. The right model at the right tier makes or breaks cost and accuracy.

Research specifically:
- **What are the current frontier models and their capabilities for structured reasoning over UI state?** Claude Opus 4.6, Claude Sonnet 4.6, GPT-4o, GPT-4.5, Gemini 3 Pro — which is best for "read this accessibility tree, decide what to do"?
- **What are the current cheap/fast models suitable for easy steps?** Claude Haiku 4.5, GPT-4o-mini, Gemini Flash — which handles routine UI navigation reliably?
- **What are the current vision models?** For the fallback path when structured state isn't available.
- **Are there any specialized models for UI/GUI tasks?** UI-TARS? Fine-tuned variants? Anything new in March-April 2026?
- **What are the current costs per token/step for each tier?** We need actual numbers to evaluate the routing strategy.

---

## Papers to dig deep into

These are from the consolidated research. Don't just read abstracts — read the architecture sections, the results, the limitations. Understand what they actually built and what actually worked.

**Tier 1 — Read fully, these directly inform the product architecture:**

1. [From Imperative to Declarative (DMI)](https://arxiv.org/abs/2510.04607) — Accessibility-backed declarative primitives. The +67% / -43.5% numbers. How it works.
2. [GPA: GUI Process Automation](https://arxiv.org/abs/2604.01676) — Deterministic replay from a single demo. The closest to the product vision. April 2026.
3. [ShowUI-Aloha](https://arxiv.org/abs/2601.07181) — Recorder → learner → planner → executor. The reference decomposition.
4. [AgentRR: Record & Replay](https://arxiv.org/abs/2505.17716) — Multi-level abstraction + check functions. The formalized product paradigm.
5. [Adaptive VLM Routing](https://arxiv.org/abs/2603.12823) — 78% cost reduction through difficulty-based routing. Practical.

**Tier 2 — Read for specific insights:**

6. [CUA-Suite](https://arxiv.org/abs/2603.24440) — Recording format: why continuous video matters.
7. [GUIDE benchmark](https://arxiv.org/abs/2603.25864) — Intent understanding is the bottleneck. The 44-55% → +50pp finding.
8. [OpenCUA](https://arxiv.org/abs/2508.09123) — Cross-platform recorder. Data pipeline. What 3 OSes?
9. [CUA-Skill](https://arxiv.org/abs/2601.21123) — Parameterized reusable skills from demonstrations.
10. [Are LLM Agents the New RPA?](https://arxiv.org/abs/2509.04198) — When deterministic beats LLM. Hybrid validation.

**Tier 3 — Skim for specific details if relevant:**

11. [SpecOps](https://arxiv.org/abs/2603.10268) — Flow regression testing.
12. [OS-Oracle](https://arxiv.org/abs/2512.16295) — Step-level critic design.
13. [ANCHOR](https://arxiv.org/abs/2602.07153) — Branch synthesis from recordings.
14. [TreeCUA](https://arxiv.org/abs/2602.09662) — Branchable tree/graph trace format.
15. [Screen2AX](https://arxiv.org/abs/2507.16704) — Synthetic accessibility from screenshots. 77% F1 on AX reconstruction.

---

## What to search for beyond the papers

The papers are necessary but not sufficient. The field is moving weekly. Search for:

- **New tools/frameworks released March-April 2026** for desktop automation, agent frameworks, accessibility tooling
- **New model capabilities** — has any model added specific UI/desktop understanding capabilities since the papers were written?
- **Production deployments** — what are companies actually shipping? Sola.ai, Anthropic Cowork/Dispatch, Microsoft Copilot Actions, Google Project Mariner — what do their architectures tell us?
- **MCP servers for desktop automation** — are there MCP tools that give agents clean access to accessibility trees, window management, etc.?
- **GitHub repos with recent activity** — tools that are actively maintained, not just paper artifacts
- **Community consensus** — what do practitioners (not just researchers) say works? HN, Reddit, Discord communities for agent development

---

## Critical mindset

As you research, maintain these skepticisms:

1. **Paper results don't equal product results.** A paper achieving 72% on a benchmark doesn't mean you'll get 72% in production. Benchmarks are controlled; real desktops are messy.

2. **"State of the art" changes monthly.** Something that was SOTA in January 2026 may already be superseded. Check dates. Verify recency.

3. **Open-source ≠ maintained.** A GitHub repo with a great README but no commits in 3 months is dead. Check activity.

4. **Cost matters as much as accuracy.** A system that achieves 95% accuracy at $5/task is worse than one that achieves 90% at $0.05/task for most use cases.

5. **Cross-platform claims need verification.** "Works on macOS, Windows, and Linux" often means "we tested on Ubuntu once." Check actual platform support.

6. **The accessibility coverage problem is real.** Only 33% of macOS apps have full AX support. Any architecture that assumes universal accessibility will fail on 2/3 of real-world software. The fallback path is not optional.

7. **This is a PoC, not a product.** Don't recommend building something that requires 6 months of engineering. Recommend what's feasible in days to weeks that proves the right architectural direction.

---

## Deliverable format

Write your findings to `.claude/plans/research-findings.md` (or a similarly clear location). Structure it as:

### Part 1: Current State Assessment
- What the harness does well (keep these)
- What's misaligned with research best practices (change these)
- What's missing entirely (add these)

### Part 2: Research Findings by Question
- Answer each of Q1-Q8 with specific, grounded findings
- For each finding: what the evidence says, what tools/options exist, what's feasible for a PoC

### Part 3: Recommended Architecture Evolution
- What the adapter layer should look like
- What the task format should evolve toward
- What the verification layer should look like
- What the capture pipeline should capture
- What stays, what changes, what's new

### Part 4: Tool and Framework Landscape (April 2026)
- What's available, maintained, and usable RIGHT NOW
- Cost/capability comparison
- What we can integrate vs build

### Part 5: Concrete Next Steps
- Ranked list of changes by impact and feasibility
- What to build first
- What to defer
- What experiments would prove the most

### Part 6: Open Questions and Risks
- What we still don't know
- What could go wrong
- What assumptions need testing

---

## What this research enables

This project is NOT the product. It is a proof-of-concept that shows what is feasible and informs how the actual product should be built. The research you produce should help us:

1. **Decide whether to keep, modify, or replace the current adapter tracks.** Is openai_cu worth keeping as a comparison tool? Should the Codex adapter be generalized? Should we build a new primary adapter?

2. **Decide what the right task format is.** Flat YAML? YAML with milestones? Something from the CUA-Skill or AgentRR literature?

3. **Decide what verification should look like.** Trivial filesystem checks? Milestone-based? Step-level critics?

4. **Decide what the capture pipeline should capture.** Screenshots + events? Continuous video? AX tree deltas?

5. **Understand what's actually achievable.** Not in theory — in practice, with available tools, in April 2026, for a PoC.

6. **Avoid building on yesterday's best practices.** The field has moved. We need to know where it is NOW.

The goal is: **an eval harness that proves the feasibility of structured-state-first, accessibility-backed desktop agent automation, using the best tools and models available in April 2026, that directly informs a product capable of near-100% accuracy on real desktop workflows.**

That's the bar. The research should tell us whether it's achievable and exactly how to get there.
