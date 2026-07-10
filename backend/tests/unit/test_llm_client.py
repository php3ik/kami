from types import SimpleNamespace

import pytest

from kami_sim.config import config
from kami_sim.factstore.models import LLMCall, init_db
from kami_sim.llm import client as client_module
from kami_sim.llm.budget import BudgetTracker
from kami_sim.llm.client import LLMClient


def test_get_model_uses_global_provider(monkeypatch):
    monkeypatch.setattr(config, "llm_provider", "openai")
    monkeypatch.setattr(config, "cheap_model_name", "gpt-4.1-mini")

    provider, model = LLMClient()._get_model("cheap")

    assert provider == "openai"
    assert model == "gpt-4.1-mini"


def test_get_model_allows_provider_prefix(monkeypatch):
    monkeypatch.setattr(config, "llm_provider", "anthropic")
    monkeypatch.setattr(config, "strong_model_name", "gemini:gemini-2.5-pro")

    provider, model = LLMClient()._get_model("strong")

    assert provider == "gemini"
    assert model == "gemini-2.5-pro"


def test_anthropic_tool_schema_converts_to_openai_function_tool():
    tool = {
        "name": "intend",
        "description": "Declare an intent.",
        "input_schema": {
            "type": "object",
            "properties": {"action_type": {"type": "string"}},
            "required": ["action_type"],
        },
    }

    converted = LLMClient()._anthropic_tool_to_openai(tool)

    assert converted == {
        "type": "function",
        "function": {
            "name": "intend",
            "description": "Declare an intent.",
            "parameters": tool["input_schema"],
        },
    }


def test_anthropic_tool_schema_converts_to_gemini_function_declaration():
    tool = {
        "name": "intend",
        "description": "Declare an intent.",
        "input_schema": {
            "type": "object",
            "properties": {"action_type": {"type": "string"}},
            "required": ["action_type"],
        },
    }

    converted = LLMClient()._anthropic_tool_to_gemini(tool)

    assert converted.name == "intend"
    assert converted.description == "Declare an intent."


@pytest.mark.asyncio
async def test_openai_reasoning_model_uses_none_effort_with_tools():
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="ok", tool_calls=[]),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
            )

    client = LLMClient()
    client._openai_client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    tool = {
        "name": "integrate_insights",
        "description": "Integrate memories.",
        "input_schema": {"type": "object", "properties": {}},
    }

    await client._call_openai(
        model="gpt-5.5",
        messages=[{"role": "user", "content": "Reflect."}],
        system="",
        tools=[tool],
        response_format=None,
        max_tokens=32,
        temperature=0.2,
        component="MemoryConsolidator",
    )

    assert len(calls) == 1
    assert calls[0]["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_call_reserves_and_persists_provider_usage(monkeypatch):
    engine, factory = init_db("sqlite:///:memory:")
    tracker = BudgetTracker()
    tracker.configure(factory)
    monkeypatch.setattr(client_module, "budget", tracker)
    monkeypatch.setattr(config, "llm_provider", "openai")
    monkeypatch.setattr(config, "cheap_model_name", "gpt-test")
    client = LLMClient()

    async def fake_call(*args, **kwargs):
        return {
            "content": "ok",
            "tool_calls": [],
            "usage": {"input_tokens": 12, "output_tokens": 4},
        }

    monkeypatch.setattr(client, "_call_openai", fake_call)
    try:
        with tracker.scope("sim-a"):
            result = await client.call(
                [{"role": "user", "content": "hello"}],
                component="AgentWorker",
                tick=7,
                max_tokens=16,
            )

        assert result["content"] == "ok"
        with factory() as session:
            call = session.query(LLMCall).one()
        assert call.simulation_id == "sim-a"
        assert call.provider == "openai"
        assert call.tick == 7
        assert call.status == "completed"
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_call_persists_provider_failure(monkeypatch):
    engine, factory = init_db("sqlite:///:memory:")
    tracker = BudgetTracker()
    tracker.configure(factory)
    monkeypatch.setattr(client_module, "budget", tracker)
    monkeypatch.setattr(config, "llm_provider", "openai")
    monkeypatch.setattr(config, "cheap_model_name", "gpt-test")
    client = LLMClient()

    async def failing_call(*args, **kwargs):
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(client, "_call_openai", failing_call)
    try:
        with tracker.scope("sim-a"), pytest.raises(TimeoutError):
            await client.call(
                [{"role": "user", "content": "hello"}],
                component="AgentWorker",
            )

        with factory() as session:
            call = session.query(LLMCall).one()
        assert call.status == "failed"
        assert call.error_type == "TimeoutError"
        assert call.cost_usd == 0
    finally:
        engine.dispose()
