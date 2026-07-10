import pytest

from kami_sim.agent_worker.prompt_builder import build_agent_prompt
from kami_sim.config import config
from kami_sim.factstore import tools as fs
from kami_sim.factstore.models import EpisodicMemoryRecord, SemanticInsight, init_db
from kami_sim.memory import memory_runtime
from kami_sim.memory.consolidator import MemoryConsolidator
from kami_sim.memory.episodic_store import EpisodicMemory, EpisodicStore
from kami_sim.memory.runtime import MemoryRuntime


def _memory_world():
    engine, factory = init_db("sqlite:///:memory:")
    with factory() as session:
        fs.create_entity(
            session,
            "agent",
            "Ari",
            0,
            entity_id="sim_alpha__agent_ari",
            simulation_id="alpha",
        )
        fs.create_entity(
            session,
            "agent",
            "Ben",
            0,
            entity_id="sim_beta__agent_ben",
            simulation_id="beta",
        )
        session.commit()
    return engine, factory


def test_episodic_store_persists_and_isolates_simulations(tmp_path):
    engine, factory = _memory_world()
    chroma_path = tmp_path / "chroma"
    try:
        store = EpisodicStore(factory, chroma_path)
        store.add_memory(
            EpisodicMemory(
                memory_id="mem_alpha",
                simulation_id="alpha",
                agent_id="sim_alpha__agent_ari",
                tick=4,
                content="Ari repaired the copper radio beside the window.",
            )
        )
        store.add_memory(
            EpisodicMemory(
                memory_id="mem_beta",
                simulation_id="beta",
                agent_id="sim_beta__agent_ben",
                tick=4,
                content="Ben planted basil in the greenhouse.",
            )
        )

        restarted = EpisodicStore(factory, chroma_path)
        alpha = restarted.recall(
            "sim_alpha__agent_ari",
            query="copper radio",
            current_tick=5,
            simulation_id="alpha",
        )
        foreign = restarted.recall(
            "sim_alpha__agent_ari",
            query="greenhouse basil",
            current_tick=5,
            simulation_id="beta",
        )

        assert [memory.memory_id for memory in alpha] == ["mem_alpha"]
        assert foreign == []
        assert store._collection_name("alpha") != store._collection_name("beta")
    finally:
        engine.dispose()


def test_chroma_collection_is_owned_and_deleted_by_simulation(tmp_path):
    engine, factory = _memory_world()
    store = EpisodicStore(
        factory, tmp_path / "chroma", vector_backend="chroma"
    )

    class FakeClient:
        def __init__(self):
            self.deleted = []

        def delete_collection(self, name):
            self.deleted.append(name)

    fake_client = FakeClient()
    store._client = fake_client
    try:
        name = store._collection_name("alpha")
        store.delete_simulation("alpha")

        assert fake_client.deleted == [name]
        assert name != store._collection_name("beta")
    finally:
        engine.dispose()


def test_episodic_store_rejects_memory_id_owned_by_another_simulation():
    engine, factory = _memory_world()
    store = EpisodicStore(factory)
    try:
        store.add_memory(
            EpisodicMemory(
                memory_id="shared_id",
                simulation_id="alpha",
                agent_id="sim_alpha__agent_ari",
                tick=1,
                content="Ari remembers the radio.",
            )
        )

        with pytest.raises(ValueError, match="another agent or simulation"):
            store.add_memory(
                EpisodicMemory(
                    memory_id="shared_id",
                    simulation_id="beta",
                    agent_id="sim_beta__agent_ben",
                    tick=1,
                    content="Ben remembers the greenhouse.",
                )
            )
    finally:
        engine.dispose()


def test_episodic_store_rejects_unknown_vector_backend():
    with pytest.raises(ValueError, match="Unsupported memory vector backend"):
        EpisodicStore(vector_backend="remote")


def test_event_memories_follow_outer_transaction_and_are_idempotent():
    engine, factory = _memory_world()
    store = EpisodicStore(factory)
    event = {
        "event_id": "evt_1",
        "tick": 3,
        "kami_id": "sim_alpha__kami_room",
        "event_type": "conversation",
        "narrative": "Ari heard a difficult admission.",
        "salience": 0.8,
        "participants": ["sim_alpha__agent_ari", "sim_beta__agent_ben"],
        "payload": {},
    }
    try:
        with factory() as session:
            store.stage_event_memories(session, "alpha", [event])
            session.rollback()
        with factory() as session:
            assert session.query(EpisodicMemoryRecord).count() == 0
            store.stage_event_memories(session, "alpha", [event])
            session.commit()
            store.stage_event_memories(session, "alpha", [event])
            session.commit()
            rows = session.query(EpisodicMemoryRecord).all()

        assert len(rows) == 1
        assert rows[0].agent_id == "sim_alpha__agent_ari"
        assert rows[0].simulation_id == "alpha"
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_consolidator_persists_summary_and_insight(monkeypatch):
    engine, factory = _memory_world()
    consolidator = MemoryConsolidator(factory)

    async def fake_summary(*args, **kwargs):
        return {
            "summary": "Ari learned to ask for help.",
            "candidate_insights": ["Asking for help can strengthen trust."],
        }

    monkeypatch.setattr(consolidator, "_summarize_day", fake_summary)
    try:
        await consolidator.consolidate_day(
            "sim_alpha__agent_ari",
            [{"tick": 2, "content": "Ari accepted help."}],
            {"name": "Ari"},
            {},
            10,
        )

        restarted = MemoryConsolidator(factory)
        state = restarted.get_state("sim_alpha__agent_ari")
        assert state.daily_summaries[0]["summary"] == "Ari learned to ask for help."
        assert state.insights[0].content == "Asking for help can strengthen trust."
        assert state.last_consolidation_tick == 10
    finally:
        engine.dispose()


def test_prompt_contains_scoped_episodic_memory_and_insight():
    engine, factory = init_db("sqlite:///:memory:")
    memory_runtime.configure(factory)
    try:
        with factory() as session:
            kami = fs.create_entity(
                session,
                "kami",
                "Room",
                0,
                entity_id="sim_alpha__kami_room",
                simulation_id="alpha",
            )
            agent = fs.create_entity(
                session,
                "agent",
                "Ari",
                0,
                entity_id="sim_alpha__agent_ari",
                simulation_id="alpha",
            )
            fs.place_entity(session, agent.entity_id, kami.entity_id, 0)
            session.commit()
            memory_runtime.episodic.add_memory(
                EpisodicMemory(
                    memory_id="mem_radio",
                    simulation_id="alpha",
                    agent_id=agent.entity_id,
                    tick=2,
                    content="I repaired the copper radio.",
                )
            )
            memory_runtime.consolidator.add_insight(
                agent.entity_id,
                "Careful repairs prevent larger failures.",
                2,
            )
            state = memory_runtime.consolidator.get_state(agent.entity_id)
            state.life_narrative = "I solve practical problems without seeking praise."
            state.daily_summaries.append(
                {
                    "tick": 2,
                    "summary": "Ari restored an old radio.",
                    "candidates": [],
                }
            )
            memory_runtime.consolidator._save_state(agent.entity_id, state)
            kami_state = fs.query_kami_state(session, kami.entity_id)
            _, messages = build_agent_prompt(
                session,
                agent,
                kami.entity_id,
                kami_state,
                tick=3,
                recent_personal_events=[{"narrative": "The radio crackled."}],
            )

        prompt = messages[0]["content"]
        assert "I repaired the copper radio." in prompt
        assert "I solve practical problems without seeking praise." in prompt
        assert "Ari restored an old radio." in prompt
        assert "Careful repairs prevent larger failures." in prompt
    finally:
        memory_runtime.configure(None)
        engine.dispose()


@pytest.mark.asyncio
async def test_nightly_runtime_consolidates_only_agents_with_scoped_memories(
    tmp_path, monkeypatch
):
    engine, factory = _memory_world()
    runtime = MemoryRuntime()
    runtime.configure(factory, tmp_path / "chroma", vector_backend="sql")
    runtime.episodic.add_memory(
        EpisodicMemory(
            memory_id="mem_alpha",
            simulation_id="alpha",
            agent_id="sim_alpha__agent_ari",
            tick=0,
            content="Ari completed a difficult repair.",
        )
    )
    calls = []

    async def fake_consolidate(**kwargs):
        calls.append(kwargs)
        return {"summary": {}, "goal_deltas": {}, "active_insights": 0}

    monkeypatch.setattr(config, "tick_in_sim_minutes", 1440)
    monkeypatch.setattr(runtime.consolidator, "consolidate_day", fake_consolidate)
    try:
        results = await runtime.consolidate_if_due("alpha", tick=0)

        assert [result["agent_id"] for result in results] == [
            "sim_alpha__agent_ari"
        ]
        assert calls[0]["day_memories"][0]["content"] == (
            "Ari completed a difficult repair."
        )
    finally:
        runtime.configure(None)
        engine.dispose()


@pytest.mark.asyncio
async def test_reflection_pipeline_persists_goals_emotion_narrative_and_provenance(
    monkeypatch,
):
    engine, factory = _memory_world()
    consolidator = MemoryConsolidator(factory)
    with factory() as session:
        agent = session.get(fs.Entity, "sim_alpha__agent_ari")
        agent.archetype = {
            "goals": {"current": "Repair the radio."},
            "emotion": {"dominant": "anxious", "intensity": 0.8},
        }
        session.commit()
    existing = consolidator.add_insight(
        "sim_alpha__agent_ari", "Working alone is always safer.", 0
    )

    async def fake_summary(*args, **kwargs):
        return {
            "summary": "Ari accepted careful help during the repair.",
            "candidate_insights": ["Trust can make difficult repairs safer."],
        }

    async def fake_call(*args, **kwargs):
        if kwargs.get("tools"):
            return {
                "content": "",
                "tool_calls": [
                    {
                        "name": "integrate_insights",
                        "input": {
                            "operations": [
                                {
                                    "action": "modify",
                                    "insight_id": existing.insight_id,
                                    "content": "Trusted help can make difficult work safer.",
                                    "source_candidate": "Trust can make difficult repairs safer.",
                                }
                            ]
                        },
                    }
                ],
            }
        system = str(kwargs.get("system") or "")
        if "goal hierarchy" in system:
            return {
                "content": '{"updates":{"current":"Finish the repair with Ben."}}',
                "tool_calls": [],
            }
        return {
            "content": (
                '{"life_narrative":"I repair what matters and now accept trusted help.",'
                f'"insight_operations":[{{"action":"strengthen",'
                f'"insight_id":"{existing.insight_id}","amount":0.2}}]}}'
            ),
            "tool_calls": [],
        }

    monkeypatch.setattr(consolidator, "_summarize_day", fake_summary)
    monkeypatch.setattr(consolidator, "_llm_available", lambda tier="strong": True)
    monkeypatch.setattr("kami_sim.memory.consolidator.llm_client.call", fake_call)
    monkeypatch.setattr(config, "tick_in_sim_minutes", 1440)
    monkeypatch.setattr(config, "consolidation_phase_5_interval_days", 1)
    try:
        result = await consolidator.consolidate_day(
            "sim_alpha__agent_ari",
            [
                {
                    "tick": 0,
                    "content": "Ben steadied the radio while Ari repaired it.",
                    "importance": 0.9,
                }
            ],
            {
                "name": "Ari",
                "background": "A careful repairer.",
                "emotion": {"dominant": "anxious", "intensity": 0.8},
            },
            {"current": "Repair the radio."},
            0,
        )

        state = consolidator.get_state("sim_alpha__agent_ari")
        with factory() as session:
            agent = session.get(fs.Entity, "sim_alpha__agent_ari")
            archetype = dict(agent.archetype or {})

        assert result["life_narrative_updated"] is True
        assert state.life_narrative.startswith("I repair what matters")
        assert state.last_narrative_tick == 0
        assert state.insights[0].content == (
            "Trusted help can make difficult work safer."
        )
        assert [item["action"] for item in state.insights[0].provenance][-2:] == [
            "modify",
            "strengthen",
        ]
        assert archetype["goals"]["current"] == "Finish the repair with Ben."
        assert archetype["emotion"]["intensity"] == 0.72
        assert "Ben steadied the radio" in archetype["emotion"]["last_trigger"]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_stale_weak_insight_decays_into_archive(monkeypatch):
    engine, factory = _memory_world()
    consolidator = MemoryConsolidator(factory)
    insight = consolidator.add_insight(
        "sim_alpha__agent_ari", "This belief has gone stale.", 0
    )
    state = consolidator.get_state("sim_alpha__agent_ari")
    state.insights[0].strength = 0.25
    consolidator._save_state("sim_alpha__agent_ari", state)

    async def fake_summary(*args, **kwargs):
        return {"summary": "A quiet day.", "candidate_insights": []}

    monkeypatch.setattr(consolidator, "_summarize_day", fake_summary)
    monkeypatch.setattr(consolidator, "_llm_available", lambda tier="strong": False)
    monkeypatch.setattr(config, "tick_in_sim_minutes", 1440)
    monkeypatch.setattr(config, "insight_decay_days_without_reinforcement", 1)
    monkeypatch.setattr(config, "consolidation_phase_5_interval_days", 7)
    try:
        await consolidator.consolidate_day(
            "sim_alpha__agent_ari",
            [{"tick": 1, "content": "Nothing reinforced the belief."}],
            {"name": "Ari", "emotion": {}},
            {},
            1,
        )

        restarted = consolidator.get_state("sim_alpha__agent_ari")
        with factory() as session:
            row = session.get(SemanticInsight, insight.insight_id)
        assert restarted.insights == []
        assert restarted.archived_insights[0].insight_id == insight.insight_id
        assert row.status == "archived"
        assert row.provenance[-1]["action"] == "decay"
    finally:
        engine.dispose()
