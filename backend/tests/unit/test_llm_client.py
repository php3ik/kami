from kami_sim.config import config
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
