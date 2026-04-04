"""
Grounding strategies: how the system locates target elements.

This is the core technical question from the research. GPA achieves 100%
grounding on their pilot tasks via graph matching. The eval measures whether
that holds across diverse real-world UIs, and what the LLM fallback adds.

Three strategies:
1. Graph matching (GPA-style): geometric + visual similarity across UI graphs
2. LLM grounding: send screen state to LLM, ask it to locate the target
3. Hybrid: graph match first, LLM fallback when confidence is low
"""

from __future__ import annotations

import abc
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Optional

from ..core.types import (
    GroundingResult,
    GroundingStrategy,
    ScreenState,
    UIElement,
    UIGraph,
    WorkflowStep,
)
from ..models.providers import ModelProvider

logger = logging.getLogger(__name__)


class GroundingProvider(abc.ABC):
    """Abstract grounding strategy."""

    @property
    @abc.abstractmethod
    def strategy(self) -> GroundingStrategy:
        ...

    @abc.abstractmethod
    async def ground(
        self,
        step: WorkflowStep,
        current_state: ScreenState,
    ) -> GroundingResult:
        """Locate the target element for this workflow step."""
        ...


# ---------------------------------------------------------------------------
# Graph matching (GPA-style, simplified for POC)
# ---------------------------------------------------------------------------

class GraphMatchGrounding(GroundingProvider):
    """GPA-inspired graph matching for element localization.

    Simplified version of the full SMC approach for the POC eval.
    Uses the key insight: match by visual/textual similarity weighted
    by geometric consistency with neighboring elements.

    Production would implement the full Sequential Monte Carlo procedure
    from the GPA paper (Section 2.3).
    """

    def __init__(self, confidence_threshold: float = 0.7, ambiguity_threshold: float = 0.5):
        self._confidence_threshold = confidence_threshold
        self._ambiguity_threshold = ambiguity_threshold

    @property
    def strategy(self) -> GroundingStrategy:
        return GroundingStrategy.GRAPH_MATCH

    async def ground(
        self,
        step: WorkflowStep,
        current_state: ScreenState,
    ) -> GroundingResult:
        start = time.monotonic()

        # Need both a template graph (from recording) and current graph
        template_graph = step.target_graph
        current_graph = current_state.ui_graph

        if template_graph is None or current_graph is None:
            return GroundingResult(
                success=False,
                strategy_used=self.strategy,
                failure_reason="missing_graph",
                latency_ms=(time.monotonic() - start) * 1000,
            )

        # Find the target element in the template
        target = self._find_target_in_template(step, template_graph)
        if target is None:
            return GroundingResult(
                success=False,
                strategy_used=self.strategy,
                failure_reason="no_target_in_template",
                latency_ms=(time.monotonic() - start) * 1000,
            )

        # Score each candidate in current graph against template target
        candidates = self._score_candidates(
            target, template_graph, current_graph
        )

        if not candidates:
            return GroundingResult(
                success=False,
                strategy_used=self.strategy,
                candidates_considered=0,
                failure_reason="no_candidates",
                latency_ms=(time.monotonic() - start) * 1000,
            )

        # Select best candidate and compute confidence
        best = candidates[0]
        ambiguity = self._compute_ambiguity(candidates)

        success = (
            best["score"] >= self._confidence_threshold
            and ambiguity < self._ambiguity_threshold
        )

        return GroundingResult(
            success=success,
            target_element=best["element"] if success else None,
            coordinates=best["element"].bbox.center if success else None,
            confidence=best["score"],
            strategy_used=self.strategy,
            candidates_considered=len(candidates),
            ambiguity_score=ambiguity,
            latency_ms=(time.monotonic() - start) * 1000,
            failure_reason=None if success else (
                "low_confidence" if best["score"] < self._confidence_threshold
                else "ambiguous"
            ),
            debug_info={
                "top_candidates": [
                    {"id": c["element"].element_id, "score": round(c["score"], 3)}
                    for c in candidates[:5]
                ],
            },
        )

    def _find_target_in_template(
        self, step: WorkflowStep, graph: UIGraph
    ) -> Optional[UIElement]:
        """Find which element in the template graph is the action target."""
        if step.action and step.action.target:
            return step.action.target
        # Fallback: match by coordinates
        if step.action and step.action.coordinates:
            cx, cy = step.action.coordinates
            for el in graph.elements:
                if el.bbox.contains_point(cx, cy):
                    return el
        return graph.elements[0] if graph.elements else None

    def _score_candidates(
        self,
        target: UIElement,
        template_graph: UIGraph,
        current_graph: UIGraph,
    ) -> list[dict[str, Any]]:
        """Score all current elements against the template target.

        Combines appearance similarity (text + visual) with geometric
        consistency from neighboring elements. This is the simplified
        version of GPA's SMC likelihood computation.
        """
        template_neighbors = template_graph.neighbors(target.element_id)
        candidates = []

        for candidate in current_graph.elements:
            # Appearance similarity
            app_score = self._appearance_similarity(target, candidate)

            # Geometric consistency with neighbors
            geo_score = self._geometric_consistency(
                target, candidate, template_neighbors,
                template_graph, current_graph
            )

            # Combined score (GPA weights appearance higher for direct match)
            combined = 0.6 * app_score + 0.4 * geo_score
            candidates.append({
                "element": candidate,
                "score": combined,
                "app_score": app_score,
                "geo_score": geo_score,
            })

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates

    def _appearance_similarity(self, a: UIElement, b: UIElement) -> float:
        """Compute appearance similarity between two elements.

        Uses text similarity (fuzzy match) and role matching.
        Production would add icon embedding cosine similarity.
        """
        score = 0.0

        # Role match
        if a.role == b.role:
            score += 0.2

        # Text similarity (simplified Levenshtein-like)
        if a.text and b.text:
            text_sim = self._fuzzy_text_match(a.text, b.text)
            score += 0.6 * text_sim
        elif not a.text and not b.text:
            score += 0.3  # Both have no text — weak match

        # Size similarity
        size_a = a.bbox.width * a.bbox.height
        size_b = b.bbox.width * b.bbox.height
        if size_a > 0 and size_b > 0:
            size_ratio = min(size_a, size_b) / max(size_a, size_b)
            score += 0.2 * size_ratio

        return min(score, 1.0)

    def _geometric_consistency(
        self,
        target: UIElement,
        candidate: UIElement,
        template_neighbors: list[UIElement],
        template_graph: UIGraph,
        current_graph: UIGraph,
    ) -> float:
        """Check if candidate's spatial context matches template target's context.

        Core GPA insight: even if the target element is ambiguous,
        its neighbors' relative positions disambiguate it.
        """
        if not template_neighbors:
            return 0.5  # No context — neutral

        current_neighbors = current_graph.neighbors(candidate.element_id)
        if not current_neighbors:
            return 0.3

        # For each template neighbor, find best match in current neighbors
        match_scores = []
        tcx, tcy = target.bbox.center
        ccx, ccy = candidate.bbox.center

        for tn in template_neighbors:
            tnx, tny = tn.bbox.center
            # Expected displacement from target
            dx_expected = tnx - tcx
            dy_expected = tny - tcy

            best_neighbor_score = 0.0
            for cn in current_neighbors:
                # Appearance match with template neighbor
                app_sim = self._appearance_similarity(tn, cn)
                if app_sim < 0.3:
                    continue

                # Displacement consistency
                cnx, cny = cn.bbox.center
                dx_actual = cnx - ccx
                dy_actual = cny - ccy

                # Gaussian tolerance for displacement difference
                disp_error = math.sqrt(
                    (dx_expected - dx_actual) ** 2 + (dy_expected - dy_actual) ** 2
                )
                # Tolerance scales with distance (GPA's σ_i formula)
                base_dist = math.sqrt(dx_expected**2 + dy_expected**2) + 0.01
                geo_sim = math.exp(-(disp_error ** 2) / (2 * (0.3 * base_dist) ** 2))

                combined = 0.5 * app_sim + 0.5 * geo_sim
                best_neighbor_score = max(best_neighbor_score, combined)

            match_scores.append(best_neighbor_score)

        # Locality-weighted average (closer neighbors weighted more)
        if not match_scores:
            return 0.3
        return sum(match_scores) / len(match_scores)

    def _fuzzy_text_match(self, a: str, b: str) -> float:
        """Simple fuzzy text matching. Production would use RapidFuzz."""
        a_lower = a.lower().strip()
        b_lower = b.lower().strip()
        if a_lower == b_lower:
            return 1.0
        if a_lower in b_lower or b_lower in a_lower:
            return 0.8

        # Character-level similarity
        common = sum(1 for c in a_lower if c in b_lower)
        total = max(len(a_lower), len(b_lower), 1)
        return common / total

    def _compute_ambiguity(self, candidates: list[dict]) -> float:
        """GPA's entropy-based ambiguity detection.

        Low entropy = clear winner. High entropy = multiple plausible candidates.
        """
        if len(candidates) < 2:
            return 0.0

        scores = [c["score"] for c in candidates[:5]]
        if max(scores) == 0:
            return 1.0

        # Softmax with temperature
        tau = 0.1
        exp_scores = [math.exp(s / tau) for s in scores]
        total = sum(exp_scores)
        probs = [e / total for e in exp_scores]

        # Normalized entropy
        entropy = -sum(p * math.log(p + 1e-10) for p in probs)
        max_entropy = math.log(len(probs))
        return entropy / max_entropy if max_entropy > 0 else 0.0


# ---------------------------------------------------------------------------
# LLM-based grounding
# ---------------------------------------------------------------------------

class LLMGrounding(GroundingProvider):
    """Use an LLM to locate UI elements.

    Two modes:
    - Vision: send screenshot, ask for coordinates
    - Structured: send accessibility tree, ask for element reference
    """

    def __init__(self, model: ModelProvider, use_vision: bool = True):
        self._model = model
        self._use_vision = use_vision

    @property
    def strategy(self) -> GroundingStrategy:
        return GroundingStrategy.LLM_VISION if self._use_vision else GroundingStrategy.LLM_STRUCTURED

    async def ground(
        self,
        step: WorkflowStep,
        current_state: ScreenState,
    ) -> GroundingResult:
        start = time.monotonic()

        if self._use_vision and current_state.screenshot_base64:
            return await self._ground_via_vision(step, current_state, start)
        elif current_state.accessibility_tree:
            return await self._ground_via_structure(step, current_state, start)
        elif current_state.ui_graph:
            return await self._ground_via_graph_description(step, current_state, start)
        else:
            return GroundingResult(
                success=False,
                strategy_used=self.strategy,
                failure_reason="no_perception_data",
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def _ground_via_vision(
        self, step: WorkflowStep, state: ScreenState, start: float
    ) -> GroundingResult:
        prompt = (
            f"I need to perform this action on a UI:\n"
            f"Step: {step.description}\n"
            f"Action type: {step.action.action_type.value if step.action else 'click'}\n\n"
            f"Look at the screenshot and identify the exact element I should interact with.\n"
            f"Return the normalized coordinates (0-1) of the center of that element.\n"
            f"Format: {{\"x\": 0.XX, \"y\": 0.YY, \"confidence\": 0.X, \"element_description\": \"...\"}}"
        )

        try:
            parsed, response = await self._model.complete_structured(
                prompt=prompt,
                schema={"x": "float", "y": "float", "confidence": "float", "element_description": "string"},
                images=[state.screenshot_base64] if state.screenshot_base64 else None,
            )

            x = float(parsed.get("x", 0))
            y = float(parsed.get("y", 0))
            conf = float(parsed.get("confidence", 0))

            return GroundingResult(
                success=conf > 0.5,
                coordinates=(x, y) if conf > 0.5 else None,
                confidence=conf,
                strategy_used=self.strategy,
                latency_ms=(time.monotonic() - start) * 1000,
                debug_info={
                    "model_response": parsed,
                    "tokens_in": response.tokens_in,
                    "tokens_out": response.tokens_out,
                    "model_cost": response.cost_usd,
                },
            )
        except Exception as e:
            logger.warning(f"LLM vision grounding failed: {e}")
            return GroundingResult(
                success=False,
                strategy_used=self.strategy,
                failure_reason=f"llm_error: {e}",
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def _ground_via_structure(
        self, step: WorkflowStep, state: ScreenState, start: float
    ) -> GroundingResult:
        """Ground using accessibility tree (much cheaper than vision)."""
        import json

        # Flatten tree to a compact representation
        tree_str = json.dumps(state.accessibility_tree, indent=1)
        # Truncate if too long
        if len(tree_str) > 8000:
            tree_str = tree_str[:8000] + "\n... (truncated)"

        prompt = (
            f"Given this UI accessibility tree:\n```\n{tree_str}\n```\n\n"
            f"Find the element for this action: {step.description}\n"
            f"Return: {{\"element_name\": \"...\", \"element_role\": \"...\", "
            f"\"confidence\": 0.X, \"reasoning\": \"...\"}}"
        )

        try:
            parsed, response = await self._model.complete_structured(
                prompt=prompt,
                schema={"element_name": "string", "element_role": "string",
                        "confidence": "float", "reasoning": "string"},
            )

            conf = float(parsed.get("confidence", 0))
            # Try to find matching element in UI graph
            target_el = None
            if state.ui_graph:
                name = parsed.get("element_name", "")
                role = parsed.get("element_role", "")
                for el in state.ui_graph.elements:
                    if (el.name and name.lower() in el.name.lower()) or \
                       (el.text and name.lower() in el.text.lower()):
                        if not role or el.role.lower() == role.lower():
                            target_el = el
                            break

            return GroundingResult(
                success=conf > 0.5,
                target_element=target_el,
                coordinates=target_el.bbox.center if target_el else None,
                confidence=conf,
                strategy_used=GroundingStrategy.LLM_STRUCTURED,
                latency_ms=(time.monotonic() - start) * 1000,
                debug_info={
                    "model_response": parsed,
                    "tokens_in": response.tokens_in,
                    "tokens_out": response.tokens_out,
                    "model_cost": response.cost_usd,
                },
            )
        except Exception as e:
            logger.warning(f"LLM structured grounding failed: {e}")
            return GroundingResult(
                success=False,
                strategy_used=GroundingStrategy.LLM_STRUCTURED,
                failure_reason=f"llm_error: {e}",
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def _ground_via_graph_description(
        self, step: WorkflowStep, state: ScreenState, start: float
    ) -> GroundingResult:
        """Fallback: describe the UI graph to the LLM textually."""
        if not state.ui_graph:
            return GroundingResult(
                success=False, strategy_used=self.strategy,
                failure_reason="no_graph", latency_ms=(time.monotonic() - start) * 1000,
            )

        elements_desc = "\n".join(
            f"- [{el.element_id}] {el.role}: '{el.text or el.name or '(no text)'}' "
            f"at ({el.bbox.x:.2f}, {el.bbox.y:.2f})"
            for el in state.ui_graph.elements[:50]
        )

        prompt = (
            f"UI elements on screen:\n{elements_desc}\n\n"
            f"Which element should I interact with for: {step.description}\n"
            f"Return: {{\"element_id\": \"...\", \"confidence\": 0.X}}"
        )

        try:
            parsed, response = await self._model.complete_structured(
                prompt=prompt,
                schema={"element_id": "string", "confidence": "float"},
            )

            eid = parsed.get("element_id", "")
            conf = float(parsed.get("confidence", 0))
            target = next(
                (el for el in state.ui_graph.elements if el.element_id == eid),
                None
            )

            return GroundingResult(
                success=conf > 0.5 and target is not None,
                target_element=target,
                coordinates=target.bbox.center if target else None,
                confidence=conf,
                strategy_used=self.strategy,
                latency_ms=(time.monotonic() - start) * 1000,
                debug_info={"model_response": parsed},
            )
        except Exception as e:
            return GroundingResult(
                success=False, strategy_used=self.strategy,
                failure_reason=f"llm_error: {e}",
                latency_ms=(time.monotonic() - start) * 1000,
            )


# ---------------------------------------------------------------------------
# Hybrid grounding (production strategy)
# ---------------------------------------------------------------------------

class HybridGrounding(GroundingProvider):
    """Graph match first, LLM fallback when confidence is low.

    This mirrors the product design: deterministic replay (graph matching)
    is the default; AI is only invoked when something breaks. The eval
    measures how often the fallback is needed and whether it helps.
    """

    def __init__(
        self,
        model: ModelProvider,
        graph_confidence_threshold: float = 0.7,
        graph_ambiguity_threshold: float = 0.5,
    ):
        self._graph = GraphMatchGrounding(
            confidence_threshold=graph_confidence_threshold,
            ambiguity_threshold=graph_ambiguity_threshold,
        )
        self._llm = LLMGrounding(model, use_vision=True)
        self._llm_structured = LLMGrounding(model, use_vision=False)

    @property
    def strategy(self) -> GroundingStrategy:
        return GroundingStrategy.HYBRID_GRAPH_LLM

    async def ground(
        self,
        step: WorkflowStep,
        current_state: ScreenState,
    ) -> GroundingResult:
        # Try graph matching first
        result = await self._graph.ground(step, current_state)

        if result.success:
            return result

        # Graph matching failed — try LLM
        logger.info(
            f"Graph matching failed for step {step.step_number} "
            f"(confidence={result.confidence:.2f}, reason={result.failure_reason}). "
            f"Falling back to LLM."
        )

        # Prefer structured (cheaper) if a11y tree available
        if current_state.accessibility_tree:
            llm_result = await self._llm_structured.ground(step, current_state)
        else:
            llm_result = await self._llm.ground(step, current_state)

        llm_result.fallback_used = True
        llm_result.debug_info["graph_result"] = {
            "confidence": result.confidence,
            "ambiguity": result.ambiguity_score,
            "failure_reason": result.failure_reason,
        }

        return llm_result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_grounding(
    strategy: GroundingStrategy,
    model: Optional[ModelProvider] = None,
    **kwargs: Any,
) -> GroundingProvider:
    if strategy == GroundingStrategy.GRAPH_MATCH:
        return GraphMatchGrounding(**kwargs)
    elif strategy in (GroundingStrategy.LLM_VISION, GroundingStrategy.LLM_STRUCTURED):
        if model is None:
            raise ValueError("LLM grounding requires a model provider")
        use_vision = strategy == GroundingStrategy.LLM_VISION
        return LLMGrounding(model, use_vision=use_vision)
    elif strategy == GroundingStrategy.HYBRID_GRAPH_LLM:
        if model is None:
            raise ValueError("Hybrid grounding requires a model provider")
        return HybridGrounding(model, **kwargs)
    else:
        raise ValueError(f"Unknown grounding strategy: {strategy}")
