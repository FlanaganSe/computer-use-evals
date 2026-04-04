I’d build this as a **hybrid desktop eval harness**, not as a screenshot benchmark and not as a one-off demo script. The key separation is: **environment adapter, agent scaffold, model adapter, and grader**. That is what makes the result reusable in a production app instead of “a clever experiment.” Anthropic’s current eval framing is the right mental model here: tasks, trials, transcripts/traces, outcomes, graders, and the harness are separate objects. OpenAI’s current eval guidance points the same way with task-specific datasets, graders, trace grading, and continuous evaluation. ([Anthropic][1])

The other big conclusion is that **harness engineering matters more than intention labeling alone**. Intention categories are useful, but mostly as a normalized action vocabulary. The hard part is what the agent can *see*, what it can *do semantically*, what state the harness remembers, and how runs are graded. OpenAI’s computer-use loop is explicitly harness-centric: the model returns actions, the harness executes them, captures the updated screen, and repeats. ([OpenAI Platform][2])

## 1) Build one local sidecar with a richer observation model

For a quick POC that is still product-usable, I would use a **local desktop sidecar** exposing a typed API over localhost. The shell can be Tauri/Electron/native; the important part is the sidecar. Its `observe()` output should not be screenshot-only.

Use a fused observation bundle:

* full screenshot
* semantic UI tree when available
* active window stack and focus
* event stream since last step
* browser-specific DOM/AX snapshot when in Chromium
* artifact state like downloads, clipboard, temp files, current directory

That is feasible with the official platform layers already available today. Windows UI Automation provides programmatic access to most desktop UI elements and event notifications. macOS exposes accessibility objects through `AXUIElement` and lets you subscribe to notifications via `AXObserver`. Linux desktop accessibility uses AT-SPI objects, actions, and event signals over D-Bus. In Chromium, the CDP Accessibility domain can return the full accessibility tree and keep `AXNodeId`s stable across calls, and Chrome’s `chrome.debugger` API exposes the Accessibility domain to extensions. ([Microsoft Learn][3])

That semantic layer matters because screenshot-only state is exactly where long multi-step jobs start to rot. OSWorld reports that longer **text** history helps while screenshot-only history does not scale the same way; later memory papers point to within-task memory failure as the main long-horizon problem, and continuous visual memory outscales text-only memory as trajectories get longer. ([OSWorld][4])

A concrete interface can stay small:

```ts
type Observation = {
  screenshotPng: bytes
  windows: WindowMeta[]
  focusedWindowId: string
  uiTree?: UiTree          // UIA / AX / AT-SPI / CDP AX
  browserDom?: DomSnapshot // browser only
  events: UiEvent[]
  artifacts: ArtifactState // downloads, clipboard, files, etc.
}

type Action =
  | { kind: "invoke", targetRef: string }
  | { kind: "set_text", targetRef: string, text: string }
  | { kind: "select", targetRef: string, value: string }
  | { kind: "hotkey", keys: string[] }
  | { kind: "click_point", x: number, y: number }
  | { kind: "drag", from: Point, to: Point }
  | { kind: "scroll", targetRef?: string, delta: number }
  | { kind: "tool", name: string, args: object }
  | { kind: "wait", until?: Condition, ms?: number }
```

## 2) Use an action ladder, not one flat action space

I would give every model the same 4-layer action ladder:

1. **Semantic actions**: `invoke`, `set_text`, `select`, `focus`, `read_value`
2. **Grounded visual actions**: click/drag on a returned bbox or target region
3. **Raw fallback actions**: coordinate click, drag path, hotkey
4. **Non-GUI tools**: file ops, downloads, clipboard, browser metadata, maybe terminal

This is where intention labels help: normalize actions into a small ontology such as `open`, `locate`, `invoke`, `input`, `transfer`, `confirm`, `wait`, `recover`. But the real gain is not the label; it is that the harness can execute **semantic-first** and fall back only when needed. GPA is the clearest research signal here: it compiles one demo into structured workflow steps, variable bindings, local UI subgraphs, readiness checks, and an FSM for deterministic replay. OSWorld-MCP adds a second signal: tool invocation should be treated as first-class, not as cheating, because mixed GUI/tool execution is often the right abstraction for real work.  ([OpenReview][5])

The single best feasibility metric I’d add is **semantic action ratio**: what fraction of successful steps were executed semantically rather than via pixel fallback. If that number is low in your target apps, you are learning that the desktop wedge is still too screenshot-dependent.

## 3) Record more than screenshots, especially in browsers

For browser tasks, you already have a cleaner path than native desktop. Chromium’s AX tree gives you structured UI state, and Playwright traces give you a high-quality audit/debug artifact. Playwright’s Trace Viewer includes screenshot filmstrips, DOM snapshots, and network requests, which makes it better than “video only” for debugging failures and building graders. That fits your browser-first memo well: keep deterministic replay as a baseline rather than throwing it away. ([Playwright][6]) 

For desktop-native tasks, mirror that discipline: every step should log the observation bundle, chosen action layer, target ref if semantic, fallback reason if not, retries, and postcondition result.

## 4) Run four baselines on the same task suite

This is the fastest way to answer your actual feasibility question.

**Baseline A: deterministic semantic/scripted**
A no-LLM executor using UIA/AX/AT-SPI/CDP/Playwright where possible.

**Baseline B: one-demo compiled executor**
A GPA-style runner built from one demonstration plus variable injection and readiness checks. 

**Baseline C: screenshot-only agent**
Plain computer-use loop with screenshot observation and raw actions.

**Baseline D: hybrid agent**
Same model family, but with semantic tree + event stream + tools + memory anchors.

That ablation tells you almost everything:

* **C vs D** tells you whether “beyond screenshots” actually moves the needle.
* **B vs D** tells you whether your wedge is “taught workflows” or “general autonomy.”
* **A vs B** tells you how much value single-demo compilation adds over pure scripting.

This also answers the packaging question in your memo: if D materially beats C, the browser-only path is too narrow and a **browser recorder + desktop sidecar** becomes the right product direction. 

One practical note: OpenAI’s eval platform can evaluate external models and custom endpoints, but **tool calls are currently not supported** in external-model evals. So for multi-step desktop agents, keep the action loop in **your own harness** and use OpenAI eval tooling for datasets, offline comparisons, or trace grading—not as the primary runtime. ([OpenAI Developers][7])

## 5) Define tasks by outcome, not by path

For cross-model and cross-OS fairness, each task should specify:

* initial state
* user goal
* allowed tools/capabilities
* success checker
* partial-credit checkers
* risk tier
* OS/app variants
* reference solution

Anthropic’s guidance is exactly right here: outcome grading is usually better than checking for one exact sequence of tool calls, because good agents often find valid paths you didn’t anticipate. They also recommend a known-good reference solution and a clean environment per trial so you can tell agent failure from harness noise. ([Anthropic][1])

I’d start with **12 canonical tasks** and run them as capability-equivalent variants across OSes. Not pixel-identical, capability-equivalent.

The families I’d include are:

* **High-DPI / small-target grounding**
  Include professional or dense UIs, because ScreenSpot-Pro shows grounding gets much harder there, and UI-Vision shows pro software plus drag/drop remain weak spots. ([arXiv][8])

* **Browser + native file boundary**
  Download in browser, rename/move locally, upload through native picker. This is where “browser-only” products usually break in the real world. Your memo already flagged local file ops as necessary. 

* **Desktop editing workflows**
  Spreadsheet/doc edit, export, cross-app copy/paste, drag/drop. UI-Vision was built precisely because desktop software remains underexplored and hard. ([arXiv][9])

* **Long-horizon memory-critical workflows**
  10–30 step jobs where a value chosen early must be used later. That is where AndroTMem says failures are mainly memory failures, not isolated perception slips. ([arXiv][10])

* **Tool-choice workflows**
  Include steps where the agent can either use GUI or a tool. OSWorld-MCP exists because this choice itself is a measurable capability. ([OpenReview][5])

* **Safety and adversarial tasks**
  OS-Harm shows prompt injection, misuse, and unsafe actions need their own benchmark slice, not a footnote. ([arXiv][11])

## 6) Grade more than task success

Your minimum dashboard should include:

* task success
* stage/subgoal success
* pass@3 or pass@5 across repeated trials
* step count and wall-clock time
* **human-step ratio**
* semantic action ratio
* pixel-fallback rate
* repair rate under perturbation
* context-rot slope vs step index
* safety violations
* cost per successful run

Efficiency belongs in the core gate, not the appendix. OSWorld-Human found that even strong agents can take about **1.4–2.7×** the necessary number of steps, which is a major product risk for latency and cost. ([arXiv][12])

Trace grading is especially valuable here because you want to know *why* a run failed: bad grounding, bad memory, wrong tool choice, unsafe action, or harness mismatch. OpenAI’s trace grading guidance is useful for this exact purpose, and Anthropic’s current write-up is also good on combining code-based, model-based, and occasional human graders. ([OpenAI Developers][13])

## 7) Handle the likely unknown-unknowns up front

The big ones I would explicitly probe in the POC are:

* **Accessibility coverage gaps**
  Screen2AX found only 33% full accessibility support on macOS in its study, so missing semantics is not an edge case. ([arXiv][14])

* **UI drift, rescaling, theme, and layout variance**
  GPA is strong precisely because it models rescaling, neighbor context, readiness, and bounded retries. 

* **Prompt injection and unsafe actions**
  OS-Harm shows this must be measured directly. ([arXiv][11])

* **Trial contamination**
  Clean trial isolation matters; otherwise shared state can inflate or suppress performance artificially. ([Anthropic][1])

* **Long-horizon memory collapse**
  Measure failure probability as workflows lengthen; do not rely on raw transcript replay alone. ([OSWorld][4])

## 8) Cross-OS and OpenAI subscriptions

Cross-OS is doable, but I would define tasks at the **goal/outcome** level and implement OS-specific adapters underneath. OSWorld reports strong correlation across OS performance, which is encouraging, but you still need separate adapters because the semantic layers and permissions differ. ([OSWorld][4])

On OpenAI billing, I would narrow your requirement from the memo. For a standalone local desktop app, **do not plan around the user’s ChatGPT subscription covering API inference**. OpenAI’s official help docs say ChatGPT and API billing are separate; Plus, Pro, and Business do not include API usage. The official route that *does* leverage the user’s ChatGPT account is the **Apps SDK / MCP** path, where you expose an MCP server and connect it to ChatGPT in developer mode. Apps SDK apps are built on MCP, and local development works by exposing your local server over HTTPS with a tunnel like ngrok or Cloudflare Tunnel. That is a good companion surface for testing the same tool plane inside ChatGPT, but it is not a substitute for a native desktop runtime. ([OpenAI Help Center][15])

My concrete recommendation is:

Build a **browser recorder + desktop sidecar** with one unified task schema. Run the 4 baselines above on a 12-task suite with repeated trials and outcome-based graders. If the **hybrid semantic+vision+tools** agent clearly beats screenshot-only, and your **semantic action ratio** is high enough in target apps, desktop expansion is plausible. If the **one-demo compiled executor** dominates, the commercial wedge is probably still “taught workflows with approval gates,” not general desktop autonomy. That is also the direction your current product memo most naturally supports.  

The next concrete artifact I’d produce from this is a task YAML schema plus the scorer spec.

[1]: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents "https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents"
[2]: https://platform.openai.com/docs/guides/tools-computer-use "https://platform.openai.com/docs/guides/tools-computer-use"
[3]: https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32?utm_source=chatgpt.com "UI Automation - Win32 apps"
[4]: https://os-world.github.io/ "https://os-world.github.io/"
[5]: https://openreview.net/forum?id=rceD6wwt4B&utm_source=chatgpt.com "Benchmarking MCP Tool Invocation In Computer-Use Agents"
[6]: https://playwright.dev/docs/best-practices "https://playwright.dev/docs/best-practices"
[7]: https://developers.openai.com/api/docs/guides/external-models/ "https://developers.openai.com/api/docs/guides/external-models/"
[8]: https://arxiv.org/abs/2504.07981 "https://arxiv.org/abs/2504.07981"
[9]: https://arxiv.org/abs/2503.15661?utm_source=chatgpt.com "UI-Vision: A Desktop-centric GUI Benchmark for Visual Perception and Interaction"
[10]: https://arxiv.org/abs/2603.18429 "https://arxiv.org/abs/2603.18429"
[11]: https://arxiv.org/abs/2506.14866 "https://arxiv.org/abs/2506.14866"
[12]: https://arxiv.org/abs/2506.16042 "https://arxiv.org/abs/2506.16042"
[13]: https://developers.openai.com/api/docs/guides/trace-grading/ "https://developers.openai.com/api/docs/guides/trace-grading/"
[14]: https://arxiv.org/abs/2507.16704?utm_source=chatgpt.com "Screen2AX: Vision-Based Approach for Automatic macOS Accessibility Generation"
[15]: https://help.openai.com/en/articles/8156019-how-can-i-move-my-chatgpt-subscription-to-the-api?utm_source=chatgpt.com "How can I move my ChatGPT subscription to the API?"
