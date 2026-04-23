"""Agent inbox management — pending message processing."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .channels import get_unread_messages, read_message


def get_inbox_digest(
    session: Session,
    agent_id: str,
    max_messages: int = 5,
) -> list[dict]:
    """Get a digest of unread messages for agent context."""
    unread = get_unread_messages(session, agent_id)
    digest = []
    for msg in unread[:max_messages]:
        digest.append({
            "message_id": msg.message_id,
            "channel_id": msg.channel_id,
            "sender_id": msg.sender_id,
            "content": msg.content[:200],
            "sent_at_tick": msg.sent_at_tick,
            "salience": msg.salience,
        })
    return digest


def process_read(
    session: Session,
    agent_id: str,
    message_ids: list[str],
    tick: int,
):
    """Mark messages as read by the agent."""
    for mid in message_ids:
        try:
            read_message(session, mid, agent_id, tick)
        except Exception:
            pass
