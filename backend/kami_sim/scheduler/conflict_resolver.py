"""Conflict resolution for concurrent proposals (spec §2.5)."""

from __future__ import annotations

from .write_committer import compute_initiative


def order_intents_by_initiative(
    intents: list[dict], tick: int
) -> list[dict]:
    """Sort intents by initiative score for deterministic ordering."""
    scored = []
    for intent in intents:
        agent_id = intent.get("agent_id", "")
        fatigue = intent.get("fatigue", 0.0)
        initiative = compute_initiative(agent_id, tick, fatigue)
        scored.append((initiative, intent))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored]
