"""Insight manager — tools for LLM-driven insight management (spec §2.6)."""

from __future__ import annotations

from .consolidator import Insight, MemoryConsolidator


def add_insight(
    consolidator: MemoryConsolidator,
    agent_id: str,
    content: str,
    tick: int,
    category: str = "",
) -> Insight:
    """Add a new L2 insight for an agent."""
    return consolidator.add_insight(agent_id, content, tick, category)


def strengthen_insight(
    consolidator: MemoryConsolidator,
    agent_id: str,
    insight_id: str,
    tick: int,
    amount: float = 0.3,
) -> bool:
    return consolidator.strengthen_insight(
        agent_id, insight_id, tick, amount
    )


def archive_insight(
    consolidator: MemoryConsolidator,
    agent_id: str,
    insight_id: str,
) -> bool:
    return consolidator.archive_insight(agent_id, insight_id)
