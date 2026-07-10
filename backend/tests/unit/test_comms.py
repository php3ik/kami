import pytest

from kami_sim.agent_worker.prompt_builder import build_agent_prompt
from kami_sim.comms.channels import (
    create_channel,
    expire_ringing_calls,
    get_forced_wake_agents,
    get_unread_messages,
    make_call,
    send_message,
    update_call_state,
)
from kami_sim.comms.inbox import get_feed_digest, get_inbox_digest, process_read
from kami_sim.eventbus.bus import EventBus
from kami_sim.factstore import tools as fs
from kami_sim.factstore.models import Message, MessageDelivery, ReadReceipt, init_db
from kami_sim.scheduler.write_committer import commit_proposals
from kami_sim.spatial.graph import SpatialGraph


def _world():
    engine, factory = init_db("sqlite:///:memory:")
    session = factory()
    for kami_id, name in (("kami_a", "A"), ("kami_b", "B")):
        fs.create_entity(session, "kami", name, 0, entity_id=kami_id)
    for agent_id, name, kami_id in (
        ("agent_ari", "Ari", "kami_a"),
        ("agent_ben", "Ben", "kami_b"),
        ("agent_cia", "Cia", "kami_b"),
    ):
        fs.create_entity(session, "agent", name, 0, entity_id=agent_id)
        fs.place_entity(session, agent_id, kami_id, 0)
    session.commit()
    graph = SpatialGraph()
    graph.add_kami("kami_a", name="A", kind="room")
    graph.add_kami("kami_b", name="B", kind="room")
    return engine, session, graph


def test_delivery_is_delayed_and_receipt_requires_processing():
    engine, session, _ = _world()
    try:
        channel = create_channel(session, "sms", ["agent_ari", "agent_ben"], 0)
        message = send_message(
            session, channel.channel_id, "agent_ari", "Meet me outside", 4
        )

        assert get_inbox_digest(session, "agent_ben", current_tick=4) == []
        digest = get_inbox_digest(session, "agent_ben", current_tick=5)
        assert digest[0]["content"] == "Meet me outside"
        assert session.query(ReadReceipt).count() == 0

        process_read(session, "agent_ben", [message.message_id, message.message_id], 5)
        assert session.query(ReadReceipt).count() == 1
        assert session.query(MessageDelivery).one().status == "read"
        assert get_unread_messages(session, "agent_ben", current_tick=5) == []
    finally:
        session.close()
        engine.dispose()


def test_high_salience_message_creates_next_tick_force_wake():
    engine, session, _ = _world()
    try:
        channel = create_channel(session, "sms", ["agent_ari", "agent_ben"], 0)
        send_message(
            session,
            channel.channel_id,
            "agent_ari",
            "Emergency",
            2,
            salience=0.9,
            active_kami_ids=set(),
        )

        assert get_forced_wake_agents(session, "default", 2) == set()
        assert get_forced_wake_agents(session, "default", 3) == {"agent_ben"}
    finally:
        session.close()
        engine.dispose()


def test_group_chat_limits_active_senders_and_message_bursts():
    engine, session, _ = _world()
    try:
        channel = create_channel(
            session,
            "group_chat",
            ["agent_ari", "agent_ben", "agent_cia"],
            0,
            medium_properties={
                "max_active_participants_per_tick": 1,
                "conversation_cooldown_threshold": 2,
                "conversation_cooldown": 5,
            },
        )
        send_message(session, channel.channel_id, "agent_ari", "one", 1)
        with pytest.raises(ValueError, match="active-participant"):
            send_message(session, channel.channel_id, "agent_ben", "two", 1)
        send_message(session, channel.channel_id, "agent_ari", "two", 1)
        with pytest.raises(ValueError, match="cooldown"):
            send_message(session, channel.channel_id, "agent_ari", "three", 2)
        assert session.query(Message).count() == 2
    finally:
        session.close()
        engine.dispose()


def test_public_posts_are_pull_only_and_never_enter_direct_inbox():
    engine, session, _ = _world()
    try:
        channel = create_channel(
            session,
            "public_post",
            ["agent_ari"],
            0,
            subscribers=["agent_ari", "agent_ben"],
        )
        message = send_message(
            session, channel.channel_id, "agent_ari", "Public status", 2
        )

        assert get_unread_messages(session, "agent_ben", current_tick=3) == []
        assert session.query(MessageDelivery).count() == 0
        feed = get_feed_digest(session, "agent_ben", 3)
        assert [item["message_id"] for item in feed] == [message.message_id]
    finally:
        session.close()
        engine.dispose()


def test_phone_call_lifecycle_wakes_and_expires():
    engine, session, _ = _world()
    try:
        channel = create_channel(
            session, "phone_dm", ["agent_ari", "agent_ben"], 0
        )
        _, message = make_call(
            session,
            "agent_ari",
            "agent_ben",
            3,
            channel_id=channel.channel_id,
        )
        assert message.kind == "call"
        assert channel.metadata_["call_state"] == "ringing"
        assert get_forced_wake_agents(session, "default", 4) == {"agent_ben"}

        update_call_state(session, channel.channel_id, "agent_ben", "active", 4)
        assert get_forced_wake_agents(session, "default", 4) == {
            "agent_ari",
            "agent_ben",
        }
        update_call_state(session, channel.channel_id, "agent_ari", "ended", 5)
        assert channel.metadata_["call_state"] == "ended"

        second = create_channel(
            session, "phone_dm", ["agent_ari", "agent_cia"], 5
        )
        make_call(
            session,
            "agent_ari",
            "agent_cia",
            6,
            channel_id=second.channel_id,
        )
        assert expire_ringing_calls(session, "default", 9) == 1
        assert second.metadata_["call_state"] == "missed"
    finally:
        session.close()
        engine.dispose()


def test_write_committer_emits_message_atomically():
    engine, session, graph = _world()
    try:
        channel = create_channel(session, "sms", ["agent_ari", "agent_ben"], 0)
        session.commit()
        events, failures = commit_proposals(
            session,
            7,
            [{
                "kami_id": "kami_a",
                "mutations": [{
                    "type": "emit_message",
                    "channel_id": channel.channel_id,
                    "sender_id": "agent_ari",
                    "content": "Committed once",
                }],
                "events": [{
                    "event_type": "message_sent",
                    "participants": ["agent_ari"],
                    "narrative": "Ari sends a message.",
                }],
            }],
            EventBus(),
            graph,
        )
        assert failures == []
        assert len(events) == 1
        assert session.query(Message).one().content == "Committed once"
        assert session.query(MessageDelivery).one().available_at_tick == 8
    finally:
        session.close()
        engine.dispose()


def test_agent_prompt_contains_only_available_messages_and_channel_ids():
    engine, session, _ = _world()
    try:
        channel = create_channel(session, "sms", ["agent_ari", "agent_ben"], 0)
        send_message(session, channel.channel_id, "agent_ari", "Private update", 1)
        agent = session.get(fs.Entity, "agent_ben")
        digest = get_inbox_digest(session, "agent_ben", current_tick=2)
        _, messages = build_agent_prompt(
            session,
            agent,
            "kami_b",
            fs.query_kami_state(session, "kami_b"),
            2,
            pending_communications=digest,
        )
        prompt = messages[0]["content"]
        assert "Private update" in prompt
        assert channel.channel_id in prompt
        assert "agent_ari" in prompt
    finally:
        session.close()
        engine.dispose()
