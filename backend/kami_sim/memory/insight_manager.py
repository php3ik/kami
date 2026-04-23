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
    import uuid
    state = consolidator.get_state(agent_id)
    insight = Insight(
        insight_id=f"ins_{uuid.uuid4().hex[:8]}",
        agent_id=agent_id,
        content=content,
        strength=1.0,
        created_tick=tick,
        last_reinforced_tick=tick,
        category=category,
    )
    state.insights.append(insight)
    return insight


def strengthen_insight(
    consolidator: MemoryConsolidator,
    agent_id: str,
    insight_id: str,
    tick: int,
    amount: float = 0.3,
) -> bool:
    state = consolidator.get_state(agent_id)
    for ins in state.insights:
        if ins.insight_id == insight_id:
            ins.strength = min(2.0, ins.strength + amount)
            ins.last_reinforced_tick = tick
            return True
    return False


def archive_insight(
    consolidator: MemoryConsolidator,
    agent_id: str,
    insight_id: str,
) -> bool:
    state = consolidator.get_state(agent_id)
    state.insights = [i for i in state.insights if i.insight_id != insight_id]
    return True
