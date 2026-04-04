# Research: OpenAI Responses API Computer-Use, Codex CLI/MCP, and Pricing

> Researcher: Claude Sonnet 4.6 | Date: 2026-04-04

---

## 1. Current State

### What exists in this repo

This project is a desktop agent eval harness (PoC phase). Prior research (`docs/research-consolidated.md`) has already established the architecture and build order. This document adds detail on:

- The OpenAI `computer-use-preview` model and Responses API tool
- The complete agent loop implementation
- Codex CLI as an MCP server
- Pricing for API vs. subscription paths
- Comparison to Anthropic computer use

---

## 2. OpenAI Computer-Use via the Responses API

### Model

The dedicated model is **`computer-use-preview`** (also referred to as `gpt-5.4` in Azure docs). It is trained specifically for visual UI interaction. It requires the Responses API — the Chat Completions API does not support the `computer` tool.

Access is currently gated: registration required at https://aka.ms/OAI/gpt54access for Azure; direct API access via waitlist.

### Tool declaration

```python
response = client.responses.create(
    model="computer-use-preview",
    tools=[{
        "type": "computer_use_preview",
        "display_width": 1024,
        "display_height": 768,
        "environment": "browser"   # also: "mac", "windows", "ubuntu"
    }],
    input=[{
        "role": "user",
        "content": [{"type": "text", "text": "Check the latest news on bing.com."}]
    }],
    reasoning={"summary": "concise"},
    truncation="auto"
)
```

Recommended display resolution: 1440x900 or 1600x900 for best click accuracy.

### Response structure

The model returns a `computer_call` item in `response.output`:

```json
{
  "type": "computer_call",
  "id": "cu_068b...",
  "call_id": "call_4y94...",
  "actions": [
    {"type": "screenshot"},
    {"type": "click", "button": "left", "x": 405, "y": 157}
  ],
  "pending_safety_checks": [],
  "status": "completed"
}
```

Key difference from Anthropic: **`actions` is a batched array**. OpenAI batches multiple actions per turn; Anthropic returns one action per turn. This means the harness must iterate over `computer_call.actions[]` and execute all of them before sending the next screenshot.

---

## 3. Agent Loop

The loop is caller-implemented. OpenAI provides no hosted execution environment for the API path (only for ChatGPT Operator at $200/mo).

```
1. Send task (plain text) + optional initial screenshot to Responses API
2. Receive response with computer_call containing actions[]
3. Execute each action in actions[] (your code: Playwright, PyAutoGUI, etc.)
4. Capture screenshot of updated environment state
5. Send computer_call_output with screenshot back via previous_response_id
6. Repeat until no computer_call in response.output
```

### Continuation request (step 5)

```python
response = client.responses.create(
    model="computer-use-preview",
    previous_response_id=response.id,   # links to prior turn
    tools=[{"type": "computer"}],
    input=[{
        "call_id": computer_call.call_id,
        "type": "computer_call_output",
        "output": {
            "type": "computer_screenshot",
            "image_url": f"data:image/png;base64,{screenshot_b64}",
            "detail": "original"
        }
    }],
    truncation="auto"
)
```

`previous_response_id` manages conversation history server-side. If you don't use it, you must manually include all prior response output items in `input[]`.

### Safety checks

If `pending_safety_checks` is non-empty, you must acknowledge them before the loop continues:

```python
input_content[0]["acknowledged_safety_checks"] = [
    {"id": check.id, "code": check.code, "message": check.message}
    for check in acknowledged_checks
]
```

Three check codes: `malicious_instructions`, `irrelevant_domain`, `sensitive_domain`. All require human-in-loop confirmation before proceeding.

---

## 4. Supported Actions

All actions are in `computer_call.actions[]` as plain dicts:

| Action | Key params | Notes |
|---|---|---|
| `screenshot` | none | Model requesting a fresh capture |
| `click` | `x`, `y`, `button` (left/right/middle/back/forward) | Includes nav buttons |
| `double_click` | `x`, `y` | |
| `drag` | `path: [{x,y}...]` | Requires >= 2 points |
| `move` | `x`, `y` | Mouse position without click |
| `scroll` | `x`, `y`, `scroll_x`, `scroll_y` | Smooth scroll offsets |
| `keypress` | `keys: [string]` | Array supports combos (Ctrl+C) |
| `type` | `text` | Keyboard text entry |
| `wait` | `ms` | Pause in milliseconds |

Coordinates are absolute pixels within the declared display dimensions. If viewport and declared dimensions don't match, click accuracy degrades.

---

## 5. Codex CLI as an MCP Server

### Starting

```bash
codex mcp-server
```

Runs as a long-lived stdio JSON-RPC process (line-delimited JSON). Any MCP client — Claude Desktop, custom agent, Agents SDK — can connect.

### Tool exposed: `codex`

Single tool. Parameters:

| Param | Type | Required | Purpose |
|---|---|---|---|
| `prompt` | string | yes | Initial prompt / task |
| `model` | string | no | Override (e.g., `gpt-4o`) |
| `cwd` | string | no | Working directory |
| `approval-policy` | enum | no | `untrusted` / `on-failure` / `on-request` / `never` |
| `sandbox` | enum | no | `read-only` / `workspace-write` / `danger-full-access` |
| `base-instructions` | string | no | Custom system prompt |

Returns: `threadId` + response `content`.

### Session multiplexing

Multiple threads can share one MCP connection via `threadId` in notification `_meta`. This enables multi-agent workflows over a single stdio connection.

### codex-reply tool

A `codex-reply` tool exists for continuing an existing thread. Accepts `threadId` + `prompt`. Referenced in cookbook examples but not fully documented in the canonical MCP page.

### Use for agent orchestration

Codex CLI can both:
1. **Act as an MCP server** (expose itself to clients via `codex mcp-server`)
2. **Consume MCP servers** (connect to external tools via `config.toml` or `codex mcp add`)

This dual role is what enables the OpenAI Agents SDK cookbook pattern: a Project Manager agent (built with Agents SDK) delegates subtasks to Codex instances via MCP, each running in its own sandbox.

Reference: [developers.openai.com/codex/mcp](https://developers.openai.com/codex/mcp) and [cookbook.openai.com/examples/codex/codex_mcp_agents_sdk](https://cookbook.openai.com/examples/codex/codex_mcp_agents_sdk/building_consistent_workflows_codex_cli_agents_sdk)

---

## 6. Pricing

### API path (for automated harness)

| Model | Input (per 1M tokens) | Output (per 1M tokens) | Notes |
|---|---|---|---|
| `computer-use-preview` | $1.50 | $6.00 | Batch API only |
| `gpt-5.4` (standard API) | $2.50 | $15.00 | If used via general Responses API |
| `gpt-5.4` (Batch API) | $1.25 | $7.50 | |

**Hosted compute (Containers):** If using OpenAI's hosted shell/code interpreter alongside computer use:
- 1 GB: $0.03 per 20-min session
- 4 GB: $0.12 per 20-min session
- 16 GB: $0.48 per 20-min session
- 64 GB: $1.92 per 20-min session

### ChatGPT subscription path (for interactive / user-facing use)

| Plan | Cost | Notes |
|---|---|---|
| ChatGPT Plus | $20/mo | Operator access limited |
| ChatGPT Pro | $200/mo | Full Operator access, fixed-cost computer-use sessions |

**Critical distinction (already in `docs/research-consolidated.md`):** Subscription billing is separate from API billing. For an **automated eval harness**, use API keys with the `computer-use-preview` model. The $200/mo Pro subscription gates access to ChatGPT Operator (the managed browser UI), not raw API calls.

### Token cost per eval run (rough estimate)

Each screenshot is ~800-1200 input tokens (compressed). A 10-step task at 1000 tokens/screenshot = ~10K input tokens + ~500 output tokens per turn = ~$0.015-0.025/run at `computer-use-preview` Batch rates. A 1,000-run eval suite ≈ $15-25. Reasonable for PoC scale.

---

## 7. OpenAI vs. Anthropic Computer-Use: Key Differences

| Dimension | OpenAI (computer-use-preview) | Anthropic (claude-3.x + computer use) |
|---|---|---|
| **Environment scope** | Primarily browser; mac/windows/ubuntu in preview | Full desktop (any app, terminal, filesystem) |
| **Managed vs. self-hosted** | API: self-hosted env required; Operator: managed cloud browser | Self-hosted only (Docker reference impl provided) |
| **Actions per turn** | Batched `actions[]` array (multiple per response) | Single action per response |
| **Tool declaration** | `type: "computer_use_preview"` with display dims + environment | `type: "computer"` (in tool_use block) |
| **Response continuation** | `previous_response_id` (server-side history) | Full message history in each request body |
| **Safety checks** | Built-in `pending_safety_checks` with acknowledgement flow | No built-in check protocol; caller responsibility |
| **Grounding approach** | Pixel coordinates (x, y) against declared display dims | Pixel coordinates (x, y) against screenshot dims |
| **API surface** | Responses API only | Messages API (standard) |
| **Reasoning visibility** | Optional `reasoning.summary` field in output | Extended thinking blocks (separate content blocks) |
| **Default subscription path** | ChatGPT Operator ($200/mo Pro) — managed | No managed subscription path; API only |
| **Desktop vs. browser maturity** | Browser mature; desktop (mac/ubuntu/windows) less mature | Desktop mature via Docker VMs |

**Practical implication for this project:** For the PoC browser phase (Phase 1), OpenAI's Responses API is well-suited and has more detailed integration examples (Playwright integration is fully documented). For macOS desktop tasks (Phase 3), Anthropic's approach has more mature desktop support and the reference Docker environment is closer to what we need. Both can be wired into the same harness via the Observation/Model abstraction from `docs/research-consolidated.md` section 3.

---

## 8. Recommendation

For the harness, treat the OpenAI computer-use tool as the **Phase 1 browser backend**:

- Use `computer-use-preview` via Responses API with Playwright
- Use `previous_response_id` for history management (simpler than manual)
- Batch-execute `actions[]` per turn, screenshot after all actions
- Acknowledge safety checks via `acknowledged_safety_checks`
- Budget ~$0.015-0.025/run at Batch API rates

For Codex CLI/MCP: use `codex mcp-server` if you want to delegate coding subtasks (e.g., script generation, test writing) to Codex from within an orchestrator agent. It is not a computer-use execution engine — it's a code agent. Don't confuse the two.

For pricing: use API keys (not subscription) for the automated harness. Reserve subscription path for any interactive user-facing flows.

---

## 9. Sources of Truth

| Area | Canonical Source | Verification Method | Drift Risk |
|---|---|---|---|
| OpenAI computer-use tool API | [developers.openai.com/api/docs/guides/tools-computer-use](https://developers.openai.com/api/docs/guides/tools-computer-use) | Fetch page; check `computer_call` structure and `actions[]` format | High — model name (`computer-use-preview` vs `gpt-5.4`) actively changing |
| Azure OpenAI computer use (full code example) | [learn.microsoft.com/en-us/azure/foundry/openai/how-to/computer-use](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/computer-use) | Read the Playwright integration section; last updated 2026-03-13 | Medium |
| Codex MCP server implementation | [deepwiki.com/openai/codex/6.4-mcp-server-implementation](https://deepwiki.com/openai/codex/6.4-mcp-server-implementation-(codex-mcp-server)) | Cross-check against openai/codex GitHub repo source | High — open source, moving fast |
| Codex MCP docs (official) | [developers.openai.com/codex/mcp](https://developers.openai.com/codex/mcp) | Verify `codex mcp-server` command and tool schema | High |
| OpenAI API pricing | [openai.com/api/pricing](https://openai.com/api/pricing) | Fetch page directly; Batch API rates for `computer-use-preview` | High — pricing changes frequently |
| Anthropic computer use comparison | [workos.com/blog/anthropics-computer-use-versus-openais-computer-using-agent-cua](https://workos.com/blog/anthropics-computer-use-versus-openais-computer-using-agent-cua) | Secondary source; verify API details against first-party docs | Medium — blog may lag product |

---

## External Sources Used

- [Computer use | OpenAI API](https://developers.openai.com/api/docs/guides/tools-computer-use)
- [Computer Use (preview) in Azure OpenAI](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/computer-use?view=foundry-classic)
- [OpenAI Extends the Responses API — InfoQ](https://www.infoq.com/news/2026/03/openai-responses-api-agents/)
- [New tools for building agents | OpenAI](https://openai.com/index/new-tools-for-building-agents/)
- [Codex MCP Server Implementation | DeepWiki](https://deepwiki.com/openai/codex/6.4-mcp-server-implementation-(codex-mcp-server))
- [Model Context Protocol — Codex | OpenAI Developers](https://developers.openai.com/codex/mcp)
- [Building Consistent Workflows with Codex CLI & Agents SDK | OpenAI Cookbook](https://cookbook.openai.com/examples/codex/codex_mcp_agents_sdk/building_consistent_workflows_codex_cli_agents_sdk)
- [Building Computer Use Agents with OpenAI's API | RIIS](https://www.riis.com/blog/building-computer-use-agents-with-openai-api)
- [Anthropic's Computer Use versus OpenAI's CUA | WorkOS](https://workos.com/blog/anthropics-computer-use-versus-openais-computer-using-agent-cua)
- [OpenAI API Pricing | finout.io](https://www.finout.io/blog/openai-pricing-in-2026)

---

# Research: Cua macOS VM Sandboxing + Playwright Browser Automation

> Researched 2026-04-04. Feeds into `.plans/plan.md` Milestone 5 (VM isolation decision gate).

---

## 10. Current State — Cua

### What Cua Is

Cua (trycua/cua, YC-backed) is open-source infrastructure for computer-use agents. It provides:
- macOS/Linux VMs on Apple Silicon via **Lume** (Swift, uses Apple Virtualization.framework)
- A unified Python SDK (`cua-computer`) across macOS VMs, Linux containers, Windows Sandbox, cloud, and Docker
- An agent framework (`cua-agent`) and benchmarking suite (`cua-bench`)
- A FastAPI-based **computer-server** running inside VMs that handles all UI commands
- An MCP server (`cua-mcp-server`) launched January 20, 2026

**Lume** is the macOS-specific component. It runs an HTTP server on port 7777 and exposes REST endpoints for VM lifecycle. The VM houses a `cua-computer-server` instance that all SDK calls route through.

---

## 11. Answers: Cua Capabilities

### 11.1 macOS VM Sandboxing

Lume creates isolated macOS VMs on Apple Silicon using Apple's Virtualization.framework. Per the Cua blog (cua.ai/blog/lume-to-containerization):

> "Users are running VMs with great performance and low memory usage. Four months later, we're happy with our choice."

Near-native performance: ~90% of native speed. Ephemeral mode resets VM state on stop — each trial gets a clean slate.

Requirements:
- Apple Silicon Mac (M1/M2/M3/M4)
- macOS 15+ on host (Lume requirement; some SDK docs say 13+)
- 8GB RAM minimum, 16GB recommended
- 30GB free disk (VM image size)

### 11.2 How It Works on Apple Silicon

Lume uses **Apple Virtualization.framework** directly — not QEMU, not Docker VM. Each macOS VM is a full virtualized macOS instance. Lume daemon (`lume serve`) runs locally on port 7777 with these key endpoints:

- `POST /lume/vms` — create VM
- `POST /lume/pull <image>` — pull image from GitHub Container Registry (LZ4-compressed, ~30GB)
- `POST /lume/vms/:name/run` — start VM (async 202)
- `DELETE /lume/vms/:name` — delete VM

Apple announced a new Containerization framework at WWDC (sub-second startup, each container in its own tiny VM). Cua is tracking it and plans to migrate. GPU passthrough expected in macOS Tahoe 26. This creates forward drift risk for the Lume layer.

### 11.3 Creating/Resetting VMs

**Yes.** Python SDK:

```python
async with Sandbox.ephemeral(Image.macos()) as sb:
    # clean VM, auto-reset on context exit
    ...
```

Ephemeral mode is the default for clean-state trials. No explicit snapshot/restore API was found documented publicly, but ephemeral mode provides the clean-slate semantics the eval harness needs.

### 11.4 Screenshots

**Yes.** Multiple levels:
- `await sb.screenshot()` — SDK level
- `await computer.interface.screenshot()` — computer-server level
- `screenshot()` — MCP tool

### 11.5 Mouse/Keyboard Input

**Yes.** Full set:
- Mouse: `left_click(x, y)`, `right_click(x, y)`, `double_click(x, y)`, `move_cursor(x, y)`, `drag_to(x, y, duration)`, `get_cursor_position()`
- Keyboard: `type_text(text)`, `press_key(key)`, `hotkey(modifier, key)`
- Gestures: `await sb.mobile.gesture(...)` (multi-touch)

### 11.6 Accessibility Trees

**Yes, confirmed.** `get_accessibility_tree()` is a listed MCP tool (source: ubos.tech/mcp/cua). Changelog v0.1.26 (2025-10-24) fixed "accessibility API not working on macOS and Windows" — it was broken before that release and has been working since.

Important caveat: the AX tree is provided from within the macOS VM. It reflects what macOS's AX API exposes, which is subject to the same 33% full-coverage gap from Screen2AX (Jul 2025). Cua does not synthesize or enhance the tree.

### 11.7 MCP Server Support

**Yes, launched January 20, 2026.** Official (@trycua on X):

> "Cua loves MCP. Launching official MCP Server support today - enabling our Computer-Use Agent to run through Claude Desktop, Cursor, and other MCP clients."

Full MCP tool surface (source: ubos.tech/mcp/cua):

| Category | Tools |
|---|---|
| Mouse | `left_click`, `right_click`, `double_click`, `move_cursor`, `drag_to`, `get_cursor_position` |
| Keyboard | `type_text`, `press_key`, `hotkey` |
| Display | `screenshot`, `get_screen_size` |
| Accessibility | `get_accessibility_tree` |
| Clipboard | `set_clipboard`, `copy_to_clipboard` |
| Filesystem | `file_exists`, `directory_exists` |
| Shell | `run_command` |

HTTP and MCP interfaces run simultaneously (v0.3.7, 2026-01-20).

### 11.8 Setup Complexity

**Moderate.** Two layers:

Layer 1 — Lume daemon (one-time per machine):
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/trycua/cua/main/libs/lume/scripts/install.sh)"
lume serve  # must be running at all times
```

Layer 2 — Python SDK:
```bash
pip install cua-agent[all]  # requires Python 3.12 or 3.13
```

First run: pulls ~30GB macOS VM image. Subsequent runs use cached image. Total first-time setup: 1-2 hours. Ongoing per-run cost: low.

### 11.9 Stability for a PoC

**Conditionally yes.** The screenshot/click/type loop is stable. The MCP server is 3 months old (Jan 2026) and still receiving bug fixes (v0.3.15: MCP redirect fix, coordinate scaling bug). The accessibility tree works but was broken as recently as Oct 2025.

Risk level for PoC use: **medium**. Suitable for Milestone 5+ use as VM isolation layer. Not recommended as a day-1 dependency before the host harness loop is proven.

The Apple Containerization migration is expected H2 2026. The Python SDK interface (`Sandbox.ephemeral`) should remain stable across that, but the Lume layer underneath will change.

---

## 12. Playwright Capabilities

### What Playwright Provides for Browser Automation

Playwright (microsoft/playwright-mcp, stable) provides two primary capabilities relevant to the eval harness:

**Screenshots:**
- Full-page and element-level capture
- Available in both "snapshot mode" and "vision mode" (via `--caps vision`)

**Accessibility Tree (ARIA Snapshots):**
- Default mode uses the accessibility tree exclusively, not pixels
- `locator.ariaSnapshot()` — YAML representation of ARIA tree
- `expect(locator).toMatchAriaSnapshot()` — assertion-style matching
- Returns: roles, accessible names, ARIA attributes, nesting relationships
- Playwright MCP reads pages via AX tree by default — no vision model required

**Token efficiency comparison (from 2026 sources):**
- Playwright MCP (default): ~114,000 tokens per typical task
- `@playwright/cli` (released early 2026): ~27,000 tokens — ~4x more efficient

**Browser support on macOS:** Chromium, Firefox, WebKit, Microsoft Edge.

**Key architectural property:** Playwright MCP natively provides the `a11y-only` observation baseline from `docs/research-consolidated.md` Section 3. This makes Playwright the correct tool for the Phase 1 observation-mode comparison (screenshot-only vs. AX-tree-only vs. hybrid).

---

## 13. Alternatives for macOS Agent Sandboxing

Only two credible options exist for full macOS desktop VM sandboxing:

| Option | macOS VM | Open source | AX tree | Python SDK | Status |
|---|---|---|---|---|---|
| **Cua / Lume** | Yes (native Apple Vz) | Yes (YC-backed) | Yes | Yes | Active, medium stability |
| **Daytona** | Claims macOS VMs | Partially | Unknown | Unknown | Claims "Computer Use" support; no implementation detail found |
| **E2B** | Linux only | Yes | No (Linux/Firecracker) | Yes | Linux-only, irrelevant for macOS |
| **Modal / Northflank / etc.** | Linux only | Varies | No | Varies | Linux-only |
| **Host macOS** | N/A | N/A | Yes (native AXUIElement) | Yes | Direct — no isolation |

**Conclusion:** Cua/Lume is the only open-source, documented, Python-SDK-backed macOS VM sandboxing option. For the PoC, host macOS is the simpler starting point; Cua is the only viable VM isolation upgrade path.

---

## 14. Constraints

1. **Lume requires Apple Silicon** — hard constraint from Apple Vz framework. No Intel fallback.
2. **Lume requires macOS 15+ on host** — verifiable before setup.
3. **VM image ~30GB** — significant first-time download; cached after.
4. **MCP server is 3 months old** — pin version for PoC stability.
5. **Apple Containerization migration** — H2 2026 drift expected in Lume layer; Python SDK API should remain stable.
6. **AX inside VM has same 33% coverage gap** — Cua does not solve the Screen2AX problem. Track `a11y_available` per step regardless.
7. **Playwright browser-only** — for native macOS desktop tasks (Finder, Office, PDF viewers), Cua/AXUIElement paths are required.

---

## 15. Options

### Option A: Host macOS first, Cua as deferred VM layer (current plan.md)

Default per `.plans/plan.md` Milestone 5 gate. Add Cua only if host-environment noise is the binding failure mode.

**Pros:** Fast iteration, no 30GB download blocking M1, no Lume daemon complexity early.
**Cons:** Trial contamination is real; requires rigorous setup/cleanup scripts per task.
**When:** Default. Proceed unless M2/M3 data shows host noise is dominant.

### Option B: Cua VM from Milestone 1

Use Cua VM as execution environment from day one.

**Pros:** Clean state per trial guaranteed, tests Cua integration early, closer to eventual production shape.
**Cons:** 30GB download before first run, Lume daemon dependency, 3-month-old MCP server risk in early milestones.
**When:** Only if host macOS is unavailable or contamination failures appear immediately in M1 smoke tests.

### Option C: Playwright for browser tasks, Cua only for desktop tasks

Use Playwright (native ARIA tree) for all browser tasks in Phase 1. Reserve Cua for Phase 3 native macOS desktop tasks.

**Pros:** Playwright is stable; provides the `a11y-only` baseline natively; lower initial complexity.
**Cons:** Doesn't address native macOS desktop — the distinctive hard problem.
**When:** This is actually the correct framing for Phase 1 per the existing plan.md. Playwright already is the Phase 1 executor.

---

## 16. Recommendation

**The existing plan.md phasing is correct. No changes required.**

- Playwright is the right Phase 1 tool: natively supports both screenshot and ARIA-tree observation modes, which is exactly the observation-format comparison the harness needs.
- Cua is the right Phase 3 VM isolation tool: it is the only open-source option providing native macOS VMs on Apple Silicon with Python SDK, screenshot, input, AX tree, and MCP support.
- The Milestone 5 decision gate ("if host execution noise is blocking, add VM adapter") is the right structure. Earn the Cua complexity by proving it is needed.

**One concrete addition:** when Cua is introduced at Milestone 5, pin `cua-computer` to a specific version. The MCP server received a bug-fix release as recently as Jan 29, 2026; unpinned installs may break across experiment runs.

**Playwright MCP vs `@playwright/cli`:** Start with Playwright MCP for Phase 1. Evaluate `@playwright/cli` in Milestone 3 when cost-per-run metrics are available — the 4x token reduction may matter at eval scale.

---

## 17. Sources of Truth — Cua + Playwright

| Area | Canonical Source | Verification Method | Drift Risk |
|---|---|---|---|
| Cua Python SDK API | [github.com/trycua/cua](https://github.com/trycua/cua) README | Read current README; `pip show cua-computer` | **High** — active dev, Apple Vz migration pending |
| Lume REST API | `libs/lume/` source + Deepwiki | `curl localhost:7777/lume/vms` when Lume running | **High** — Apple Containerization may replace in H2 2026 |
| Cua MCP tools | [ubos.tech/mcp/cua](https://ubos.tech/mcp/cua) + `libs/mcp-server/` | Read tool registration in source | **Medium** — 3-month-old server, still getting fixes |
| Cua AX tree support | Changelog v0.1.26 (2025-10-24) | Run `get_accessibility_tree()` in test VM | **Medium** — works, but 33% AX gap remains |
| Playwright MCP tools | [github.com/microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) | Read README | **Low** — Microsoft, stable |
| Playwright ARIA snapshots | [playwright.dev/docs/aria-snapshots](https://playwright.dev/docs/aria-snapshots) | `npx playwright --version` | **Low** — mature API |
| `@playwright/cli` efficiency | TestCollab/TestDino 2026 | npm package `@playwright/cli` | **Medium** — new in 2026, evolving |
| Lume host requirements | cua.ai macOS sandbox docs | `sw_vers` + Lume install script | **Low** — macOS 15+ is stable |
| Apple Containerization framework | cua.ai/blog/lume-to-containerization | Apple dev news / WWDC | **High** — expected macOS Tahoe 26 |

---

## External Sources (Cua + Playwright)

- [github.com/trycua/cua](https://github.com/trycua/cua)
- [cua.ai/docs/cua/reference/desktop-sandbox/macos](https://cua.ai/docs/cua/reference/desktop-sandbox/macos)
- [cua.ai/docs/cua/reference/desktop-sandbox/changelog](https://cua.ai/docs/cua/reference/desktop-sandbox/changelog)
- [cua.ai/blog/lume-to-containerization](https://cua.ai/blog/lume-to-containerization)
- [deepwiki.com/trycua/cua](https://deepwiki.com/trycua/cua)
- [deepwiki.com/trycua/cua/5-lume-vm-management](https://deepwiki.com/trycua/cua/5-lume-vm-management)
- [ubos.tech/mcp/cua](https://ubos.tech/mcp/cua)
- [x.com/trycua/status/1910455692861071414](https://x.com/trycua/status/1910455692861071414) — MCP launch
- [github.com/microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp)
- [playwright.dev/docs/aria-snapshots](https://playwright.dev/docs/aria-snapshots)
- [betterstack.com/community/comparisons/best-sandbox-runners](https://betterstack.com/community/comparisons/best-sandbox-runners/) — sandbox market overview

---

---

# Research: Inspect AI Eval Framework

> Researcher: Claude Sonnet 4.6 | Date: 2026-04-04
> Question: Should this PoC build on top of Inspect AI or build a thin custom harness?

---

## 18. Current State — Inspect AI

### What exists in this repo

- `docs/research-consolidated.md` line 251 already recommends Inspect AI as the "primary harness runner" but the `.plans/plan.md` Milestone 3 (line 244) explicitly says "Do not make Inspect AI the day-1 runtime backbone."
- No implementation code exists yet.

### What Inspect AI is (as of April 2026)

Inspect AI is an open-source LLM/agent eval framework from the UK AI Security Institute (AISI).
- PyPI: `inspect-ai`, latest version **0.3.199** (released March 17, 2026)
- License: MIT, Python >= 3.10
- Used by: UK AISI, Anthropic, DeepMind, and others for production safety evals
- Docs: https://inspect.aisi.org.uk/
- Source: https://github.com/UKGovernmentBEIS/inspect_ai

---

## 19. How the Dataset → Solver → Scorer Pattern Works

Every Inspect eval is a `Task` wired from three components.

**Dataset** — Loads `Sample` objects. Each sample has an `input`, optional `target`, `metadata`, and per-sample `sandbox` specification. Supported formats: CSV, JSON, JSONL, Hugging Face datasets, or in-memory `MemoryDataset`. Custom readers take a record dict and return a `Sample`. Our task YAML files would load via a custom reader or `MemoryDataset`.

**Solver** — An async function decorated with `@solver` that receives and returns a `TaskState`. `TaskState` holds the message history (`messages`), model output (`output`), and metadata. Solvers chain together via `chain()`. Built-ins include `prompt_template()`, `system_message()`, `generate()`, `use_tools()`, `chain_of_thought()`, and `self_critique()`. Agents are solvers — the ReAct agent and custom `@agent` functions both satisfy the solver interface.

**Scorer** — An async function decorated with `@scorer` that receives `TaskState` and `Target` and returns a `Score`. Built-ins: `includes()`, `match()`, `exact()`, `pattern()`, `f1()`, `model_graded_qa()`. Custom scorers are trivial to write. `model_graded_qa()` supports multiple grader models, majority voting, partial credit, and custom rubric templates — maps directly to our LLM-judge grading path.

Mapping to this project's five objects:
- `task` → Inspect `Task` + `Sample`
- `trial` → one `eval()` call over a sample
- `trace` → Inspect's per-step JSONL log (messages, tool calls, screenshots, costs — all captured automatically)
- `grader` → Inspect `Scorer`
- `report` → Inspect View dashboard or `eval_results` output

---

## 20. Desktop / Browser Agent Eval Support

### Computer tool (built-in)

Inspect ships a `computer()` tool that gives a model a desktop environment via screenshots with mouse and keyboard interaction.

- Backed by the `aisiuk/inspect-computer-tool` Docker image (Ubuntu 22.04) with Firefox, VS Code, Xpdf pre-installed
- Actions: `key`, `type`, `mouse_move`, `left_click`, `right_click`, `left_click_drag`, `double_click`, screenshot
- Optional VNC monitoring via port 5900 and 6080 (noVNC) — watch the agent live
- Natively binds to Anthropic `computer_20251124`, OpenAI's updated computer tool (GPT 5.4), and Google's Gemini computer tool (all confirmed in March 2026 changelog)
- `max_screenshots` and `timeout` are configurable

**Critical constraint**: Docker required. The environment is Ubuntu Linux, not macOS. No built-in macOS desktop environment support exists.

### Web browser tool (built-in)

Headless Chromium with navigation, history, and mouse/keyboard interactions. Runs in the main process sandbox — no Docker. Well-suited for browser eval tasks.

### Custom tools

The `@tool` decorator registers any async Python function as a model-callable tool. Type annotations and docstring parameter descriptions are required so Inspect can generate the tool schema. Tools are wired via `use_tools([tool1(), tool2()])` in a solver.

There is no constraint on what a custom tool can do — macOS `AXUIElement` calls, `ScreenCaptureKit` screenshots, Playwright, Cua SDK calls, shell commands — all viable. This means the macOS observation and execution layer can be implemented as Inspect tools, making Inspect the orchestrator without locking you into its Ubuntu container.

---

## 21. How Easy Is It to Add Custom Tools, Environments, and Scorers?

**Custom tools**: Very easy. One decorator + async function + docstring. Can call any Python code.

**Custom scorers**: Very easy. One decorator + async function returning `Score(value, explanation)`. Our programmatic graders (`file_exists`, `clipboard_content`, etc.) map cleanly.

**Custom agents**: Moderate effort. `@agent` decorator, implement `execute(state: AgentState) -> AgentState`, call `generate_loop()` for the tool-use loop. Full control over message construction, tool selection, and state. The `store_as()` interface handles per-sample persistent state across turns.

**Custom sandbox environments**: Inspect defines a `SandboxEnvironment` interface. Third-party implementations exist for k8s, EC2, Proxmox, Vagrant, Podman, Modal. Building a macOS adapter wrapping Cua VMs is feasible — but non-trivial (days, not hours). No macOS-native sandbox adapter exists in the extension registry as of April 2026.

**Logging and observability**: Inspect automatically logs all messages, tool calls, screenshots, token counts, and costs to JSONL per eval run. The Inspect View web dashboard renders these traces interactively. This is comparable to what our custom `trace` model would do, but already built and production-tested (used by UK AISI).

---

## 22. Built-in Support for Computer-Use Agents

Yes, with caveats.

Inspect has first-class support for computer-use models. The `computer()` tool auto-binds to Anthropic `computer_20251124`, OpenAI's updated computer tool (GPT 5.4), and Gemini's native computer tool. The March 2026 changelog confirms active investment: new model versions are wired in as they are released.

The limitation is environment: Ubuntu Docker only. The agent sees what is in the container, not a macOS desktop.

---

## 23. Tradeoffs: Inspect AI vs. Custom Harness

### Option A — Build on Inspect AI from day 1

**What it gives you:**
- Dataset/Solver/Scorer maps to our five objects — no equivalent code to write
- Single CLI interface over OpenAI, Anthropic, Google, Groq, Mistral, local models
- Full trace logging to JSONL with tool calls, screenshots, costs, token counts — built in
- Inspect View web UI for trace exploration — built in
- `model_graded_qa()` for LLM-as-judge scoring with majority vote and confidence intervals — built in
- Agent bridge to OpenAI Agents SDK, LangChain, Pydantic AI
- Parallelism, retries, resumable runs — built in
- Growing extension ecosystem: 30+ integrations, 1000+ evals in inspect-evals

**What it costs you:**
- DSL ramp time (a few hours)
- Docker required for the built-in computer tool (not needed for custom tools)
- Computer tool runs Ubuntu — for macOS evaluation you must build custom tools regardless
- Active weekly releases (~0.3.x); behavior can change under you
- Risk of confusing framework behavior with agent behavior — named in `.plans/plan.md:246`

### Option B — Custom thin harness

**What it gives you:**
- Full transparency — every line is yours
- No framework abstraction over the macOS observation/execution layer
- No Docker dependency for the main loop
- Faster Milestone 1 (no framework ramp)

**What it costs you:**
- Re-implementing: multi-model adapter, trace logging, cost tracking, parallel runner, report aggregation, LLM-as-judge scorer, log viewer
- Substantial code surface for a PoC
- Risk of the harness growing into an accidental framework (`.plans/plan.md:405`)

### Option C — Custom harness first, Inspect AI as optional adapter (current plan)

Build a thin custom runner with narrow adapter interfaces. After the loop works, integrate Inspect AI when multi-model comparison and parallel runs become the bottleneck.

**What it gives you:**
- All transparency benefits of Option B during the most uncertain phase
- Migration path to Inspect AI when its strengths matter
- No risk of conflating framework behavior with agent behavior in Milestones 1-2

**What it costs you:**
- You own the core runner and trace model
- Reconciling two task/trace representations when Inspect AI is added (mitigated by compatible abstractions)

---

## 24. Can Inspect AI Run Tasks Against macOS Desktop Environments?

Not natively.

The built-in `computer()` tool uses Ubuntu Docker. No macOS sandbox exists in the official extension registry. Cua is not integrated with Inspect.

**What is possible:**

1. Write `@tool` functions calling macOS APIs (AXUIElement, ScreenCaptureKit, Playwright, Cua SDK). Inspect orchestrates the tool calls; macOS interaction code is custom either way.
2. Build a custom `SandboxEnvironment` plugin wrapping Cua VMs. This would give per-sample clean environment resets via Inspect's interface but requires building the adapter.

For macOS-native desktop evaluation, Inspect AI provides orchestration but not the environment. The macOS-specific code (observation, execution, environment setup) must be custom-built regardless of whether Inspect is used.

---

## 25. Recommendation

**Use a thin custom harness first (Option C), with Inspect AI deferred to Milestone 3 or later as an optional adapter.**

This confirms the direction already in `.plans/plan.md`. Specific reasoning:

1. The core unknown is harness boundary quality — whether and how observation format, grounding, and execution primitives affect agent performance. Starting inside a framework risks attributing framework behavior to agent behavior.
2. Inspect AI's largest value-adds are multi-model orchestration, parallel eval runs, and trace logging. None of these are the binding constraint for Milestones 1 or 2.
3. For macOS desktop evaluation, Inspect AI does not reduce implementation work. The macOS observation layer (AX, ScreenCaptureKit), executor, and environment setup must be written regardless.
4. The computer tool only adds value if the target environment is Linux/Docker. For macOS-first work it provides nothing useful out of the box.

**After Milestones 1-2, when multi-model comparison and parallel trials become the bottleneck, Inspect AI is worth adopting.** At that point, our `Scorer` maps cleanly to Inspect `@scorer`, our `TaskLoader` maps to `MemoryDataset`, and the abstractions are compatible enough that integration is manageable.

**Component-level guidance:**

| Component | Approach | When |
|---|---|---|
| Task schema / loader | Custom (YAML + Pydantic) | Milestone 1 |
| Trial runner / agent loop | Custom | Milestone 1-2 |
| Trace logger | Custom (append-only JSONL) | Milestone 1 |
| Programmatic graders | Custom `graders/` | Milestone 2 |
| LLM-as-judge scorer | DeepEval first; or `model_graded_qa()` if Inspect adopted | Milestone 2-3 |
| Multi-model adapter | Custom interfaces; then consider Inspect's model layer | Milestone 3 |
| Parallel execution / retries | Inspect AI (if/when adopted) | Milestone 3+ |
| macOS computer-use loop | Custom `@tool` wrappers over AX/ScreenCaptureKit/Cua | Milestone 3-4 |
| Linux/Docker computer-use | Inspect `computer()` tool | Only if cross-OS comparison is needed |
| Trace viewer | Custom report first; Inspect View if adopted | Milestone 3+ |

---

## 26. Sources of Truth — Inspect AI

| Area | Canonical Source | Verification Method | Drift Risk |
|---|---|---|---|
| Inspect AI docs | https://inspect.aisi.org.uk/ | Compare changelog version to PyPI `0.3.199` | Medium — active weekly releases |
| Inspect AI PyPI | https://pypi.org/project/inspect-ai/ | `pip index versions inspect-ai` | High — patch releases frequent |
| Inspect GitHub | https://github.com/UKGovernmentBEIS/inspect_ai | Check commit log and issues | Medium |
| Computer tool Docker image | `aisiuk/inspect-computer-tool` on Docker Hub | `docker pull` + inspect labels | Medium |
| Inspect extensions registry | https://inspect.aisi.org.uk/extensions/index.html | Check for new macOS or Cua sandbox entries | Low-medium |
| Anthropic computer-use tool version | https://docs.anthropic.com/en/docs/build-with-claude/computer-use | Check for versions beyond `computer_20251124` | High |

---

## External Sources (Inspect AI)

- [Inspect AI docs — inspect.aisi.org.uk](https://inspect.aisi.org.uk/)
- [Inspect AI Agents — inspect.aisi.org.uk/agents.html](https://inspect.aisi.org.uk/agents.html)
- [Inspect AI Standard Tools — inspect.aisi.org.uk/tools-standard.html](https://inspect.aisi.org.uk/tools-standard.html)
- [Inspect AI Sandboxing — inspect.aisi.org.uk/sandboxing.html](https://inspect.aisi.org.uk/sandboxing.html)
- [Inspect AI Extensions — inspect.aisi.org.uk/extensions/index.html](https://inspect.aisi.org.uk/extensions/index.html)
- [Inspect AI Changelog — inspect.aisi.org.uk/CHANGELOG.html](https://inspect.aisi.org.uk/CHANGELOG.html)
- [inspect-ai on PyPI — pypi.org/project/inspect-ai](https://pypi.org/project/inspect-ai/)
- [Inspect GitHub — github.com/UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai)
- [Inspect Sandboxing Toolkit — aisi.gov.uk](https://www.aisi.gov.uk/blog/the-inspect-sandboxing-toolkit-scalable-and-secure-ai-agent-evaluations)
- [Inspect AI Review 2025 — neurlcreators.substack.com](https://neurlcreators.substack.com/p/inspect-ai-evaluation-framework-review)
- [Hamel's practical notes on Inspect AI — hamel.dev](https://hamel.dev/notes/llm/evals/inspect.html)
