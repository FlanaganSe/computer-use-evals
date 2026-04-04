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
