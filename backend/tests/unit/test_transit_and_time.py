import pytest

from kami_sim.factstore import tools as fs
from kami_sim.factstore.models import (
    Schedule,
    Simulation,
    SimulationTick,
    TransitJourney,
    init_db,
)
from kami_sim.scheduler import tick_scheduler as scheduler_module
from kami_sim.scheduler.tick_scheduler import TickScheduler
from kami_sim.scheduler.write_committer import commit_proposals
from kami_sim.spatial.graph import SpatialGraph
from kami_sim.spatial.transit import (
    advance_transit,
    begin_transit,
    transit_entity_id,
)


def _world(simulation_id: str = "sim-a"):
    engine, factory = init_db("sqlite:///:memory:")
    session = factory()
    session.add(
        Simulation(
            id=simulation_id,
            name="Transit test",
            is_active=True,
            graph_data={},
        )
    )
    room_a = f"sim_{simulation_id}__kami_a"
    room_b = f"sim_{simulation_id}__kami_b"
    room_c = f"sim_{simulation_id}__kami_c"
    agent = f"sim_{simulation_id}__agent_ari"
    for room_id, name in ((room_a, "A"), (room_b, "B"), (room_c, "C")):
        fs.create_entity(
            session,
            "kami",
            name,
            0,
            entity_id=room_id,
            simulation_id=simulation_id,
        )
    fs.create_entity(
        session,
        "agent",
        "Ari",
        0,
        entity_id=agent,
        simulation_id=simulation_id,
    )
    fs.place_entity(session, agent, room_a, 0)
    session.commit()
    graph = SpatialGraph()
    for room_id in (room_a, room_b, room_c):
        graph.add_kami(room_id, name=room_id, kind="room")
    graph.add_edge(room_a, room_b)
    graph.add_edge(room_b, room_c)
    return engine, factory, session, graph, room_a, room_b, room_c, agent


def test_transit_uses_source_n_placeholder_n_plus_1_and_destination_n_plus_2():
    engine, _, session, graph, room_a, room_b, _, agent = _world()
    try:
        journey = begin_transit(session, agent, room_b, 4, graph)
        assert fs.get_current_location(session, agent).kami_id == room_a
        assert journey.depart_at_tick == 5
        assert journey.arrive_at_tick == 6

        events, transitions = advance_transit(session, "sim-a", 5)
        assert fs.get_current_location(session, agent).kami_id == transit_entity_id("sim-a")
        assert journey.status == "in_transit"
        assert events[0]["event_type"] == "transit_departure"
        assert transitions[0]["transition"] == "departed"

        events, transitions = advance_transit(session, "sim-a", 6)
        assert fs.get_current_location(session, agent).kami_id == room_b
        assert journey.status == "arrived"
        assert events[0]["event_type"] == "transit_arrival"
        assert transitions[0]["transition"] == "arrived"
    finally:
        session.close()
        engine.dispose()


def test_transit_rejects_non_adjacent_and_duplicate_journeys():
    engine, _, session, graph, _, room_b, room_c, agent = _world()
    try:
        with pytest.raises(ValueError, match="not adjacent"):
            begin_transit(session, agent, room_c, 1, graph)
        begin_transit(session, agent, room_b, 1, graph)
        with pytest.raises(ValueError, match="active journey"):
            begin_transit(session, agent, room_b, 2, graph)
    finally:
        session.close()
        engine.dispose()


def test_write_committer_schedules_agent_move_without_teleporting():
    engine, _, session, graph, room_a, room_b, _, agent = _world()
    try:
        events, failures = commit_proposals(
            session,
            3,
            [{
                "kami_id": room_a,
                "mutations": [{
                    "type": "move_entity",
                    "entity_id": agent,
                    "to_kami_id": room_b,
                    "reason": "test",
                }],
                "events": [{
                    "event_type": "departure_planned",
                    "participants": [agent],
                    "narrative": "Ari heads for the door.",
                }],
            }],
            scheduler_module.EventBus(),
            graph,
        )
        journey = session.query(TransitJourney).one()
        assert failures == []
        assert len(events) == 1
        assert journey.status == "scheduled"
        assert fs.get_current_location(session, agent).kami_id == room_a
    finally:
        session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_sparse_mode_jumps_directly_to_next_schedule(monkeypatch):
    engine, factory, session, graph, room_a, _, _, agent = _world()
    fs.change_state(session, agent, "sleeping", True, 0)
    session.add(
        Schedule(
            schedule_id="schedule_wake",
            simulation_id="sim-a",
            fires_at_tick=40,
            kami_id=room_a,
            event_template={"event_type": "alarm", "narrative": "An alarm sounds."},
        )
    )
    session.commit()
    session.close()

    class FakeKamiWorker:
        def __init__(self, *args, **kwargs):
            pass

        async def render_tick(self, kami_id, tick, intents):
            return {
                "narrative": "An alarm sounds.",
                "mutations": [],
                "events": [{
                    "event_type": "alarm",
                    "participants": [],
                    "narrative": "An alarm sounds.",
                    "salience": 0.7,
                }],
                "broadcasts": [],
            }

    monkeypatch.setattr(scheduler_module, "KamiWorker", FakeKamiWorker)
    scheduler = TickScheduler(factory, graph, simulation_id="sim-a")
    try:
        results = await scheduler.run(1)
        result = results[0]
        with factory() as verify:
            ledger = verify.query(SimulationTick).all()
        assert result["tick"] == 40
        assert result["time_mode"] == "sparse"
        assert result["jumped_from_tick"] == 0
        assert result["skipped_ticks"] == 40
        assert result["next_tick"] == 41
        assert scheduler.current_tick == 41
        assert [row.tick for row in ledger] == [40]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_advances_transit_phases_without_rendering_placeholder(monkeypatch):
    engine, factory, session, graph, _, room_b, _, agent = _world()
    begin_transit(session, agent, room_b, 0, graph)
    session.commit()
    session.close()

    class FakeAgentWorker:
        def __init__(self, *args, **kwargs):
            pass

        async def think(self, agent_id, kami_id, tick, recent_personal_events=None):
            return {
                "agent_id": agent_id,
                "intents": [{
                    "agent_id": agent_id,
                    "agent_name": "Ari",
                    "action_type": "wait",
                    "target": "",
                    "params": {},
                    "salience": 0.1,
                }],
                "beliefs": [],
                "processed_message_ids": [],
                "inner_monologue": "I arrive and pause.",
            }

    class FakeKamiWorker:
        def __init__(self, *args, **kwargs):
            pass

        async def render_tick(self, kami_id, tick, intents):
            return {
                "narrative": "Ari arrives.",
                "mutations": [],
                "events": [{
                    "event_type": "idle",
                    "participants": [agent],
                    "narrative": "Ari arrives and pauses.",
                    "salience": 0.2,
                }],
                "broadcasts": [],
            }

    monkeypatch.setattr(scheduler_module, "AgentCognitionWorker", FakeAgentWorker)
    monkeypatch.setattr(scheduler_module, "KamiWorker", FakeKamiWorker)
    scheduler = TickScheduler(factory, graph, simulation_id="sim-a")
    scheduler.current_tick = 1
    try:
        departure = (await scheduler.run(1))[0]
        with factory() as verify:
            assert fs.get_current_location(verify, agent).kami_id == transit_entity_id("sim-a")
        arrival = (await scheduler.run(1))[0]
        with factory() as verify:
            assert fs.get_current_location(verify, agent).kami_id == room_b
        assert departure["events"][0]["event_type"] == "transit_departure"
        assert departure["active_agent_count"] == 0
        assert arrival["events"][0]["event_type"] == "transit_arrival"
        assert scheduler.current_tick == 3
    finally:
        engine.dispose()
