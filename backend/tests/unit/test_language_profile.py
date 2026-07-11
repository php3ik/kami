import pytest

from kami_sim.agent_worker.worker import AgentCognitionWorker
from kami_sim.eventbus.bus import EventBus
from kami_sim.factstore.models import Entity, Simulation, init_db
from kami_sim.kami_worker.worker import KamiWorker
from kami_sim.language import (
    get_simulation_language,
    language_instruction,
    normalize_content_language,
    structured_text_matches_language,
    text_matches_language,
)
from kami_sim.simulations.repository import SimulationRepository
from kami_sim.spatial.graph import SpatialGraph
from kami_sim.world_builder import staged


def test_language_profile_normalizes_supported_locales_and_defaults_to_english():
    assert normalize_content_language("uk-UA") == "uk"
    assert normalize_content_language("Українська") == "uk"
    assert normalize_content_language("en-GB") == "en"
    assert normalize_content_language("unsupported") == "en"
    assert "All diegetic and user-visible text must be in Ukrainian" in language_instruction("uk")
    assert text_matches_language("Олена уважно дивиться у темне вікно.", "uk")
    assert not text_matches_language("Olena looks carefully through the dark window.", "uk")
    assert structured_text_matches_language(
        {
            "event_type": "action",
            "summary": "Олена відчиняє важкі двері майстерні.",
        },
        "uk",
    )


def test_repository_persists_simulation_content_language():
    engine, factory = init_db("sqlite:///:memory:")
    try:
        repository = SimulationRepository(factory)
        record = repository.upsert({
            "id": "uk-world",
            "name": "Український світ",
            "prompt": "Світ у Карпатах",
            "content_language": "uk",
        })

        assert record["content_language"] == "uk"
        with factory() as session:
            assert get_simulation_language(session, "uk-world") == "uk"
    finally:
        engine.dispose()


def test_ukrainian_agent_and_kami_fallbacks_do_not_switch_to_english():
    engine, factory = init_db("sqlite:///:memory:")
    try:
        with factory() as session:
            session.add(Simulation(
                id="uk-world",
                name="Світ",
                prompt="Український світ",
                content_language="uk",
            ))
            session.add_all([
                Entity(
                    entity_id="agent_olena",
                    simulation_id="uk-world",
                    kind="agent",
                    canonical_name="Олена",
                    archetype={},
                ),
                Entity(
                    entity_id="kami_dim",
                    simulation_id="uk-world",
                    kind="kami",
                    canonical_name="Старий дім",
                    archetype={},
                ),
            ])
            session.commit()

            agent_result = AgentCognitionWorker(session).fallback(
                "agent_olena", reason="timeout"
            )
            kami_result = KamiWorker(
                session, EventBus(), SpatialGraph()
            ).fallback("kami_dim", 1, [])

            assert "Я завмираю" in agent_result["inner_monologue"]
            assert "Час очікування" in agent_result["inner_monologue"]
            assert "спокійна хвилина" in kami_result["narrative"]
            assert "The " not in kami_result["narrative"]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_world_builder_seed_receives_and_persists_language_contract(monkeypatch):
    captured = {}

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return {
            "content": "",
            "tool_calls": [{
                "name": "submit_world",
                "input": {
                    "world_seed": {
                        "name": "Гірська громада",
                        "premise": "Детальний опис життя гірської громади та її щоденних суперечностей.",
                    }
                },
            }],
        }

    monkeypatch.setattr(staged.llm_client, "call", fake_call)
    seed = await staged._generate_seed(
        "Громада в Карпатах", 8, "Гірська громада", "uk"
    )

    assert "must be in Ukrainian" in captured["messages"][0]["content"]
    assert seed["content_language"] == "uk"
