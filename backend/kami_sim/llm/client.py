"""LLM client wrapper with two-tier routing — spec §2.3, §3.2 Phase 2.

All LLM calls go through this wrapper. Supports cheap/strong tier routing
and prompt caching.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import anthropic

from ..config import config
from ..determinism import request_seed
from .budget import budget

logger = logging.getLogger(__name__)


class LLMClient:
    """Wrapper around Anthropic API with tier routing and cost tracking."""

    def __init__(self):
        self._anthropic_client = None
        self._openai_client = None
        self._gemini_client = None

    def reset_clients(self) -> None:
        self._anthropic_client = None
        self._openai_client = None
        self._gemini_client = None

    def _get_model(self, tier: str) -> tuple[str, str]:
        if tier == "cheap":
            model = config.cheap_model_name
        elif tier == "strong":
            model = config.strong_model_name
        else:
            raise ValueError(f"Unknown tier: {tier}")

        if ":" in model:
            provider, model_name = model.split(":", 1)
            return provider.strip().lower(), model_name.strip()
        return config.llm_provider.strip().lower(), model.strip()

    async def call(
        self,
        messages: list[dict],
        system: str | list[dict] = "",
        tier: str = "cheap",
        component: str = "unknown",
        tick: int | None = None,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        timeout_seconds: float | None = None,
    ) -> dict:
        """Make an LLM call with cost tracking.

        Returns dict with 'content', 'tool_calls', 'usage'.
        """
        provider, model = self._get_model(tier)
        qualified_model = f"{provider}:{model}"
        request_payload = {
            "messages": messages,
            "system": system,
            "tools": tools,
            "response_format": response_format,
        }
        payload_bytes = len(
            json.dumps(
                request_payload, ensure_ascii=False, sort_keys=True, default=str
            ).encode("utf-8")
        )
        seed = request_seed(component, tick, request_payload)
        effective_temperature = 0.0 if config.deterministic_mode else temperature
        reservation_id = budget.reserve_call(
            qualified_model,
            # One token cannot encode less than one payload byte. The fixed
            # allowance covers provider-added message framing.
            max_input_tokens=payload_bytes + 1024,
            max_output_tokens=max_tokens,
        )

        try:
            operation = self._call_with_retries(
                provider=provider,
                model=model,
                messages=messages,
                system=system,
                tools=tools,
                response_format=response_format,
                max_tokens=max_tokens,
                temperature=effective_temperature,
                component=component,
                seed=seed,
            )
            timeout = (
                config.llm_soft_timeout_seconds
                if timeout_seconds is None
                else max(0.0, float(timeout_seconds))
            )
            if timeout > 0:
                result = await asyncio.wait_for(
                    operation, timeout=timeout
                )
            else:
                result = await operation
        except Exception as exc:
            try:
                budget.record_failure(
                    model=qualified_model,
                    component=component,
                    error=exc,
                    tick=tick,
                    reservation_id=reservation_id,
                )
            except Exception:
                logger.exception("Failed to persist LLM failure accounting")
            raise

        try:
            usage = result["usage"]
            budget.record_call(
                model=qualified_model,
                component=component,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_read_tokens=usage.get("cache_read", 0),
                cache_write_tokens=usage.get("cache_write", 0),
                tick=tick,
                reservation_id=reservation_id,
            )
        except Exception:
            budget.release_reservation(reservation_id)
            raise

        return result

    async def _call_with_retries(
        self,
        *,
        provider: str,
        model: str,
        messages: list[dict],
        system: str | list[dict],
        tools: list[dict] | None,
        response_format: dict | None,
        max_tokens: int,
        temperature: float,
        component: str,
        seed: int | None,
    ) -> dict:
        attempts = max(1, config.llm_retry_attempts)
        for attempt in range(1, attempts + 1):
            try:
                return await self._dispatch_provider_call(
                    provider=provider,
                    model=model,
                    messages=messages,
                    system=system,
                    tools=tools,
                    response_format=response_format,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    component=component,
                    seed=seed,
                )
            except Exception as exc:
                if attempt >= attempts or not self._is_transient_error(exc):
                    raise
                delay = config.llm_retry_base_delay_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Retrying transient LLM failure (%s, attempt %s/%s): %s",
                    component,
                    attempt + 1,
                    attempts,
                    type(exc).__name__,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
        raise RuntimeError("LLM retry loop exhausted")

    async def _dispatch_provider_call(self, **kwargs) -> dict:
        provider = kwargs.pop("provider")
        if provider == "anthropic":
            return await self._call_anthropic(**kwargs)
        if provider == "openai":
            return await self._call_openai(**kwargs)
        if provider == "gemini":
            kwargs.pop("response_format", None)
            return await self._call_gemini(**kwargs)
        raise ValueError(
            f"Unknown LLM provider: {provider}. Use anthropic, openai, or gemini."
        )

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        status = getattr(exc, "status_code", None)
        if status in {408, 409, 425, 429} or (
            isinstance(status, int) and status >= 500
        ):
            return True
        name = type(exc).__name__.lower()
        return any(
            marker in name
            for marker in ("timeout", "ratelimit", "connection", "serviceunavailable")
        )

    async def _call_anthropic(
        self,
        model: str,
        messages: list[dict],
        system: str | list[dict],
        tools: list[dict] | None,
        response_format: dict | None,
        max_tokens: int,
        temperature: float,
        component: str,
        seed: int | None = None,
    ) -> dict:
        if self._anthropic_client is None:
            self._anthropic_client = anthropic.AsyncAnthropic(
                api_key=config.anthropic_api_key
            )

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
            response = await self._anthropic_client.messages.create(**kwargs)
        except Exception as e:
            logger.error(f"LLM call failed ({component}): {e}")
            raise

        # Extract usage
        usage = response.usage
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

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

    async def _call_openai(
        self,
        model: str,
        messages: list[dict],
        system: str | list[dict],
        tools: list[dict] | None,
        response_format: dict | None,
        max_tokens: int,
        temperature: float,
        component: str,
        seed: int | None = None,
    ) -> dict:
        if self._openai_client is None:
            from openai import AsyncOpenAI

            self._openai_client = AsyncOpenAI(api_key=config.openai_api_key)

        openai_messages = []
        system_text = self._system_to_text(system)
        if system_text:
            openai_messages.append({"role": "system", "content": system_text})
        openai_messages.extend(
            {
                "role": m.get("role", "user"),
                "content": self._content_to_text(m.get("content", "")),
            }
            for m in messages
        )

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_completion_tokens": max_tokens,
        }
        if self._openai_uses_reasoning_controls(model):
            kwargs["reasoning_effort"] = (
                "none" if tools or component == "WorldBuilder" else "low"
            )
        if not self._openai_uses_default_temperature(model):
            kwargs["temperature"] = temperature
        if seed is not None:
            kwargs["seed"] = seed
        if tools:
            kwargs["tools"] = [self._anthropic_tool_to_openai(t) for t in tools]
            if component == "WorldBuilder" and len(tools) == 1:
                kwargs["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tools[0]["name"]},
                }
            elif component == "KamiWorker":
                kwargs["tool_choice"] = "required"
        if response_format:
            kwargs["response_format"] = response_format

        try:
            response = await self._openai_client.chat.completions.create(**kwargs)
        except Exception as e:
            rejected_controls = self._rejected_openai_controls(e, kwargs)
            if rejected_controls:
                logger.warning(
                    "Retrying OpenAI call without unsupported controls %s (%s)",
                    sorted(rejected_controls),
                    component,
                )
                for control in rejected_controls:
                    kwargs.pop(control, None)
                response = await self._openai_client.chat.completions.create(**kwargs)
            else:
                logger.error(f"LLM call failed ({component}): {e}")
                raise

        choice = response.choices[0]
        message = choice.message
        tool_calls = []
        for tc in message.tool_calls or []:
            arguments = tc.function.arguments or "{}"
            try:
                parsed_args = json.loads(arguments)
            except json.JSONDecodeError:
                parsed_args = {"raw_arguments": arguments}
            tool_calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "input": parsed_args,
            })

        usage = response.usage
        return {
            "content": message.content or "",
            "tool_calls": tool_calls,
            "stop_reason": choice.finish_reason,
            "usage": {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
                "cache_read": 0,
                "cache_write": 0,
            },
        }

    async def _call_gemini(
        self,
        model: str,
        messages: list[dict],
        system: str | list[dict],
        tools: list[dict] | None,
        max_tokens: int,
        temperature: float,
        component: str,
        seed: int | None = None,
    ) -> dict:
        if self._gemini_client is None:
            from google import genai

            self._gemini_client = genai.Client(api_key=config.gemini_api_key)

        from google.genai import types

        tool_config = None
        if tools:
            tool_config = [
                types.Tool(
                    function_declarations=[
                        self._anthropic_tool_to_gemini(t) for t in tools
                    ]
                )
            ]

        contents = "\n\n".join(
            f"{m.get('role', 'user').upper()}:\n{self._content_to_text(m.get('content', ''))}"
            for m in messages
        )

        gen_config = types.GenerateContentConfig(
            system_instruction=self._system_to_text(system) or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            tools=tool_config,
            seed=seed,
        )

        try:
            response = await self._gemini_client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=gen_config,
            )
        except Exception as e:
            logger.error(f"LLM call failed ({component}): {e}")
            raise

        tool_calls = []
        for idx, fc in enumerate(getattr(response, "function_calls", None) or []):
            tool_calls.append({
                "id": f"gemini_call_{idx}",
                "name": fc.name,
                "input": dict(fc.args or {}),
            })

        usage = getattr(response, "usage_metadata", None)
        return {
            "content": getattr(response, "text", "") or "",
            "tool_calls": tool_calls,
            "stop_reason": None,
            "usage": {
                "input_tokens": getattr(usage, "prompt_token_count", 0) if usage else 0,
                "output_tokens": getattr(usage, "candidates_token_count", 0) if usage else 0,
                "cache_read": 0,
                "cache_write": 0,
            },
        }

    def _anthropic_tool_to_openai(self, tool: dict) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object"}),
            },
        }

    def _openai_uses_default_temperature(self, model: str) -> bool:
        """Newer reasoning models reject custom temperature values."""
        lowered = model.lower()
        return lowered.startswith(("gpt-5", "o1", "o3", "o4"))

    def _openai_uses_reasoning_controls(self, model: str) -> bool:
        lowered = model.lower()
        return lowered.startswith(("gpt-5", "o1", "o3", "o4"))

    @staticmethod
    def _rejected_openai_controls(exc: Exception, kwargs: dict) -> set[str]:
        message = str(exc).lower()
        rejected = set()
        for control in ("reasoning_effort", "seed"):
            if control in kwargs and control in message:
                rejected.add(control)
        return rejected

    def _anthropic_tool_to_gemini(self, tool: dict):
        from google.genai import types

        return types.FunctionDeclaration(
            name=tool["name"],
            description=tool.get("description", ""),
            parameters=tool.get("input_schema", {"type": "object"}),
        )

    def _system_to_text(self, system: str | list[dict]) -> str:
        if isinstance(system, str):
            return system
        return "\n\n".join(
            self._content_to_text(block.get("text", "")) for block in system
        )

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(str(block.get("text", block.get("content", ""))))
                else:
                    parts.append(str(block))
            return "\n".join(parts)
        return str(content)


# Global singleton
llm_client = LLMClient()
