from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kami_sim.factstore.models import Base
from kami_sim.factstore import tools as fs
from kami_sim.kami_worker.scene_dynamics import (
    analyze_scene_dynamics,
    apply_scene_guardrails,
)
from kami_sim.spatial.graph import SpatialGraph


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_world(session):
    fs.create_entity(session, "kami", "Lane", tick=0, entity_id="kami_lane")
    fs.create_entity(session, "kami", "Shop", tick=0, entity_id="kami_shop")
    fs.create_entity(session, "agent", "Petro", tick=0, entity_id="agent_petro")
    fs.create_entity(session, "agent", "Hanna", tick=0, entity_id="agent_hanna")
    fs.place_entity(session, "agent_petro", "kami_lane", tick=0)
    fs.place_entity(session, "agent_hanna", "kami_lane", tick=0)
    graph = SpatialGraph()
    graph.add_kami("kami_lane")
    graph.add_kami("kami_shop")
    graph.add_edge("kami_lane", "kami_shop")
    return graph


def test_scene_dynamics_blocks_ambiguous_unknown_targets():
    session = make_session()
    graph = make_world(session)
    intents = [{
        "intent_id": "int_1",
        "agent_id": "agent_petro",
        "action_type": "talk",
        "target": "unknown_person_1",
        "utterance": "Who are you?",
    }]

    dynamics = analyze_scene_dynamics(
        session, "kami_lane", 2, intents, [], [], graph
    )
    result = apply_scene_guardrails(
        {
            "events": [],
            "mutations": [{
                "type": "update_relation",
                "from_entity": "agent_petro",
                "to_entity": "unknown_person_1",
                "rel_type": "trusts",
            }],
        },
        dynamics,
        "kami_lane",
        2,
        intents,
    )

    assert dynamics.invalid_intents
    assert len(result["mutations"]) == 1
    assert result["mutations"][0]["type"] == "record_intent_result"
    assert result["mutations"][0]["status"] == "blocked"
    assert "target_not_present" in result["mutations"][0]["blockers"]


def test_scene_dynamics_forces_repeated_question_to_pause():
    session = make_session()
    graph = make_world(session)
    for tick in range(3):
        fs.emit_event(
            session,
            tick=tick,
            kami_id="kami_lane",
            event_type="conversation",
            participants=["agent_petro", "agent_hanna"],
            narrative="Petro asks again: who are you and why are you here?",
            salience=0.8,
        )
    thread = fs.upsert_conversation_thread(
        session,
        tick=2,
        kami_id="kami_lane",
        participants=["agent_petro", "agent_hanna"],
        topic="stranger at the lane",
        summary="They keep asking who the person is and why they are here.",
        open_question="Who are you and why are you here?",
        tension=0.5,
    )
    intents = [{
        "intent_id": "int_2",
        "agent_id": "agent_petro",
        "action_type": "talk",
        "target": "agent_hanna",
        "utterance": "Who are you and why are you here?",
    }]

    dynamics = analyze_scene_dynamics(
        session,
        "kami_lane",
        4,
        intents,
        fs.get_events(session, kami_id="kami_lane", since_tick=0),
        [thread],
        graph,
    )
    result = apply_scene_guardrails(
        {
            "events": [{
                "kami_id": "kami_lane",
                "tick": 4,
                "event_type": "conversation",
                "participants": ["agent_petro", "agent_hanna"],
                "narrative": "Petro asks the same question again.",
                "salience": 0.84,
                "payload": {},
            }],
            "mutations": [],
        },
        dynamics,
        "kami_lane",
        4,
        intents,
    )

    assert dynamics.loop_break_required
    assert any(
        m["type"] == "update_conversation_thread" and m["status"] == "paused"
        for m in result["mutations"]
    )
    assert result["events"][0]["payload"]["loop_break_applied"] is True


def test_scene_dynamics_builds_affordance_and_conflict_plan():
    session = make_session()
    graph = make_world(session)
    fs.create_entity(
        session,
        "object",
        "Shared radio",
        tick=0,
        entity_id="obj_radio",
        archetype={"uses": ["send a weather report", "listen for replies"]},
    )
    fs.place_entity(session, "obj_radio", "kami_lane", tick=0)
    intents = [
        {
            "intent_id": "int_petro",
            "agent_id": "agent_petro",
            "action_type": "use_object",
            "target": "obj_radio",
        },
        {
            "intent_id": "int_hanna",
            "agent_id": "agent_hanna",
            "action_type": "work",
            "target": "obj_radio",
        },
    ]

    dynamics = analyze_scene_dynamics(
        session, "kami_lane", 2, intents, [], [], graph
    )
    plan = dynamics.to_resolution_plan()

    assert all(item["status"] == "feasible" for item in plan["intent_assessments"])
    assert plan["intent_assessments"][0]["affordances"] == [
        "send a weather report",
        "listen for replies",
    ]
    assert plan["conflict_groups"] == [{
        "kind": "exclusive_resource",
        "target": "obj_radio",
        "intent_ids": ["int_petro", "int_hanna"],
    }]
