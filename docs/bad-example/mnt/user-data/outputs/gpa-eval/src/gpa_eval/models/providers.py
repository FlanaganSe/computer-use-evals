"""
LLM provider abstraction.

Supports Anthropic (API key or default) and OpenAI (API key or subscription).
Tracks token usage and cost per call for eval reporting.

The model layer is used for:
1. Workflow interpretation (recording → semantic steps)
2. Grounding fallback (when graph matching fails)
3. Self-healing (when replay detects a UI change)
4. State verification (checking post-action assertions)
"""

from __future__ import annotations

import abc
import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ModelResponse:
    """Standardized response from any model provider."""
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    raw_response: Optional[Any] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ModelProvider(abc.ABC):
    """Abstract LLM provider."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        ...

    @property
    @abc.abstractmethod
    def model_id(self) -> str:
        ...

    @abc.abstractmethod
    async def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        images: Optional[list[str]] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_format: Optional[str] = None,
    ) -> ModelResponse:
        """Send a completion request. Images are base64-encoded."""
        ...

    @abc.abstractmethod
    async def complete_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        system: Optional[str] = None,
        images: Optional[list[str]] = None,
    ) -> tuple[dict[str, Any], ModelResponse]:
        """Send a completion request expecting JSON conforming to schema."""
        ...


# ---------------------------------------------------------------------------
# Cost tables (approximate, for eval tracking)
# ---------------------------------------------------------------------------

# Per million tokens
COST_TABLE: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.0, "output": 8.0},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o3-mini": {"input": 1.10, "output": 4.40},
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    costs = COST_TABLE.get(model, {"input": 3.0, "output": 15.0})
    return (tokens_in * costs["input"] + tokens_out * costs["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------

class AnthropicProvider(ModelProvider):
    """Anthropic Claude via the Messages API."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        api_key: Optional[str] = None,
    ):
        self._model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model_id(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
            except ImportError:
                raise RuntimeError("Install anthropic: pip install anthropic")
        return self._client

    async def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        images: Optional[list[str]] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_format: Optional[str] = None,
    ) -> ModelResponse:
        client = self._get_client()
        start = time.monotonic()

        # Build content blocks
        content: list[dict] = []
        if images:
            for img_b64 in images:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64,
                    },
                })
        content.append({"type": "text", "text": prompt})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            kwargs["system"] = system

        response = await client.messages.create(**kwargs)

        text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        return ModelResponse(
            text=text,
            model=self._model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=estimate_cost(self._model, tokens_in, tokens_out),
            latency_ms=(time.monotonic() - start) * 1000,
            raw_response=response,
        )

    async def complete_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        system: Optional[str] = None,
        images: Optional[list[str]] = None,
    ) -> tuple[dict[str, Any], ModelResponse]:
        system_with_json = (system or "") + (
            "\n\nRespond with valid JSON only. No markdown, no explanation. "
            f"Conform to this schema: {json.dumps(schema)}"
        )
        response = await self.complete(
            prompt=prompt,
            system=system_with_json.strip(),
            images=images,
            temperature=0.0,
        )
        # Parse JSON from response
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        parsed = json.loads(text)
        return parsed, response


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------

class OpenAIProvider(ModelProvider):
    """OpenAI via the Chat Completions API.

    Supports both API key auth and subscription-based access.
    For subscription users, the API key comes from their account settings.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        organization: Optional[str] = None,
    ):
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._organization = organization or os.environ.get("OPENAI_ORG_ID")
        self._client = None

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model_id(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            try:
                import openai
                kwargs: dict[str, Any] = {}
                if self._api_key:
                    kwargs["api_key"] = self._api_key
                if self._organization:
                    kwargs["organization"] = self._organization
                self._client = openai.AsyncOpenAI(**kwargs)
            except ImportError:
                raise RuntimeError("Install openai: pip install openai")
        return self._client

    async def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        images: Optional[list[str]] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_format: Optional[str] = None,
    ) -> ModelResponse:
        client = self._get_client()
        start = time.monotonic()

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})

        # Build user message with optional images
        content: list[dict] = []
        if images:
            for img_b64 in images:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_b64}",
                        "detail": "high",
                    },
                })
        content.append({"type": "text", "text": prompt})
        messages.append({"role": "user", "content": content})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        response = await client.chat.completions.create(**kwargs)

        text = response.choices[0].message.content or ""
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0

        return ModelResponse(
            text=text,
            model=self._model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=estimate_cost(self._model, tokens_in, tokens_out),
            latency_ms=(time.monotonic() - start) * 1000,
            raw_response=response,
        )

    async def complete_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        system: Optional[str] = None,
        images: Optional[list[str]] = None,
    ) -> tuple[dict[str, Any], ModelResponse]:
        system_with_json = (system or "") + (
            "\n\nRespond with valid JSON only. No markdown, no explanation. "
            f"Conform to this schema: {json.dumps(schema)}"
        )
        response = await self.complete(
            prompt=prompt,
            system=system_with_json.strip(),
            images=images,
            temperature=0.0,
            response_format="json",
        )
        parsed = json.loads(response.text)
        return parsed, response


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_model(
    provider: str = "anthropic",
    model: Optional[str] = None,
    **kwargs: Any,
) -> ModelProvider:
    """Create a model provider.

    Args:
        provider: "anthropic" or "openai"
        model: Model ID (uses sensible default if omitted)
        **kwargs: Passed to provider constructor (api_key, organization, etc.)
    """
    if provider == "anthropic":
        return AnthropicProvider(
            model=model or "claude-sonnet-4-20250514",
            **kwargs,
        )
    elif provider == "openai":
        return OpenAIProvider(
            model=model or "gpt-4o",
            **kwargs,
        )
    else:
        raise ValueError(f"Unknown model provider: {provider}")
