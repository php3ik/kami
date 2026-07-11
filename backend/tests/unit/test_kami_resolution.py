import pytest

from kami_sim.eventbus.bus import EventBus
from kami_sim.factstore import tools as fs
from kami_sim.factstore.models import init_db
from kami_sim.kami_worker import worker as worker_module
from kami_sim.kami_worker.worker import KamiWorker
from kami_sim.spatial.graph import SpatialGraph


def _world():
    engine, factory = init_db("sqlite:///:memory:")
    session = factory()
    fs.create_entity(session, "kami", "Workshop", 0, entity_id="kami_workshop")
    fs.create_entity(session, "agent", "Ari", 0, entity_id="agent_ari")
    fs.place_entity(session, "agent_ari", "kami_workshop", 0)
    session.commit()
    graph = SpatialGraph()
    graph.add_kami("kami_workshop", name="Workshop", kind="workshop")
    return engine, session, graph


def test_kami_parser_uses_factual_summary_and_preserves_causes():
    engine, session, graph = _world()
    try:
        worker = KamiWorker(session, EventBus(), graph)
        result = worker._parse_response(
            {
                "content": "Uncommitted literary prose must not win.",
                "tool_calls": [{
                    "name": "emit_event",
                    "input": {
                        "event_type": "repair",
                        "participants": ["agent_ari"],
                        "summary": "Ari secures the loose housing.",
                        "salience": 0.6,
                        "causes": ["evt_fault"],
                    },
                }],
            },
            "kami_workshop",
            3,
            [],
        )

        assert result["narrative"] == "Ari secures the loose housing."
        assert result["events"][0]["causes"] == ["evt_fault"]
        assert result["events"][0]["payload"]["resolution_summary"] == (
            "Ari secures the loose housing."
        )
    finally:
        session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_committed_renderer_uses_separate_tool_call(monkeypatch):
    engine, session, graph = _world()
    calls = []

    async def fake_call(**kwargs):
        calls.append(kwargs)
        return {
            "content": "",
            "tool_calls": [{
                "name": "submit_narrative",
                "input": {
                    "narrative": "Ari braces the housing against the workshop bench. The loosened fitting finally holds."
                },
            }],
        }

    monkeypatch.setattr(worker_module.llm_client, "call", fake_call)
    try:
        worker = KamiWorker(session, EventBus(), graph)
        narrative = await worker.render_committed_narrative(
            "kami_workshop",
            3,
            {"mutations": [{"type": "change_state", "entity_id": "agent_ari", "attribute": "activity", "new_value": "repairing"}]},
            [{
                "event_id": "evt_1",
                "event_type": "repair",
                "participants": ["agent_ari"],
                "narrative": "Ari secures the loose housing.",
                "salience": 0.6,
                "causes": [],
            }],
        )

        assert "finally holds" in narrative
        assert calls[0]["component"] == "NarrativeRenderer"
        assert calls[0]["tier"] == "cheap"
        assert calls[0]["max_tokens"] == 320
    finally:
        session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_idle_committed_event_skips_narrative_llm(monkeypatch):
    engine, session, graph = _world()

    async def unexpected_call(**kwargs):
        raise AssertionError("idle narration must not call the LLM")

    monkeypatch.setattr(worker_module.llm_client, "call", unexpected_call)
    try:
        worker = KamiWorker(session, EventBus(), graph)
        narrative = await worker.render_committed_narrative(
            "kami_workshop",
            4,
            {"mutations": []},
            [{
                "event_id": "evt_idle",
                "event_type": "idle",
                "participants": [],
                "narrative": "The workshop remains quiet.",
                "salience": 0.1,
            }],
        )
        assert narrative == "The workshop remains quiet."
    finally:
        session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_empty_scene_skips_kami_adjudication_llm(monkeypatch):
    engine, session, graph = _world()

    async def unexpected_call(**kwargs):
        raise AssertionError("empty scenes must resolve deterministically")

    monkeypatch.setattr(worker_module.llm_client, "call", unexpected_call)
    try:
        worker = KamiWorker(session, EventBus(), graph)
        result = await worker.render_tick("kami_workshop", 5, [])

        assert result["deterministic_idle"] is True
        assert result["events"][0]["event_type"] == "idle"
        assert result["fallback"] is False
    finally:
        session.close()
        engine.dispose()


def test_meta_event_summary_uses_place_specific_factual_fallback():
    engine, session, graph = _world()
    try:
        worker = KamiWorker(session, EventBus(), graph)
        result = worker._parse_response(
            {
                "content": "",
                "tool_calls": [{
                    "name": "emit_event",
                    "input": {
                        "event_type": "idle",
                        "summary": "No agent intents or tool calls were available.",
                        "salience": 0.1,
                    },
                }],
            },
            "kami_workshop",
            5,
            [],
        )

        assert result["narrative"] == "Nothing changes in Workshop during this tick."
    finally:
        session.close()
        engine.dispose()
