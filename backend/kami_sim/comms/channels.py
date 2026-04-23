"""CommsLayer channels — non-local communication (spec §2.7).

Channels are first-class entities with lifecycle, participants, and medium properties.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..factstore.models import Channel, Message, ReadReceipt
from ..factstore import tools as fs


CHANNEL_KINDS = {
    "phone_dm", "sms", "messenger_dm", "group_chat", "email",
    "radio_broadcast", "tv_broadcast", "public_post", "letter_mail",
}


def create_channel(
    session: Session,
    kind: str,
    participants: list[str],
    tick: int,
    medium_properties: dict | None = None,
    metadata: dict | None = None,
) -> Channel:
    """Create a communication channel."""
    channel = Channel(
        channel_id=f"chan_{uuid.uuid4().hex[:10]}",
        kind=kind,
        participants=participants,
        subscribers=participants.copy(),
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
) -> Message:
    """Send a message on a channel."""
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise ValueError(f"Channel {channel_id} not found")
    if sender_id not in channel.participants:
        raise ValueError(f"Sender {sender_id} not in channel participants")

    msg = Message(
        message_id=f"msg_{uuid.uuid4().hex[:10]}",
        channel_id=channel_id,
        sender_id=sender_id,
        content=content,
        sent_at_tick=tick,
        salience=salience,
    )
    session.add(msg)
    session.flush()
    return msg


def read_message(
    session: Session,
    message_id: str,
    agent_id: str,
    tick: int,
) -> ReadReceipt:
    """Record that an agent has read a message."""
    receipt = ReadReceipt(
        message_id=message_id,
        agent_id=agent_id,
        read_at_tick=tick,
    )
    session.add(receipt)
    session.flush()
    return receipt


def get_unread_messages(
    session: Session,
    agent_id: str,
) -> list[Message]:
    """Get messages not yet read by this agent."""
    # Get all channels where agent is a participant
    channels = session.query(Channel).all()
    agent_channels = [c for c in channels if agent_id in (c.participants or [])]

    unread = []
    for channel in agent_channels:
        messages = (
            session.query(Message)
            .filter(Message.channel_id == channel.channel_id)
            .all()
        )
        for msg in messages:
            receipt = (
                session.query(ReadReceipt)
                .filter(
                    ReadReceipt.message_id == msg.message_id,
                    ReadReceipt.agent_id == agent_id,
                )
                .first()
            )
            if receipt is None and msg.sender_id != agent_id:
                unread.append(msg)

    return unread
