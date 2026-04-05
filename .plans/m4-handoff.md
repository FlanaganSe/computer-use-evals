# Milestone 4 Handoff: Comparison Runs and Cheap-First Routing

## What Was Completed

1. **Cheap-first routing** added to the structured-state desktop adapter:
   - Routing is opt-in via `routing_enabled=True` constructor arg
   - Cheap tier: `gpt-4.1-mini` for steps with >= 3 interactive AX elements and no prior failure
   - Strong tier: `gpt-4.1` for sparse trees (< 3 elements), post-failure retries, and parse-failure escalation
   - Parse-failure retry: if cheap model returns unparseable JSON, automatically retries with strong model
   - Routing metadata tracked per-run: `cheap_steps`, `strong_steps`, `escalations`

2. **Routed adapter registered** as `structured_state_desktop_routed` in runner ADAPTERS via lambda factory (same pattern as `openai_cu_hybrid`)

3. **Run configs** added:
   - `structured_state_desktop.yaml` — baseline (single strong model)
   - `structured_state_desktop_routed.yaml` — cheap-first routing enabled

4. **Reporting** — "Structured-State Experiment" section added to `generate_detailed_report()` showing routing metadata alongside outcome/cost/steps

5. **Cost estimation** — routed runs use weighted blended pricing based on actual cheap/strong step ratios

6. **Tests** — 14 new tests covering:
   - Routing heuristic selection (rich tree -> cheap, sparse -> strong, post-failure -> strong)
   - Routed adapter registration and factory
   - Cost metadata with/without routing
   - Evidence includes model_used and routing_tier
   - Parse-failure retry escalation path
   - Structured-state experiment report section
   - Baseline vs routed report rendering

## What Was NOT Added (and Why)

- **Screenshot fallback**: Not justified. TextEdit (the only trusted desktop task app) has excellent AX coverage. The 33% figure from Screen2AX is across all macOS apps, not the targeted apps. No M1-M3 evidence showed AX gaps on the trusted task set.

- **Second provider/SDK**: Not justified. Both models (gpt-4.1, gpt-4.1-mini) use the existing OpenAI SDK. Introducing Anthropic would add a dependency and create cross-provider noise in the experiment.

- **Default promotion**: Explicitly deferred to M5 per plan. The structured-state path is NOT the default.

## How to Run Comparison Experiments

```bash
# Baseline structured-state (single strong model)
harness run --config run_configs/structured_state_desktop.yaml

# Routed structured-state (cheap-first)
harness run --config run_configs/structured_state_desktop_routed.yaml

# Screenshot-first baseline (legacy)
harness run --config run_configs/openai_browser.yaml

# Generate comparison report
harness report --runs-dir runs/
```

## Key Design Decisions

- **Routing is internal to the adapter**: The runner/protocol/environment are unchanged. Routing is an adapter-level concern, invisible to the harness spine.
- **Routing thresholds are simple heuristics**: sparse tree (< 3 interactive elements) or post-failure -> strong model. No trained classifier.
- **Parse-failure escalation reclassifies the step**: The cheap step count is decremented and strong step count incremented, keeping `cheap_steps + strong_steps == total routed decisions`.
- **Cost blending is proportional**: Routed runs estimate cost using a weighted blend of cheap/strong pricing based on actual step ratios, not the strong model price for all tokens.

## Experimental Questions This Enables

After running the comparison suite, the repo can now answer:
1. Did structured-state beat screenshot-first on the trusted set?
2. Did routing save enough cost without unacceptable quality loss?
3. Was fallback actually needed, or just available?

## What's Next (M5)

- Promote structured-state desktop as the documented primary desktop path
- Reword reporting/docs/config so `openai_cu` is clearly a comparison lane
- Legacy surface audit
