"""MemoryConsolidator — sleep-time reflection (spec §2.6).

Four-level hierarchy: L0 raw episodic, L1 daily summaries, L2 semantic insights,
L3 life narrative. Runs nightly per agent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..llm.client import llm_client

logger = logging.getLogger(__name__)


@dataclass
class Insight:
    """L2 semantic insight."""

    insight_id: str
    agent_id: str
    content: str
    strength: float = 1.0
    created_tick: int = 0
    last_reinforced_tick: int = 0
    category: str = ""  # person, self, world, place


@dataclass
class AgentMemoryState:
    """Per-agent memory hierarchy state."""

    daily_summaries: list[dict] = field(default_factory=list)  # L1
    insights: list[Insight] = field(default_factory=list)  # L2
    life_narrative: str = ""  # L3
    last_consolidation_tick: int = 0


class MemoryConsolidator:
    """Manages nightly consolidation for all agents."""

    def __init__(self):
        self._agent_states: dict[str, AgentMemoryState] = {}

    def get_state(self, agent_id: str) -> AgentMemoryState:
        if agent_id not in self._agent_states:
            self._agent_states[agent_id] = AgentMemoryState()
        return self._agent_states[agent_id]

    async def consolidate_day(
        self,
        agent_id: str,
        day_memories: list[dict],
        persona: dict,
        goals: dict,
        current_tick: int,
    ) -> dict:
        """Run nightly consolidation for one agent.

        Phases: summarize -> integrate insights -> reflect on goals -> rebalance emotion.
        """
        state = self.get_state(agent_id)

        # Phase 1: Daily summarization (cheap LLM)
        summary = await self._summarize_day(agent_id, day_memories, persona)
        state.daily_summaries.append({
            "tick": current_tick,
            "summary": summary.get("summary", ""),
            "candidates": summary.get("candidate_insights", []),
        })

        # Phase 2: Insight integration (mid-tier)
        for candidate in summary.get("candidate_insights", []):
            await self._integrate_insight(agent_id, candidate, current_tick)

        # Phase 3: Goal reflection
        goal_deltas = await self._reflect_goals(agent_id, persona, goals, summary, current_tick)

        # Phase 4: Emotional rebalancing (algorithmic)
        self._rebalance_emotions(agent_id)

        # Enforce hard cap on insights
        self._enforce_insight_cap(agent_id, max_insights=40)

        state.last_consolidation_tick = current_tick

        return {
            "summary": summary,
            "goal_deltas": goal_deltas,
            "active_insights": len(state.insights),
        }

    async def _summarize_day(
        self, agent_id: str, memories: list[dict], persona: dict
    ) -> dict:
        """Phase 1: Generate daily summary and candidate insights."""
        if not memories:
            return {"summary": "An uneventful day.", "candidate_insights": []}

        memory_text = "\n".join(
            f"- [tick {m.get('tick', '?')}] {m.get('content', '')}" for m in memories
        )

        try:
            response = await llm_client.call(
                messages=[{
                    "role": "user",
                    "content": f"""Summarize this person's day in 2-4 sentences. Then list 3-7 potential insights they might have gained.

Person: {persona.get('name', 'Unknown')}
Background: {persona.get('background', '')}

Today's experiences:
{memory_text}

Format your response as:
SUMMARY: [2-4 sentences]
INSIGHTS:
- [insight 1]
- [insight 2]
...""",
                }],
                system="You summarize a person's daily experiences into a brief diary entry and extract potential insights about people, self, or the world.",
                tier="cheap",
                component="Consolidator",
                max_tokens=500,
            )

            text = response.get("content", "")
            # Simple parsing
            summary = ""
            insights = []
            if "SUMMARY:" in text:
                parts = text.split("INSIGHTS:")
                summary = parts[0].replace("SUMMARY:", "").strip()
                if len(parts) > 1:
                    for line in parts[1].strip().split("\n"):
                        line = line.strip().lstrip("- ")
                        if line:
                            insights.append(line)

            return {"summary": summary or text, "candidate_insights": insights}

        except Exception as e:
            logger.error(f"Day summarization failed for {agent_id}: {e}")
            return {"summary": "The day passed.", "candidate_insights": []}

    async def _integrate_insight(
        self, agent_id: str, candidate: str, tick: int
    ):
        """Phase 2: Add, reinforce, or modify existing insights."""
        state = self.get_state(agent_id)

        # Simple deduplication: check for similar existing insights
        for existing in state.insights:
            # Basic similarity check (keyword overlap)
            existing_words = set(existing.content.lower().split())
            candidate_words = set(candidate.lower().split())
            overlap = len(existing_words & candidate_words)
            if overlap > 3:
                # Reinforce existing
                existing.strength = min(2.0, existing.strength + 0.2)
                existing.last_reinforced_tick = tick
                return

        # New insight
        import uuid
        state.insights.append(Insight(
            insight_id=f"ins_{uuid.uuid4().hex[:8]}",
            agent_id=agent_id,
            content=candidate,
            strength=1.0,
            created_tick=tick,
            last_reinforced_tick=tick,
        ))

    async def _reflect_goals(
        self, agent_id: str, persona: dict, goals: dict,
        day_summary: dict, tick: int,
    ) -> dict:
        """Phase 3: Reflect on goals and produce deltas."""
        # Placeholder — returns empty deltas for MVP
        return {}

    def _rebalance_emotions(self, agent_id: str):
        """Phase 4: Exponential decay of emotional load."""
        # Algorithmic, no LLM — applied via state updates
        pass

    def _enforce_insight_cap(self, agent_id: str, max_insights: int = 40):
        """Remove weakest insights if over cap."""
        state = self.get_state(agent_id)
        if len(state.insights) > max_insights:
            state.insights.sort(key=lambda i: i.strength, reverse=True)
            state.insights = state.insights[:max_insights]

    def get_insights_text(self, agent_id: str) -> str:
        """Get formatted insights for agent cognition prompt."""
        state = self.get_state(agent_id)
        if not state.insights:
            return ""
        lines = [f"- {i.content} (strength={i.strength:.1f})" for i in state.insights]
        return "\n".join(lines)
