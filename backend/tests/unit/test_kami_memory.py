import pytest

from kami_sim.config import config
from kami_sim.eventbus.bus import EventBus
from kami_sim.factstore import tools as fs
from kami_sim.factstore.models import (
    KamiImprint,
    KamiMemoryProfile,
    KamiMemorySummary,
    init_db,
)
from kami_sim.kami_worker.prompt_builder import build_kami_prompt
from kami_sim.memory import memory_runtime
from kami_sim.memory.kami_memory import KamiMemoryStore, imprint_on_kami
from kami_sim.spatial.graph import SpatialGraph


def _kami_world():
    engine, factory = init_db("sqlite:///:memory:")
    with factory() as session:
        fs.create_entity(
            session,
            "kami",
            "Workshop",
            0,
            entity_id="sim_alpha__kami_workshop",
            simulation_id="alpha",
            archetype={"description": "A compact radio workshop."},
        )
        fs.create_entity(
            session,
            "kami",
            "Greenhouse",
            0,
            entity_id="sim_beta__kami_greenhouse",
            simulation_id="beta",
        )
        session.commit()
    return engine, factory


def test_high_salience_imprints_follow_transaction_and_scope():
    engine, factory = _kami_world()
    store = KamiMemoryStore(factory)
    event = {
        "event_id": "evt_radio_fire",
        "tick": 4,
        "kami_id": "sim_alpha__kami_workshop",
        "event_type": "accident",
        "narrative": "A radio battery fire scorched the north workbench.",
        "salience": 0.96,
        "payload": {},
    }
    foreign = {
        **event,
        "event_id": "evt_foreign",
        "kami_id": "sim_beta__kami_greenhouse",
    }
    try:
        with factory() as session:
            store.stage_event_imprints(session, "alpha", [event])
            session.rollback()
        with factory() as session:
            assert session.query(KamiImprint).count() == 0
            store.stage_event_imprints(session, "alpha", [event, foreign])
            session.commit()
            store.stage_event_imprints(session, "alpha", [event])
            imprint_on_kami(
                session,
                "sim_alpha__kami_workshop",
                event["narrative"],
                5,
                source_event_id="different_source_same_fact",
            )
            session.commit()
            rows = session.query(KamiImprint).all()

        assert len(rows) == 1
        assert rows[0].simulation_id == "alpha"
        assert rows[0].kami_id == "sim_alpha__kami_workshop"
        assert "scorched" in rows[0].fact
        assert "scorched" in store.get_prompt_context(
            "sim_alpha__kami_workshop", "alpha"
        )
    finally:
        engine.dispose()


def test_manual_imprint_rejects_cross_simulation():
    engine, factory = _kami_world()
    try:
        with factory() as session:
            with pytest.raises(ValueError, match="another simulation"):
                imprint_on_kami(
                    session,
                    "sim_alpha__kami_workshop",
                    "A permanent mark.",
                    2,
                    simulation_id="beta",
                )
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_kami_consolidation_persists_and_is_idempotent(monkeypatch):
    engine, factory = _kami_world()
    store = KamiMemoryStore(factory)
    calls = []
    with factory() as session:
        fs.emit_event(
            session,
            tick=0,
            kami_id="sim_alpha__kami_workshop",
            event_type="repair",
            participants=[],
            salience=0.7,
            narrative="The damaged radio transmitted clearly after a careful repair.",
            event_id="evt_repair",
        )
        imprint_on_kami(
            session,
            "sim_alpha__kami_workshop",
            "The north workbench bears a black scorch mark.",
            0,
            source_event_id="evt_scorch",
        )
        session.commit()

    async def fake_summary(*args, **kwargs):
        calls.append((args, kwargs))
        return "A careful repair restored radio contact, while the scorched bench remained visible."

    monkeypatch.setattr(store, "_summarize_day", fake_summary)
    monkeypatch.setattr(config, "tick_in_sim_minutes", 1440)
    try:
        first = await store.consolidate_if_due("alpha", 0)
        second = await store.consolidate_if_due("alpha", 0)
        restarted = KamiMemoryStore(factory)
        context = restarted.get_prompt_context(
            "sim_alpha__kami_workshop", "alpha"
        )
        with factory() as session:
            assert session.query(KamiMemorySummary).count() == 1
            profile = session.get(
                KamiMemoryProfile, "sim_alpha__kami_workshop"
            )

        assert len(calls) == 1
        assert first[0]["idempotent"] is False
        assert second[0]["idempotent"] is True
        assert profile.last_consolidation_tick == 0
        assert "restored radio contact" in context
        assert "black scorch mark" in context
    finally:
        engine.dispose()


def test_kami_prompt_contains_durable_long_term_memory():
    engine, factory = _kami_world()
    memory_runtime.configure(factory)
    try:
        with factory() as session:
            kami = session.get(fs.Entity, "sim_alpha__kami_workshop")
            profile = KamiMemoryProfile(
                kami_id=kami.entity_id,
                simulation_id="alpha",
                long_term_memory="The north workbench is permanently scorched.",
                last_consolidation_tick=5,
            )
            session.add(profile)
            session.commit()
            graph = SpatialGraph()
            graph.add_kami(kami.entity_id, name="Workshop", kind="workshop")
            _, messages = build_kami_prompt(
                session,
                kami.entity_id,
                kami,
                tick=6,
                agent_intents=[],
                event_bus=EventBus(),
                spatial_graph=graph,
            )

        assert "The north workbench is permanently scorched." in messages[0][
            "content"
        ]
    finally:
        memory_runtime.configure(None)
        engine.dispose()
