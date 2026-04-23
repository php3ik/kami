"""LLM client wrapper with two-tier routing — spec §2.3, §3.2 Phase 2.

All LLM calls go through this wrapper. Supports cheap/strong tier routing
and prompt caching.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from ..config import config
from .budget import budget

logger = logging.getLogger(__name__)


class LLMClient:
    """Wrapper around Anthropic API with tier routing and cost tracking."""

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def _get_model(self, tier: str) -> str:
        if tier == "cheap":
            return config.cheap_model_name
        elif tier == "strong":
            return config.strong_model_name
        else:
            raise ValueError(f"Unknown tier: {tier}")

    async def call(
        self,
        messages: list[dict],
        system: str | list[dict] = "",
        tier: str = "cheap",
        component: str = "unknown",
        tick: int | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> dict:
        """Make an LLM call with cost tracking.

        Returns dict with 'content', 'tool_calls', 'usage'.
        """
        model = self._get_model(tier)

        # Build kwargs
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        # System prompt — support cache_control markers
        if isinstance(system, list):
            kwargs["system"] = system
        elif system:
            if config.prompt_cache_enabled:
                kwargs["system"] = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                kwargs["system"] = system

        if tools:
            kwargs["tools"] = tools

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as e:
            logger.error(f"LLM call failed ({component}): {e}")
            raise

        # Extract usage
        usage = response.usage
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

        # Track cost
        budget.record_call(
            model=model,
            component=component,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            tick=tick,
        )

        # Parse response
        content_text = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return {
            "content": content_text,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read": cache_read,
                "cache_write": cache_write,
            },
        }


# Global singleton
llm_client = LLMClient()
