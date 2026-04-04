---
name: OpenAI Computer-Use Responses API — key technical facts
description: Canonical facts about the OpenAI computer-use-preview model, Responses API loop, Codex MCP server, and pricing as of April 2026
type: project
---

The dedicated model is `computer-use-preview` (also `gpt-5.4` in Azure docs). It is Responses-API-only.

Key structural difference from Anthropic: OpenAI batches actions — `computer_call.actions[]` is an array; Anthropic returns one action per turn.

Continuation uses `previous_response_id` (server-side history).

Safety checks (`pending_safety_checks`) must be acknowledged via `acknowledged_safety_checks` in the next request before the loop can continue.

Codex CLI runs as an MCP server via `codex mcp-server` (stdio JSON-RPC). Exposes a `codex` tool (and `codex-reply` for thread continuation). Multiple threads share one connection via `threadId` metadata. It is a code agent, not a computer-use execution engine.

Pricing (Batch API): `computer-use-preview` at $1.50 input / $6.00 output per 1M tokens. Rough cost: ~$0.015-0.025/run for a 10-step eval task.

ChatGPT Pro ($200/mo) gates Operator (managed cloud browser), not raw API access. For automated harness: use API keys.

**Why:** Needed for Phase 1 browser eval loop implementation.
**How to apply:** Use `computer-use-preview` with Playwright; budget ~$15-25/1K runs; don't conflate Codex MCP with computer-use.
