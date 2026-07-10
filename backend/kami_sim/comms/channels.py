"""Durable non-local communication channels and delivery state."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import config
from ..determinism import generate_id
from ..factstore import tools as fs
from ..factstore.models import Channel, Entity, Message, MessageDelivery, ReadReceipt
from .wake_logic import determine_delivery_mode


CHANNEL_KINDS = {
    "phone_dm",
    "sms",
    "messenger_dm",
    "group_chat",
    "email",
    "radio_broadcast",
    "tv_broadcast",
    "public_post",
    "letter_mail",
}
DIRECT_CHANNEL_KINDS = {"phone_dm", "sms", "messenger_dm", "group_chat", "email"}
PUSH_CHANNEL_KINDS = DIRECT_CHANNEL_KINDS | {"letter_mail"}
CALL_STATES = {"idle", "ringing", "active", "declined", "missed", "ended"}


def create_channel(
    session: Session,
    kind: str,
    participants: list[str],
    tick: int,
    medium_properties: dict | None = None,
    metadata: dict | None = None,
    subscribers: list[str] | None = None,
) -> Channel:
    """Create a simulation-scoped channel after validating all members."""
    if kind not in CHANNEL_KINDS:
        raise ValueError(f"Unsupported channel kind: {kind}")
    participants = list(dict.fromkeys(participants))
    subscribers = list(dict.fromkeys(subscribers or participants))
    if not participants:
        raise ValueError("A channel requires at least one participant")
    simulation_id = fs.resolve_simulation_id(
        session, *participants, *subscribers
    )
    for entity_id in {*participants, *subscribers}:
        entity = session.get(Entity, entity_id)
        if entity is None or entity.kind != "agent":
            raise ValueError(f"Channel member {entity_id} is not an agent")
    channel = Channel(
        channel_id=generate_id("chan_", 10),
        simulation_id=simulation_id,
        kind=kind,
        participants=participants,
        subscribers=subscribers,
        medium_properties=medium_properties or {},
        created_at_tick=tick,
        metadata_=metadata or {},
    )
    session.add(channel)
    session.flush()
    return channel


def send_message(
    session: Session,
    channel_id: str,
    sender_id: str,
    content: str,
    tick: int,
    salience: float = 0.5,
    *,
    kind: str = "message",
    metadata: dict | None = None,
    active_kami_ids: set[str] | None = None,
) -> Message:
    """Insert a message and one-tick-delayed per-recipient deliveries."""
    channel = _require_channel(session, channel_id)
    if sender_id not in (channel.participants or []):
        raise ValueError(f"Sender {sender_id} not in channel participants")
    if session.get(Entity, sender_id) is None:
        raise ValueError(f"Sender {sender_id} not found")
    fs.resolve_simulation_id(session, sender_id, explicit=channel.simulation_id)
    content = content.strip()
    if not content:
        raise ValueError("Message content cannot be empty")
    if len(content) > 4000:
        raise ValueError("Message content exceeds 4000 characters")
    salience = max(0.0, min(1.0, float(salience)))
    _enforce_group_rate_limit(session, channel, sender_id, tick)

    message = Message(
        message_id=generate_id("msg_", 10),
        simulation_id=channel.simulation_id,
        channel_id=channel_id,
        sender_id=sender_id,
        content=content,
        sent_at_tick=tick,
        salience=salience,
        kind=kind,
        metadata_=metadata or {},
    )
    session.add(message)
    session.flush()

    if channel.kind in PUSH_CHANNEL_KINDS:
        for recipient_id in channel.participants or []:
            if recipient_id == sender_id:
                continue
            location = fs.get_current_location(session, recipient_id)
            is_active = bool(
                location
                and active_kami_ids is not None
                and location.kami_id in active_kami_ids
            )
            using_phone = _agent_uses_phone(session, recipient_id)
            mode = determine_delivery_mode(
                recipient_id,
                kami_is_active=is_active,
                agent_using_phone=using_phone,
                message_salience=salience,
                is_call=kind == "call",
            )
            session.add(
                MessageDelivery(
                    simulation_id=channel.simulation_id,
                    message_id=message.message_id,
                    recipient_id=recipient_id,
                    mode=mode,
                    status="pending",
                    available_at_tick=tick + 1,
                    created_at_tick=tick,
                )
            )
    session.flush()
    return message


def make_call(
    session: Session,
    sender_id: str,
    recipient_id: str,
    tick: int,
    *,
    channel_id: str | None = None,
    salience: float = 0.95,
    active_kami_ids: set[str] | None = None,
) -> tuple[Channel, Message]:
    """Start a ringing phone channel and enqueue a forced-wake delivery."""
    fs.resolve_simulation_id(session, sender_id, recipient_id)
    if sender_id == recipient_id:
        raise ValueError("An agent cannot call themselves")
    channel = _require_channel(session, channel_id) if channel_id else None
    if channel is None:
        channel = _find_direct_channel(session, sender_id, recipient_id, "phone_dm")
    if channel is None:
        if not _has_remote_contact(session, sender_id, recipient_id):
            raise ValueError("Caller has no has_contact_via relation to recipient")
        channel = create_channel(
            session, "phone_dm", [sender_id, recipient_id], tick
        )
    if channel.kind != "phone_dm" or set(channel.participants or []) != {
        sender_id,
        recipient_id,
    }:
        raise ValueError("Calls require a two-person phone_dm channel")
    current_state = (channel.metadata_ or {}).get("call_state", "idle")
    if current_state in {"ringing", "active"}:
        raise ValueError("This channel already has an active call")
    channel.metadata_ = {
        **(channel.metadata_ or {}),
        "call_state": "ringing",
        "caller_id": sender_id,
        "recipient_id": recipient_id,
        "started_at_tick": tick,
        "ring_timeout_tick": tick + 3,
    }
    message = send_message(
        session,
        channel.channel_id,
        sender_id,
        "Incoming call",
        tick,
        salience,
        kind="call",
        metadata={"recipient_id": recipient_id},
        active_kami_ids=active_kami_ids,
    )
    return channel, message


def update_call_state(
    session: Session,
    channel_id: str,
    agent_id: str,
    state: str,
    tick: int,
) -> Channel:
    """Answer, decline, or end a call as one of its participants."""
    channel = _require_channel(session, channel_id)
    if channel.kind != "phone_dm" or agent_id not in (channel.participants or []):
        raise ValueError("Agent is not a participant in this phone call")
    if state not in {"active", "declined", "ended"}:
        raise ValueError(f"Unsupported call state: {state}")
    current = (channel.metadata_ or {}).get("call_state", "idle")
    allowed = {
        "active": {"ringing"},
        "declined": {"ringing"},
        "ended": {"ringing", "active"},
    }
    if current not in allowed[state]:
        raise ValueError(f"Cannot transition call from {current} to {state}")
    if state in {"active", "declined"}:
        recipient_id = (channel.metadata_ or {}).get("recipient_id")
        if recipient_id and agent_id != recipient_id:
            raise ValueError("Only the recipient can answer or decline a call")
    channel.metadata_ = {
        **(channel.metadata_ or {}),
        "call_state": state,
        "updated_at_tick": tick,
    }
    return channel


def expire_ringing_calls(session: Session, simulation_id: str, tick: int) -> int:
    """Mark unanswered calls as missed without waking unrelated channels."""
    count = 0
    channels = session.query(Channel).filter(
        Channel.simulation_id == simulation_id,
        Channel.kind == "phone_dm",
    ).all()
    for channel in channels:
        metadata = channel.metadata_ or {}
        if (
            metadata.get("call_state") == "ringing"
            and int(metadata.get("ring_timeout_tick", tick + 1)) <= tick
        ):
            channel.metadata_ = {
                **metadata,
                "call_state": "missed",
                "updated_at_tick": tick,
            }
            count += 1
    return count


def read_message(
    session: Session,
    message_id: str,
    agent_id: str,
    tick: int,
) -> ReadReceipt:
    """Idempotently record cognition of a message and settle delivery state."""
    message = session.get(Message, message_id)
    if message is None:
        raise ValueError(f"Message {message_id} not found")
    agent = session.get(Entity, agent_id)
    if agent is None:
        raise ValueError(f"Agent {agent_id} not found")
    channel = _require_channel(session, message.channel_id)
    eligible = set(channel.participants or []) | set(channel.subscribers or [])
    if agent_id not in eligible:
        raise ValueError(f"Agent {agent_id} is not a message recipient")
    simulation_id = fs.resolve_simulation_id(
        session, agent_id, explicit=message.simulation_id
    )
    receipt = session.query(ReadReceipt).filter(
        ReadReceipt.simulation_id == simulation_id,
        ReadReceipt.message_id == message_id,
        ReadReceipt.agent_id == agent_id,
    ).one_or_none()
    if receipt is None:
        receipt = ReadReceipt(
            simulation_id=simulation_id,
            message_id=message_id,
            agent_id=agent_id,
            read_at_tick=tick,
        )
        session.add(receipt)
    delivery = session.query(MessageDelivery).filter(
        MessageDelivery.simulation_id == simulation_id,
        MessageDelivery.message_id == message_id,
        MessageDelivery.recipient_id == agent_id,
    ).one_or_none()
    if delivery is not None:
        delivery.status = "read"
    session.flush()
    return receipt


def get_unread_messages(
    session: Session,
    agent_id: str,
    *,
    current_tick: int | None = None,
) -> list[Message]:
    """Return available direct messages not yet processed by cognition."""
    agent = session.get(Entity, agent_id)
    if agent is None:
        raise ValueError(f"Agent {agent_id} not found")
    simulation_id = fs.resolve_simulation_id(session, agent_id)
    channels = [
        channel
        for channel in get_agent_channels(
            session, agent_id, include_subscriptions=False
        )
        if channel.kind in PUSH_CHANNEL_KINDS
    ]
    channel_ids = [channel.channel_id for channel in channels]
    if not channel_ids:
        return []
    read_ids = {
        row.message_id
        for row in session.query(ReadReceipt).filter(
            ReadReceipt.simulation_id == simulation_id,
            ReadReceipt.agent_id == agent_id,
        )
    }
    messages = session.query(Message).filter(
        Message.simulation_id == simulation_id,
        Message.channel_id.in_(channel_ids),
        Message.sender_id != agent_id,
    ).order_by(Message.sent_at_tick, Message.message_id).all()
    result = []
    for message in messages:
        if message.message_id in read_ids:
            continue
        delivery = session.query(MessageDelivery).filter(
            MessageDelivery.simulation_id == simulation_id,
            MessageDelivery.message_id == message.message_id,
            MessageDelivery.recipient_id == agent_id,
        ).one_or_none()
        if delivery is not None:
            if delivery.status != "pending":
                continue
            if current_tick is not None and delivery.available_at_tick > current_tick:
                continue
        result.append(message)
    return result


def get_agent_channels(
    session: Session,
    agent_id: str,
    *,
    include_subscriptions: bool = True,
) -> list[Channel]:
    simulation_id = fs.resolve_simulation_id(session, agent_id)
    channels = session.query(Channel).filter(
        Channel.simulation_id == simulation_id
    ).order_by(Channel.created_at_tick, Channel.channel_id).all()
    return [
        channel
        for channel in channels
        if agent_id in (channel.participants or [])
        or (include_subscriptions and agent_id in (channel.subscribers or []))
    ]


def get_channel_messages(
    session: Session,
    channel_id: str,
    *,
    limit: int = 100,
    until_tick: int | None = None,
) -> list[Message]:
    channel = _require_channel(session, channel_id)
    query = session.query(Message).filter(
        Message.simulation_id == channel.simulation_id,
        Message.channel_id == channel_id,
    )
    if until_tick is not None:
        query = query.filter(Message.sent_at_tick <= until_tick)
    return list(reversed(query.order_by(Message.sent_at_tick.desc()).limit(limit).all()))


def get_forced_wake_agents(
    session: Session, simulation_id: str, tick: int
) -> set[str]:
    """Return recipients due for force wake plus participants in active calls."""
    deliveries = session.query(MessageDelivery).filter(
        MessageDelivery.simulation_id == simulation_id,
        MessageDelivery.status == "pending",
        MessageDelivery.mode == "force_wake",
        MessageDelivery.available_at_tick <= tick,
    ).all()
    agents = {delivery.recipient_id for delivery in deliveries}
    channels = session.query(Channel).filter(
        Channel.simulation_id == simulation_id,
        Channel.kind == "phone_dm",
    ).all()
    for channel in channels:
        if (channel.metadata_ or {}).get("call_state") == "active":
            agents.update(channel.participants or [])
    return agents


def get_next_forced_wake_tick(
    session: Session, simulation_id: str, after_tick: int
) -> int | None:
    row = session.query(MessageDelivery).filter(
        MessageDelivery.simulation_id == simulation_id,
        MessageDelivery.status == "pending",
        MessageDelivery.mode == "force_wake",
        MessageDelivery.available_at_tick > after_tick,
    ).order_by(MessageDelivery.available_at_tick).first()
    return row.available_at_tick if row is not None else None


def get_next_call_transition_tick(
    session: Session, simulation_id: str, after_tick: int
) -> int | None:
    candidates = []
    for channel in session.query(Channel).filter(
        Channel.simulation_id == simulation_id,
        Channel.kind == "phone_dm",
    ):
        metadata = channel.metadata_ or {}
        if metadata.get("call_state") != "ringing":
            continue
        timeout = int(metadata.get("ring_timeout_tick", after_tick))
        if timeout <= after_tick:
            return after_tick
        candidates.append(timeout)
    return min(candidates) if candidates else None


def get_kami_notifications(
    session: Session, kami_id: str, tick: int
) -> list[dict]:
    """Return non-content notification events physically audible in a kami."""
    recipients = {agent.entity_id for agent in fs.get_agents_in_kami(session, kami_id)}
    if not recipients:
        return []
    simulation_id = fs.resolve_simulation_id(session, kami_id)
    rows = session.query(MessageDelivery).filter(
        MessageDelivery.simulation_id == simulation_id,
        MessageDelivery.recipient_id.in_(recipients),
        MessageDelivery.status == "pending",
        MessageDelivery.available_at_tick <= tick,
        MessageDelivery.mode.in_(("ambient", "force_wake")),
    ).all()
    notifications = []
    for delivery in rows:
        message = session.get(Message, delivery.message_id)
        if message is None:
            continue
        notifications.append({
            "message_id": message.message_id,
            "recipient_id": delivery.recipient_id,
            "kind": message.kind,
            "salience": message.salience,
            "description": "phone ringing" if message.kind == "call" else "message notification",
        })
    return notifications


def get_feed_messages(
    session: Session, agent_id: str, tick: int, *, limit: int = 10
) -> list[Message]:
    """Pull subscribed public posts only after an explicit check_feed action."""
    channels = [
        channel
        for channel in get_agent_channels(session, agent_id)
        if channel.kind == "public_post" and agent_id in (channel.subscribers or [])
    ]
    if not channels:
        return []
    return session.query(Message).filter(
        Message.simulation_id == fs.resolve_simulation_id(session, agent_id),
        Message.channel_id.in_([channel.channel_id for channel in channels]),
        Message.sent_at_tick <= tick,
    ).order_by(Message.sent_at_tick.desc()).limit(limit).all()


def _require_channel(session: Session, channel_id: str | None) -> Channel:
    channel = session.get(Channel, channel_id) if channel_id else None
    if channel is None:
        raise ValueError(f"Channel {channel_id} not found")
    return channel


def _find_direct_channel(
    session: Session, sender_id: str, recipient_id: str, kind: str
) -> Channel | None:
    simulation_id = fs.resolve_simulation_id(session, sender_id, recipient_id)
    for channel in session.query(Channel).filter(
        Channel.simulation_id == simulation_id,
        Channel.kind == kind,
    ):
        if set(channel.participants or []) == {sender_id, recipient_id}:
            return channel
    return None


def _agent_uses_phone(session: Session, agent_id: str) -> bool:
    states = {row.attribute: row.value for row in fs.get_state(session, agent_id)}
    return bool(states.get("using_phone") or states.get("phone_in_use"))


def _has_remote_contact(session: Session, sender_id: str, recipient_id: str) -> bool:
    return any(
        relation.rel_type == "has_contact_via"
        and {relation.from_entity, relation.to_entity} == {sender_id, recipient_id}
        for relation in fs.get_relations(session, sender_id, direction="both")
    )


def _enforce_group_rate_limit(
    session: Session, channel: Channel, sender_id: str, tick: int
) -> None:
    if channel.kind != "group_chat":
        return
    properties = channel.medium_properties or {}
    metadata = channel.metadata_ or {}
    cooldown_until = int(metadata.get("cooldown_until_tick", -1))
    if tick < cooldown_until:
        raise ValueError(f"Group channel is cooling down until tick {cooldown_until}")
    window = max(1, int(properties.get("conversation_cooldown", 5)))
    recent = session.query(Message).filter(
        Message.simulation_id == channel.simulation_id,
        Message.channel_id == channel.channel_id,
        Message.sent_at_tick >= max(0, tick - window + 1),
        Message.sent_at_tick <= tick,
    ).all()
    active_senders = {message.sender_id for message in recent if message.sent_at_tick == tick}
    max_active = int(
        properties.get("max_active_participants_per_tick", config.group_chat_max_active_per_tick)
    )
    if sender_id not in active_senders and len(active_senders) >= max_active:
        raise ValueError("Group channel active-participant limit reached for this tick")
    threshold = int(
        properties.get("conversation_cooldown_threshold", config.conversation_cooldown_threshold)
    )
    if len(recent) >= threshold:
        raise ValueError("Group channel entered conversation cooldown")
