import pytest

from kami_sim.comms.channels import (
    create_channel,
    get_unread_messages,
    read_message,
    send_message,
)
from kami_sim.eventbus.bus import EventBus
from kami_sim.factstore import tools as fs
from kami_sim.factstore.models import init_db
from kami_sim.kami_worker.worker import KamiWorker
from kami_sim.spatial.graph import SpatialGraph


def _worlds():
    engine, factory = init_db("sqlite:///:memory:")
    session = factory()
    for scope in ("alpha", "beta"):
        kami_id = f"sim_{scope}__kami_room"
        agent_id = f"sim_{scope}__agent_ari"
        fs.create_entity(
            session,
            "kami",
            "Room",
            0,
            entity_id=kami_id,
            simulation_id=scope,
        )
        fs.create_entity(
            session,
            "agent",
            "Ari",
            0,
            entity_id=agent_id,
            simulation_id=scope,
        )
        fs.place_entity(session, agent_id, kami_id, 0)
    session.commit()
    return engine, session


def test_factstore_writes_inherit_entity_scope():
    engine, session = _worlds()
    try:
        belief = fs.update_belief(
            session,
            "sim_alpha__agent_ari",
            "fact",
            1,
            believed_value="safe",
        )
        need = fs.set_agent_need(
            session, "sim_alpha__agent_ari", "stress", 0.5, 1
        )
        intent = fs.record_agent_intent(
            session,
            1,
            "sim_alpha__agent_ari",
            "sim_alpha__kami_room",
            "wait",
        )
        event = fs.emit_event(
            session,
            1,
            "sim_alpha__kami_room",
            "idle",
            participants=["sim_alpha__agent_ari"],
        )

        assert {belief.simulation_id, need.simulation_id, intent.simulation_id, event.simulation_id} == {"alpha"}
        assert fs.get_events(session, simulation_id="beta") == []
        assert fs.get_events(session, simulation_id="alpha") == [event]
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    "operation",
    [
        lambda session: fs.move_entity(
            session, "sim_alpha__agent_ari", "sim_beta__kami_room", 1
        ),
        lambda session: fs.update_relation(
            session,
            "sim_alpha__agent_ari",
            "sim_beta__agent_ari",
            "knows",
            1,
        ),
        lambda session: fs.transfer_ownership(
            session, "sim_alpha__agent_ari", "sim_beta__agent_ari", 1
        ),
        lambda session: fs.emit_event(
            session,
            1,
            "sim_alpha__kami_room",
            "invalid",
            participants=["sim_beta__agent_ari"],
        ),
    ],
)
def test_factstore_rejects_cross_simulation_operations(operation):
    engine, session = _worlds()
    try:
        with pytest.raises(ValueError, match="Cross-simulation"):
            operation(session)
    finally:
        session.close()
        engine.dispose()


def test_comms_queries_and_receipts_are_simulation_scoped():
    engine, session = _worlds()
    try:
        channel = create_channel(
            session, "sms", ["sim_alpha__agent_ari"], 1
        )
        message = send_message(
            session,
            channel.channel_id,
            "sim_alpha__agent_ari",
            "Alpha only",
            1,
        )

        assert channel.simulation_id == "alpha"
        assert message.simulation_id == "alpha"
        assert get_unread_messages(session, "sim_beta__agent_ari") == []
        with pytest.raises(ValueError, match="not a message recipient"):
            read_message(session, message.message_id, "sim_beta__agent_ari", 2)
        with pytest.raises(ValueError, match="Cross-simulation"):
            create_channel(
                session,
                "sms",
                ["sim_alpha__agent_ari", "sim_beta__agent_ari"],
                2,
            )
    finally:
        session.close()
        engine.dispose()


def test_kami_participant_normalization_cannot_resolve_other_world_agent():
    engine, session = _worlds()
    try:
        worker = KamiWorker(session, EventBus(), SpatialGraph())

        participants = worker._normalize_participants(
            ["sim_alpha__agent_ari", "sim_beta__agent_ari", "Ari"],
            "sim_alpha__kami_room",
        )

        assert participants == ["sim_alpha__agent_ari"]
    finally:
        session.close()
        engine.dispose()
