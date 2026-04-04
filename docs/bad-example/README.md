# GPA Eval Harness

**A proof-of-concept evaluation framework for GUI Process Automation.**

Tests whether browser and desktop workflows can be reliably recorded, grounded, and replayed — across different perception modes, grounding strategies, and LLM backends.

## What This Is For

Before building a product, you need answers to these questions:

| Question | What the harness measures |
|---|---|
| Can we reliably find UI elements? | Grounding confidence, ambiguity, success rate |
| Does graph matching (GPA-style) actually work? | Graph match vs LLM grounding comparison |
| How often is AI fallback needed? | Fallback rate, fallback success rate |
| What breaks, and why? | Failure taxonomy (grounding, timing, auth, anti-bot) |
| What does it cost per workflow? | Token usage, API cost, model calls |
| How fast is it? | Latency per step, per workflow, by strategy |
| Does it work across perception modes? | Screenshot vs a11y tree vs UI graph vs hybrid |
| Does it work across models? | Anthropic vs OpenAI comparison |
| Do long workflows degrade? | Success rate by workflow length |
| Are accessibility trees available? | A11y availability rate across apps |

## Architecture

The framework decomposes GUI automation into four independent dimensions. You can vary any one while holding the others constant:

```
Perception              Grounding              Model               Execution
─────────              ─────────              ─────                ─────────
Screenshot ─────┐      Graph Match ────┐      Anthropic ────┐     Playwright
A11y Tree ──────┼──→   LLM Vision ─────┼──→   OpenAI ───────┼──→  OS Automation
UI Graph ───────┤      LLM Structured ─┤      (BYOK) ───────┘     Dry Run
Hybrid ─────────┘      Hybrid ─────────┘
```

### Key abstractions

**`PerceptionProvider`** — How the system sees the screen.
- `ScreenshotPerception`: Raw pixels. Universal but expensive (~50K tokens/frame).
- `AccessibilityTreePerception`: OS a11y APIs. Cheap (~4K tokens) but not always available.
- `UIGraphPerception`: GPA-style element graph. Compact, enables geometric matching.
- `HybridPerception`: All of the above. Measures which channel provides value.

**`GroundingProvider`** — How the system locates target elements.
- `GraphMatchGrounding`: GPA-style geometric + visual similarity matching. No LLM needed.
- `LLMGrounding`: Send screen state to LLM, ask for coordinates/element.
- `HybridGrounding`: Graph match first, LLM fallback on low confidence. Production strategy.

**`ModelProvider`** — Which LLM handles interpretation and fallback.
- `AnthropicProvider`: Claude via Messages API.
- `OpenAIProvider`: GPT via Chat Completions API. Supports subscription-based keys.

**`EvalHarness`** — Orchestrates perception → grounding → execution → measurement.

### The "harness engineering" insight

The way you present screen state to a grounding strategy fundamentally determines what it can do. This framework makes that explicit:

- **Graph matching** needs a `UIGraph` — it never sees raw pixels.
- **LLM vision grounding** needs a screenshot — it can't use structured data.
- **LLM structured grounding** needs an accessibility tree — cheaper than vision.
- **Hybrid** gets everything and measures what each channel contributes.

This is not just plumbing. It's the key experimental variable.

### Failure taxonomy

The harness classifies every step outcome into a specific failure mode:

```python
class StepOutcome(Enum):
    SUCCESS
    GROUNDING_FAILURE        # Couldn't find element
    GROUNDING_AMBIGUOUS      # Multiple plausible candidates
    GROUNDING_WRONG          # Found wrong element
    READINESS_TIMEOUT        # Screen not in expected state
    ACTION_FAILED            # Element found but action failed
    STATE_MISMATCH           # Post-action state unexpected
    NAVIGATION_ERROR         # Wrong page/app
    AUTH_BLOCKED             # SSO/MFA/CAPTCHA
    ANTI_BOT_BLOCKED         # Anti-automation detection
    TIMING_ERROR             # Race condition / animation
    HEALED                   # Failed initially, AI fixed it
    SKIPPED                  # Step not applicable
```

Different failure modes have different product implications:
- `GROUNDING_FAILURE` → need better element detection
- `AUTH_BLOCKED` → need human handoff for MFA
- `TIMING_ERROR` → need better wait/retry logic
- `ANTI_BOT_BLOCKED` → some sites won't work, period

## Setup

```bash
# Clone and install
pip install -e ".[all]"

# Install Playwright browsers
playwright install chromium

# Set API keys (at least one)
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
```

## Usage

### Quick single eval
```bash
python scripts/run_eval.py --perception hybrid --grounding hybrid_graph_llm
```

### Grounding-only eval (no browser needed)
```bash
python scripts/run_eval.py --grounding-only
```

### Dry run (perception + grounding, no action execution)
```bash
python scripts/run_eval.py --dry-run
```

### Full comparison suite
```bash
python scripts/run_eval.py --suite
```

### Programmatic usage
```python
import asyncio
from gpa_eval.core.types import *
from gpa_eval.eval.harness import quick_eval
from gpa_eval.eval.metrics import generate_report

workflow = Workflow(
    name="My Test",
    target_url="https://example.com",
    steps=[
        WorkflowStep(
            step_number=1,
            description="Click the 'More information' link",
            action=Action(action_type=ActionType.CLICK),
        ),
    ],
)

result = asyncio.run(quick_eval(workflow, dry_run=True))
print(generate_report([result]))
```

## What to Evaluate First

### Phase 1: Grounding accuracy (no execution needed)
Record screen states from target apps. Run grounding strategies against them.
This answers: "Can we reliably find the elements we need?"

### Phase 2: Replay reliability
Execute simple browser workflows end-to-end. Measure success rate.
This answers: "Does deterministic replay work for real sites?"

### Phase 3: Self-healing rate
Intentionally change UI (resize window, use different theme).
Measure how often graph matching still works, and when LLM fallback helps.
This answers: "How brittle is replay, and does AI repair work?"

### Phase 4: Cross-platform
Run the same workflows on different OSes.
Measure accessibility tree availability and perception quality.
This answers: "Can we extend beyond browser to desktop?"

## Unknown-Unknowns This Framework Surfaces

The harness is designed to reveal things you didn't know to look for:

- **Grounding confidence distribution**: Not just mean, but p10 and p50. If the tail is fat, you'll have unpredictable failures.
- **Ambiguity score**: How often are there multiple plausible targets? This determines whether graph matching alone is sufficient.
- **Fallback value**: When the LLM fallback fires, does it actually help? If fallback_success_rate is low, the AI repair feature isn't worth building yet.
- **A11y availability**: What fraction of screens have usable accessibility trees? Below 50% means you can't rely on structured perception.
- **Failure position**: Do workflows fail early (bad initial state), mid (dynamic content), or late (accumulated state drift)? This is context rot manifesting.
- **Cost scaling**: Does cost per step increase as workflows get longer? If so, you have a context management problem.
- **Elements per screen**: Dense UIs (>100 elements) stress graph matching differently than sparse ones (<20). The harness tracks this.

## Extending

### Add a new perception mode
Subclass `PerceptionProvider`, implement `capture()`, register in the factory.

### Add a new grounding strategy
Subclass `GroundingProvider`, implement `ground()`, register in the factory.

### Add a new model provider
Subclass `ModelProvider`, implement `complete()` and `complete_structured()`.

### Add a new workflow
Create a `Workflow` object with `WorkflowStep` entries. Each step needs:
- A description (human-readable intent)
- An action type (click, type, etc.)
- Optionally: a target UI graph for graph matching

### Add desktop workflows
The perception layer already supports macOS, Windows, and Linux accessibility
APIs. You'll need to implement an `ActionExecutor` for desktop (PyAutoGUI,
pyobjc, or platform-specific automation APIs).

## Research References

This framework is informed by:

- **GPA (Salesforce, 2026)**: UI graph representation, SMC-based grounding, readiness checking, deterministic replay
- **Screen2AX (MacPaw, 2025)**: Generating accessibility trees from screenshots when native a11y is unavailable
- **Context Folding (2026)**: Managing context for long-horizon agent tasks
- **AgentProg (2025)**: Program-guided context management for GUI agents
- **UFO³ (Microsoft, 2025-2026)**: Multi-device orchestration for desktop automation
- **API vs GUI Agents (2025)**: Hybrid approaches combining structured APIs with GUI interaction
