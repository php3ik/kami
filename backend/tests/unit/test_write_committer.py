from kami_sim.eventbus.bus import EventBus
from kami_sim.factstore import tools as fs
from kami_sim.factstore.models import Event, init_db
from kami_sim.scheduler.write_committer import commit_proposals
from kami_sim.spatial.graph import SpatialGraph


def _world():
    engine, factory = init_db("sqlite:///:memory:")
    session = factory()
    fs.create_entity(session, "kami", "Room", 0, entity_id="kami_room")
    fs.create_entity(session, "agent", "Ari", 0, entity_id="agent_ari")
    fs.place_entity(session, "agent_ari", "kami_room", 0)
    session.commit()
    graph = SpatialGraph()
    graph.add_kami("kami_room", name="Room", kind="room")
    return engine, session, graph


def test_rejects_scene_event_when_mutation_fails():
    engine, session, graph = _world()
    try:
        events, failures = commit_proposals(
            session,
            1,
            [{
                "kami_id": "kami_room",
                "mutations": [{"type": "unknown_mutation"}],
                "events": [{
                    "event_type": "action",
                    "narrative": "This must not become canon.",
                }],
            }],
            EventBus(),
            graph,
        )

        assert events == []
        assert failures[0]["mutation"]["type"] == "unknown_mutation"
        assert session.query(Event).count() == 0
    finally:
        session.close()
        engine.dispose()


def test_commits_events_in_kami_order_and_preserves_causes():
    engine, session, graph = _world()
    fs.create_entity(session, "kami", "Atrium", 0, entity_id="kami_atrium")
    graph.add_kami("kami_atrium", name="Atrium", kind="room")
    session.commit()
    try:
        events, failures = commit_proposals(
            session,
            1,
            [
                {
                    "kami_id": "kami_room",
                    "events": [{"event_type": "idle", "narrative": "Room waits."}],
                },
                {
                    "kami_id": "kami_atrium",
                    "events": [{
                        "event_type": "arrival",
                        "narrative": "A door opens.",
                        "causes": ["evt_prior"],
                    }],
                },
            ],
            EventBus(),
            graph,
        )

        assert failures == []
        assert [event["kami_id"] for event in events] == ["kami_atrium", "kami_room"]
        assert events[0]["causes"] == ["evt_prior"]
    finally:
        session.close()
        engine.dispose()
