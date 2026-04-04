# Research: OpenAI Responses API for Computer Use

**Date:** 2026-04-04
**Sources:** Azure OpenAI docs (gpt-5.4, March 2026), OpenAI developer docs (computer-use-preview model page), community corrections thread, RIIS/Portkey/orgo.ai implementation guides, Dicklesworthstone GitHub guide.

---

## 1. Current State — Two Parallel Tool Formats

There are two divergent tool type strings in the wild, tied to two different models:

| Model | Tool type string | Field names | Notes |
|---|---|---|---|
| `computer-use-preview` (preview) | `"computer_use_preview"` | `display_width`, `display_height`, `environment` | Responses API only; actions[] batched |
| `gpt-5.4` (Azure GA) | `"computer"` | no display/env fields needed | Responses API only; actions[] batched |

The harness targets `computer-use-preview` (available on OpenAI API without Azure). The tool type for that model is `"computer_use_preview"`. Field names confirmed by RIIS blog, Portkey docs, and the Dicklesworthstone guide — note they are `display_width` / `display_height` (not `display_width_px`).

**Model snapshot:** `computer-use-preview-2025-03-11` (current as of April 2026).

---

## 2. Tool Declaration

```python
tools = [{
    "type": "computer_use_preview",
    "display_width": 1280,
    "display_height": 720,
    "environment": "browser",   # options: "browser", "mac", "windows", "ubuntu"
}]
```

Required top-level param: `truncation="auto"` — multiple sources confirm this is mandatory for multi-step interactions (context window management). Without it the API may error on long conversations.

---

## 3. Initial API Call

```python
import base64
from openai import OpenAI

client = OpenAI()  # reads OPENAI_API_KEY from env

# Take initial screenshot (raw PNG bytes → base64 string)
screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

response = client.responses.create(
    model="computer-use-preview",
    tools=[{
        "type": "computer_use_preview",
        "display_width": 1280,
        "display_height": 720,
        "environment": "browser",
    }],
    input=[{
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": "Your task description here"
            },
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{screenshot_b64}",
                "detail": "original"   # preserves full resolution; improves click accuracy
            }
        ]
    }],
    instructions="You are an AI agent controlling a browser...",
    truncation="auto",
)
```

**Note on image format in first call:** The initial screenshot goes in a user message `content[]` as `{"type": "input_image", "image_url": "data:image/png;base64,...", "detail": "original"}`. This is different from the `computer_call_output` format used in subsequent calls.

---

## 4. Response Object Structure

```python
# response.id          — string, used as previous_response_id in next call
# response.output      — list of output items
# response.usage       — token usage object

# Iterate output items:
for item in response.output:
    item.type  # "message", "reasoning", "computer_call"
```

### computer_call item

```python
computer_calls = [item for item in response.output if item.type == "computer_call"]
computer_call = computer_calls[0]  # typically one per response

computer_call.call_id              # str — used in computer_call_output
computer_call.actions              # list of action dicts (batched array)
computer_call.pending_safety_checks  # list or None
computer_call.status               # "completed"
```

### Full computer_call JSON example (from Azure docs, applies to computer-use-preview):

```json
{
    "id": "cu_068b...",
    "type": "computer_call",
    "call_id": "call_4y94crSZe0elpGhdiiwjLpa0",
    "actions": [
        {
            "type": "screenshot"
        }
    ],
    "pending_safety_checks": null,
    "status": "completed"
}
```

---

## 5. actions[] Array — All Action Types and Parameters

Actions are plain dicts (not typed objects) in the response. Access with `.get()` or dict key access.

| Action type | Parameters | Notes |
|---|---|---|
| `screenshot` | (none) | Model requesting current screen state |
| `click` | `x`, `y`, `button` (`"left"`, `"right"`, `"middle"`) | Pixel coordinates; `button` defaults to `"left"` |
| `double_click` | `x`, `y` | |
| `drag` | `path` (list of `{x, y}` dicts) | At least 2 points |
| `move` | `x`, `y` | Mouse move without click |
| `scroll` | `x`, `y`, `scroll_x`, `scroll_y` | `scroll_x`/`scroll_y` are offsets; `x`/`y` are position |
| `keypress` | `keys` (list of strings) | e.g. `["ctrl", "c"]` for combos |
| `type` | `text` | |
| `wait` | `ms` | Milliseconds |

**Coordinates** are in the declared display space. With `display_width=1280, display_height=720`, model coordinates map 1:1 to Playwright viewport pixels — no scaling needed.

**Screenshot action handling:** When `actions[i].type == "screenshot"`, the model is asking for a fresh view. This should be treated as a no-op in action execution — the runner's natural loop collects a new screenshot on every `decide()` call anyway. Just skip it.

---

## 6. Continuing the Loop — computer_call_output

After executing all actions in `computer_call.actions`, take a screenshot and send it back:

```python
screenshot_b64 = base64.b64encode(new_screenshot_bytes).decode("utf-8")

response = client.responses.create(
    model="computer-use-preview",
    previous_response_id=response.id,   # server-side history; no need to replay messages
    tools=[{
        "type": "computer_use_preview",
        "display_width": 1280,
        "display_height": 720,
        "environment": "browser",
    }],
    input=[{
        "call_id": computer_call.call_id,
        "type": "computer_call_output",
        "output": {
            "type": "input_image",
            "image_url": f"data:image/png;base64,{screenshot_b64}",
            "detail": "original"
        }
    }],
    truncation="auto",
)
```

**Two confirmed formats for the screenshot output type** — there is documentation inconsistency:
- RIIS blog and Portkey use: `"type": "input_image"` inside `output{}`
- Azure docs (gpt-5.4) use: `"type": "computer_screenshot"` inside `output{}`

Both appear accepted. For `computer-use-preview` (non-Azure), `"type": "input_image"` is the format used in the original OpenAI guide examples and confirmed by multiple third-party sources. Use `"type": "input_image"` for our implementation.

`previous_response_id` is the server-side session continuation mechanism. You do **not** need to rebuild the full input history — OpenAI stores it. Just send the new `computer_call_output`.

---

## 7. Safety Checks

Safety checks appear in `computer_call.pending_safety_checks` as a list of objects:

```json
{
    "type": "computer_call",
    "call_id": "call_nEJ...",
    "actions": [{"type": "click", "button": "left", "x": 135, "y": 193}],
    "pending_safety_checks": [
        {
            "id": "cu_sc_67cb...",
            "code": "malicious_instructions",
            "message": "We've detected instructions that may cause your application to perform malicious or unauthorized actions. Please acknowledge this warning if you'd like to proceed."
        }
    ],
    "status": "completed"
}
```

Three possible `code` values:
- `"malicious_instructions"` — adversarial content in screenshot
- `"irrelevant_domain"` — current domain is irrelevant to conversation history
- `"sensitive_domain"` — current domain is flagged as sensitive

To acknowledge and proceed, include `acknowledged_safety_checks` in the `computer_call_output` input:

```python
input_item = {
    "call_id": computer_call.call_id,
    "type": "computer_call_output",
    "acknowledged_safety_checks": [
        {
            "id": check.id,
            "code": check.code,
            "message": check.message
        }
        for check in computer_call.pending_safety_checks
    ],
    "output": {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{screenshot_b64}",
        "detail": "original"
    }
}
```

The `acknowledged_safety_checks` field is a sibling of `output` inside the `computer_call_output` item, not nested inside `output`.

If `pending_safety_checks` is `None` or empty, omit `acknowledged_safety_checks` entirely.

---

## 8. Token Usage

```python
# Access usage on the response object:
response.usage.input_tokens           # int
response.usage.output_tokens          # int
response.usage.total_tokens           # int
response.usage.input_tokens_details.cached_tokens   # int (may be 0)
response.usage.output_tokens_details.reasoning_tokens  # int
```

Full JSON from Azure docs (same structure for OpenAI API):

```json
"usage": {
    "input_tokens": 820,
    "input_tokens_details": {
        "cached_tokens": 0
    },
    "output_tokens": 40,
    "output_tokens_details": {
        "reasoning_tokens": 16
    },
    "total_tokens": 860
}
```

**Pricing (current as of April 2026, standard API):**
- Input: $3.00 / 1M tokens
- Output: $12.00 / 1M tokens
- Batch API: 50% discount → $1.50 in / $6.00 out

Cost formula for the adapter's `_estimate_cost()`:
```python
cost = (input_tokens / 1_000_000) * 3.00 + (output_tokens / 1_000_000) * 12.00
```

---

## 9. Complete Adapter Loop Pattern

```python
import base64
import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

TOOLS = [{
    "type": "computer_use_preview",
    "display_width": 1280,
    "display_height": 720,
    "environment": "browser",
}]

def first_call(task_description: str, screenshot_bytes: bytes):
    """First turn: no previous_response_id."""
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    return client.responses.create(
        model="computer-use-preview",
        tools=TOOLS,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": task_description},
                {"type": "input_image", "image_url": f"data:image/png;base64,{screenshot_b64}", "detail": "original"},
            ]
        }],
        truncation="auto",
    )

def continuation_call(prev_response_id: str, call_id: str, screenshot_bytes: bytes,
                      pending_safety_checks=None):
    """Subsequent turns: use previous_response_id + computer_call_output."""
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    input_item = {
        "call_id": call_id,
        "type": "computer_call_output",
        "output": {
            "type": "input_image",
            "image_url": f"data:image/png;base64,{screenshot_b64}",
            "detail": "original",
        }
    }
    if pending_safety_checks:
        input_item["acknowledged_safety_checks"] = [
            {"id": sc.id, "code": sc.code, "message": sc.message}
            for sc in pending_safety_checks
        ]
    return client.responses.create(
        model="computer-use-preview",
        previous_response_id=prev_response_id,
        tools=TOOLS,
        input=[input_item],
        truncation="auto",
    )

# --- Parsing helpers ---

def extract_computer_call(response):
    """Returns the first computer_call item or None."""
    for item in response.output:
        if item.type == "computer_call":
            return item
    return None

def is_done(response) -> bool:
    """Model is done when there is no computer_call in output."""
    return extract_computer_call(response) is None
```

---

## 10. Constraints

1. **`truncation="auto"` is mandatory** — without it, long multi-step conversations may fail. All `responses.create()` calls must include it.

2. **`previous_response_id` replaces full history** — never manually replay the output array. Use `previous_response_id` and send only the new `computer_call_output`.

3. **Model is Responses-API-only** — cannot use Chat Completions (`client.chat.completions.create`).

4. **Tier 3+ required** — rate limits: 3,000 req/min, 20M tokens/min. The harness runs at far lower rate, so no concern.

5. **Context window: 8,192 tokens; max output: 1,024 tokens** — for `computer-use-preview`. Screenshots count as many tokens. `truncation="auto"` handles this.

6. **Tool type name is `"computer_use_preview"` for our model** — `"computer"` is the GA/Azure tool type for `gpt-5.4`. Do not confuse them.

7. **Actions are dicts, not typed objects** — `computer_call.actions` is a list of plain Python dicts. Use `.get("type")` or `action["type"]`, not `action.type`.

---

## 11. Options for the Adapter Implementation

### Option A — Stateful adapter, one API call per `decide()`

`decide()` builds the correct API call (first vs. continuation) based on internal `_previous_response_id` state. Returns all actions from the single `computer_call.actions[]` list. The runner iterates and executes them. On the next `decide()` call, the adapter sends the new screenshot as `computer_call_output`.

- Trade-off: The runner loop always takes a fresh screenshot before calling `decide()`. This screenshot is always the state AFTER the previous batch executed. This is correct behavior — it matches what the model expects.
- Risk: If the runner changes to not always take a screenshot, the adapter breaks.
- This is the simplest approach. Recommended.

### Option B — Adapter drives the inner action loop internally

`decide()` calls the API, executes all actions by calling back into the environment itself, and loops until the model is done. Returns `[DONE]` at the end.

- Trade-off: Violates the Adapter protocol's purpose (adapters decide, environments execute). Tightly couples the adapter to the environment. Breaks the step-recording in the runner.
- Not recommended.

### Option C — Adapter skips screenshot actions, natural loop handles them

When a `screenshot` action appears in `actions[]`, skip it. The runner loop's next iteration naturally collects a fresh screenshot. Treat all other actions as a batch return.

- This is what Option A already does. The `screenshot` action type just needs to be filtered out of the returned `list[Action]` (or mapped to a SCREENSHOT ActionType if the runner handles it).

**Recommendation: Option A.** The design of the runner loop (collect observation → decide → execute) already matches OpenAI's intended pattern perfectly.

---

## 12. Sources of Truth

| Area | Canonical Source | Verification Method | Drift Risk |
|---|---|---|---|
| Tool declaration format (`computer_use_preview`) | Multiple third-party implementations (RIIS, Portkey, orgo.ai) all agree | Search for OpenAI SDK changelog | **Medium** — GA `gpt-5.4` uses `"computer"` type; preview API may converge |
| Response structure (`computer_call`, `actions[]`, `call_id`) | Azure docs (March 2026) + community examples | Run a single live API call, `print(response.model_dump())` | **Low** — stable since launch |
| `computer_call_output` screenshot format (`"type": "input_image"`) | RIIS blog, Portkey docs, orgo.ai | Check against openai-python SDK `ResponseComputerToolCallOutputParam` | **Medium** — `computer_screenshot` type also appears in Azure docs |
| Safety check structure (`pending_safety_checks`, `acknowledged_safety_checks`) | Azure docs (March 2026), verbatim JSON shown | N/A, confirmed in official docs | **Low** |
| Token usage fields (`response.usage.input_tokens`, etc.) | Azure docs response JSON example (matches standard OpenAI pattern) | `response.usage.model_dump()` | **Low** |
| Pricing ($3.00/$12.00 per 1M) | economize.cloud OpenAI pricing page (April 2026), pricepertoken.com | platform.openai.com/docs/pricing | **Medium** — pricing changes periodically |
| `truncation="auto"` requirement | Multiple implementation guides | Omit it and observe if API errors | **Low** |

---

## 13. Key Divergence: Prior Memory vs. Current Findings

The prior memory file (`project_openai_computer_use.md`) recorded:
- Pricing: `$1.50 input / $6.00 output per 1M tokens (Batch API)` — **this is the Batch API rate**. Standard API is $3.00/$12.00. Both are current and correct; the memory was referring specifically to Batch API pricing.
- Tool declaration fields listed as `display_width_px`, `display_height_px` — **incorrect field names**. Confirmed correct names are `display_width` and `display_height` (no `_px` suffix).
- `computer_call.actions[]` described as batched — **confirmed correct**.
- `previous_response_id` for continuation — **confirmed correct**.
- Safety check field names — **confirmed correct**.

The prior memory should be updated to correct the field names.

---

## Sources

- [Computer Use in Azure OpenAI (classic) — Microsoft Learn (March 2026)](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/computer-use?view=foundry-classic) — Most complete worked example with full JSON
- [Building Computer Use Agents with OpenAI's API — RIIS](https://www.riis.com/blog/building-computer-use-agents-with-openai-api) — Python examples for computer-use-preview
- [Portkey OpenAI Computer Use Guide](https://portkey.ai/docs/guides/use-cases/openai-computer-use) — Confirms tool declaration and computer_call_output format
- [OpenAI computer-use-preview model page](https://developers.openai.com/api/docs/models/computer-use-preview) — Context window, pricing, API compatibility
- [Incorrect API docs thread — OpenAI Community](https://community.openai.com/t/incorrect-api-docs-for-computer-use-preview/1158712) — Corrections to field names and image format
- [Dicklesworthstone guide — GitHub](https://github.com/Dicklesworthstone/guide_to_openai_response_api_and_agents_sdk) — Confirms computer_use_preview type + environment field
- [OpenAI Computer Use docs — developers.openai.com](https://developers.openai.com/api/docs/guides/tools-computer-use) — Primary canonical source (403 from browser, accessible via API)
- [orgo.ai OpenAI Computer Use guide](https://docs.orgo.ai/guides/openai-computer-use) — Alternate action parsing pattern
