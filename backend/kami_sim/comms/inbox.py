"""Agent inbox and pull-based feed context."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..factstore.models import Channel, Entity
from .channels import get_feed_messages, get_unread_messages, read_message


def get_inbox_digest(
    session: Session,
    agent_id: str,
    max_messages: int = 8,
    *,
    current_tick: int | None = None,
) -> list[dict]:
    """Get available unread messages with sender and channel context."""
    unread = get_unread_messages(session, agent_id, current_tick=current_tick)
    digest = []
    for message in unread[-max_messages:]:
        sender = session.get(Entity, message.sender_id)
        channel = session.get(Channel, message.channel_id)
        digest.append({
            "message_id": message.message_id,
            "channel_id": message.channel_id,
            "channel_kind": channel.kind if channel else "unknown",
            "sender_id": message.sender_id,
            "sender_name": sender.canonical_name if sender else message.sender_id,
            "content": message.content[:1000],
            "kind": message.kind,
            "sent_at_tick": message.sent_at_tick,
            "salience": message.salience,
        })
    return digest


def get_feed_digest(
    session: Session, agent_id: str, tick: int, max_messages: int = 10
) -> list[dict]:
    """Return public posts after the agent explicitly checks their feed."""
    result = []
    for message in get_feed_messages(session, agent_id, tick, limit=max_messages):
        sender = session.get(Entity, message.sender_id)
        result.append({
            "message_id": message.message_id,
            "channel_id": message.channel_id,
            "sender_id": message.sender_id,
            "sender_name": sender.canonical_name if sender else message.sender_id,
            "content": message.content[:1000],
            "sent_at_tick": message.sent_at_tick,
            "salience": message.salience,
        })
    return result


def process_read(
    session: Session,
    agent_id: str,
    message_ids: list[str],
    tick: int,
) -> None:
    """Mark only messages actually passed through successful cognition as read."""
    for message_id in dict.fromkeys(message_ids):
        read_message(session, message_id, agent_id, tick)
