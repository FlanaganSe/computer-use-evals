---
name: Inspect AI framework research
description: Key facts about Inspect AI (version, architecture, computer-use support, macOS limitations, recommendation) as of April 2026
type: project
---

Inspect AI is the UK AISI's open-source LLM/agent eval framework. Latest PyPI version: 0.3.199 (March 17, 2026). MIT license, Python >= 3.10.

Architecture: Dataset (Samples) → Solver (TaskState transforms) → Scorer. Maps cleanly to this project's five objects (task/trial/trace/grader/report).

**Why:** Inspect is recommended as a Milestone 3+ integration target, not a day-1 dependency. The decision is documented in `.plans/plan.md` Milestone 3.

**How to apply:** When multi-model comparison and parallel eval runs become the bottleneck (post-Milestone 2), Inspect AI adoption is low-friction because our Scorer/TaskLoader abstractions are compatible with its @scorer/@dataset interfaces. Do not adopt before the custom harness loop is proven.

Key facts:
- Built-in `computer()` tool: Ubuntu 22.04 Docker, VNC observable, auto-binds to Anthropic/OpenAI/Gemini computer-use APIs. Does NOT support macOS.
- Custom @tool: any async Python function. macOS AX, ScreenCaptureKit, Playwright, Cua SDK all viable as custom tools — Inspect orchestrates calls.
- Custom @scorer: trivial to write. model_graded_qa() supports multi-model voting, partial credit, custom rubrics.
- Custom @agent: moderate effort. AgentState + generate_loop() for tool-use loop.
- Custom sandbox: SandboxEnvironment interface. No macOS adapter exists in the registry (as of April 2026). Cua adapter would need to be built.
- Extensions ecosystem: 30+ integrations, 1000+ evals in inspect-evals package.
- Observability: automatic JSONL trace per run (messages, tool calls, screenshots, costs). Inspect View web dashboard.
- Multi-model support: single CLI/API over OpenAI, Anthropic, Google, Groq, Mistral, local models.

macOS desktop conclusion: Inspect AI does not reduce macOS implementation work. All macOS-specific code (AX, ScreenCaptureKit, executor, env setup) must be custom regardless. Inspect provides orchestration, not the macOS environment layer.
