import pytest

from kami_sim.factstore import tools as fs
from kami_sim.factstore.models import (
    EpisodicMemoryRecord,
    Event,
    SimulationTick,
    init_db,
)
from kami_sim.memory import memory_runtime
from kami_sim.scheduler import tick_scheduler as scheduler_module
from kami_sim.scheduler.tick_scheduler import TickScheduler
from kami_sim.simulations import SimulationRepository
from kami_sim.spatial.graph import SpatialGraph


class DummySession:
    def rollback(self):
        pass

    def close(self):
        pass


@pytest.mark.asyncio
async def test_run_returns_only_current_batch_and_keeps_full_log(monkeypatch):
    scheduler = TickScheduler(
        session_factory=lambda: DummySession(),
        spatial_graph=SpatialGraph(),
    )

    async def fake_run_tick(session, tick, progress_callback=None):
        return {
            "tick": tick,
            "active_kami_count": 0,
            "active_agent_count": 0,
            "events": [],
            "narratives": {},
        }

    monkeypatch.setattr(scheduler, "_run_tick", fake_run_tick)

    first = await scheduler.run(num_ticks=2)
    second = await scheduler.run(num_ticks=1)

    assert [t["tick"] for t in first] == [0, 1]
    assert [t["tick"] for t in second] == [2]
    assert [t["tick"] for t in scheduler.tick_log] == [0, 1, 2]


@pytest.mark.asyncio
async def test_fatal_tick_does_not_advance_clock(monkeypatch):
    scheduler = TickScheduler(
        session_factory=lambda: DummySession(),
        spatial_graph=SpatialGraph(),
    )

    async def fail_tick(session, tick, progress_callback=None):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(scheduler, "_run_tick", fail_tick)

    results = await scheduler.run(num_ticks=2)

    assert len(results) == 1
    assert results[0]["tick"] == 0
    assert results[0]["error"] == "commit failed"
    assert scheduler.current_tick == 0


@pytest.mark.asyncio
async def test_empty_tick_is_durable_and_replayed_without_reexecution():
    engine, factory = init_db("sqlite:///:memory:")
    repository = SimulationRepository(factory)
    repository.upsert({"id": "sim-a", "name": "A"})
    scheduler = TickScheduler(
        session_factory=factory,
        spatial_graph=SpatialGraph(),
        simulation_id="sim-a",
    )
    try:
        first = await scheduler.run(num_ticks=1)
        scheduler.current_tick = 0
        second = await scheduler.run(num_ticks=1)

        with factory() as session:
            records = session.query(SimulationTick).all()
        assert first[0]["tick"] == 0
        assert second[0]["idempotent_replay"] is True
        assert len(records) == 1
        assert records[0].status == "committed"
        assert records[0].attempt_count == 1
        assert repository.get("sim-a")["current_tick"] == 1
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_post_commit_broadcast_failure_does_not_retry_canonical_tick(monkeypatch):
    engine, factory = init_db("sqlite:///:memory:")
    with factory() as session:
        fs.create_entity(
            session,
            "kami",
            "Room",
            0,
            entity_id="sim_sim-a__kami_room",
            simulation_id="sim-a",
        )
        fs.create_entity(
            session,
            "agent",
            "Ari",
            0,
            entity_id="sim_sim-a__agent_ari",
            simulation_id="sim-a",
        )
        session.commit()
    graph = SpatialGraph()
    graph.add_kami("sim_sim-a__kami_room", name="Room", kind="room")
    scheduler = TickScheduler(factory, graph, simulation_id="sim-a")
    memory_runtime.configure(factory)

    class FakeKamiWorker:
        def __init__(self, *args, **kwargs):
            pass

        async def render_tick(self, kami_id, tick, intents):
            return {
                "narrative": "The room settles.",
                "mutations": [],
                "events": [{
                    "event_type": "idle",
                    "narrative": "The room settles.",
                    "salience": 0.1,
                    "participants": ["sim_sim-a__agent_ari"],
                }],
                "broadcasts": [{"text": "A sound", "salience": 0.2}],
            }

    monkeypatch.setattr(
        scheduler_module, "detect_active_kami", lambda *args, **kwargs: {"sim_sim-a__kami_room"}
    )
    monkeypatch.setattr(
        scheduler_module, "detect_active_agents", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(scheduler_module, "KamiWorker", FakeKamiWorker)
    monkeypatch.setattr(
        scheduler.event_bus,
        "publish_broadcast",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("listener failed")),
    )
    try:
        result = await scheduler.run(num_ticks=1)

        with factory() as session:
            assert session.query(Event).count() == 1
            assert session.query(EpisodicMemoryRecord).count() == 1
            tick_record = session.query(SimulationTick).one()
        assert "error" not in result[0]
        assert scheduler.current_tick == 1
        assert tick_record.status == "committed"
    finally:
        memory_runtime.configure(None)
        engine.dispose()


@pytest.mark.asyncio
async def test_failed_tick_is_recorded_and_retry_commits_same_ledger_row(monkeypatch):
    engine, factory = init_db("sqlite:///:memory:")
    scheduler = TickScheduler(
        session_factory=factory,
        spatial_graph=SpatialGraph(),
        simulation_id="sim-a",
    )
    original_run_tick = scheduler._run_tick

    async def fail_tick(session, tick, progress_callback=None):
        raise RuntimeError("interrupted")

    try:
        monkeypatch.setattr(scheduler, "_run_tick", fail_tick)
        failed = await scheduler.run(num_ticks=1)
        monkeypatch.setattr(scheduler, "_run_tick", original_run_tick)
        retried = await scheduler.run(num_ticks=1)

        with factory() as session:
            record = session.query(SimulationTick).one()
        assert failed[0]["error"] == "interrupted"
        assert retried[0]["tick"] == 0
        assert scheduler.current_tick == 1
        assert record.status == "committed"
        assert record.attempt_count == 2
        assert record.error_message is None
    finally:
        engine.dispose()
