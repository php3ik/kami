"""Persistent four-level memory consolidation for simulated agents."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..config import config
from ..factstore.models import (
    AgentMemoryProfile,
    Entity,
    MemorySummary,
    SemanticInsight,
)
from ..llm.client import llm_client

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class Insight:
    insight_id: str
    agent_id: str
    content: str
    strength: float = 1.0
    created_tick: int = 0
    last_reinforced_tick: int = 0
    category: str = ""


@dataclass
class AgentMemoryState:
    daily_summaries: list[dict] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)
    life_narrative: str = ""
    last_consolidation_tick: int = 0


class MemoryConsolidator:
    """Runs and persists L1-L3 memory consolidation."""

    def __init__(self, session_factory=None):
        self.session_factory = session_factory
        self._agent_states: dict[str, AgentMemoryState] = {}

    def get_state(self, agent_id: str) -> AgentMemoryState:
        if self.session_factory is None:
            return self._agent_states.setdefault(agent_id, AgentMemoryState())
        session = self.session_factory()
        try:
            summaries = (
                session.query(MemorySummary)
                .filter(MemorySummary.agent_id == agent_id)
                .order_by(MemorySummary.tick.asc())
                .all()
            )
            insights = (
                session.query(SemanticInsight)
                .filter(
                    SemanticInsight.agent_id == agent_id,
                    SemanticInsight.status == "active",
                )
                .order_by(
                    SemanticInsight.strength.desc(),
                    SemanticInsight.last_reinforced_tick.desc(),
                )
                .all()
            )
            profile = session.get(AgentMemoryProfile, agent_id)
            return AgentMemoryState(
                daily_summaries=[
                    {
                        "tick": row.tick,
                        "summary": row.summary,
                        "candidates": list(row.candidates or []),
                    }
                    for row in summaries
                ],
                insights=[
                    Insight(
                        insight_id=row.insight_id,
                        agent_id=row.agent_id,
                        content=row.content,
                        strength=float(row.strength),
                        created_tick=row.created_tick,
                        last_reinforced_tick=row.last_reinforced_tick,
                        category=row.category,
                    )
                    for row in insights
                ],
                life_narrative=profile.life_narrative if profile else "",
                last_consolidation_tick=(
                    profile.last_consolidation_tick if profile else 0
                ),
            )
        finally:
            session.close()

    async def consolidate_day(
        self,
        agent_id: str,
        day_memories: list[dict],
        persona: dict,
        goals: dict,
        current_tick: int,
    ) -> dict:
        state = self.get_state(agent_id)
        summary = await self._summarize_day(agent_id, day_memories, persona)
        state.daily_summaries.append(
            {
                "tick": current_tick,
                "summary": summary.get("summary", ""),
                "candidates": summary.get("candidate_insights", []),
            }
        )
        for candidate in summary.get("candidate_insights", []):
            await self._integrate_insight(
                agent_id, candidate, current_tick, state=state
            )
        goal_deltas = await self._reflect_goals(
            agent_id, persona, goals, summary, current_tick
        )
        self._rebalance_emotions(agent_id)
        self._enforce_insight_cap(
            agent_id,
            max_insights=config.max_active_l2_insights_per_agent,
            state=state,
        )
        state.last_consolidation_tick = current_tick
        self._save_state(agent_id, state)
        return {
            "summary": summary,
            "goal_deltas": goal_deltas,
            "active_insights": len(state.insights),
        }

    async def _summarize_day(
        self, agent_id: str, memories: list[dict], persona: dict
    ) -> dict:
        if not memories:
            return {"summary": "An uneventful day.", "candidate_insights": []}
        memory_text = "\n".join(
            f"- [tick {memory.get('tick', '?')}] {memory.get('content', '')}"
            for memory in memories
        )
        try:
            response = await llm_client.call(
                messages=[
                    {
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
- [insight 2]""",
                    }
                ],
                system="You summarize a person's daily experiences into a brief diary entry and extract grounded insights about people, self, or the world.",
                tier="cheap",
                component="Consolidator",
                max_tokens=500,
            )
            text = response.get("content", "")
            summary = ""
            insights: list[str] = []
            if "SUMMARY:" in text:
                parts = text.split("INSIGHTS:", 1)
                summary = parts[0].replace("SUMMARY:", "").strip()
                if len(parts) > 1:
                    insights = [
                        line.strip().lstrip("- ")
                        for line in parts[1].strip().split("\n")
                        if line.strip().lstrip("- ")
                    ]
            return {"summary": summary or text, "candidate_insights": insights}
        except Exception as exc:
            logger.error("Day summarization failed for %s: %s", agent_id, exc)
            return {"summary": "The day passed.", "candidate_insights": []}

    async def _integrate_insight(
        self,
        agent_id: str,
        candidate: str,
        tick: int,
        state: AgentMemoryState | None = None,
    ) -> Insight:
        owns_state = state is None
        state = state or self.get_state(agent_id)
        candidate_words = set(candidate.casefold().split())
        for existing in state.insights:
            overlap = len(set(existing.content.casefold().split()) & candidate_words)
            if overlap > 3:
                existing.strength = min(2.0, existing.strength + 0.2)
                existing.last_reinforced_tick = tick
                if self.session_factory is not None and owns_state:
                    self._save_state(agent_id, state)
                return existing
        insight = Insight(
            insight_id=f"ins_{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            content=candidate,
            strength=1.0,
            created_tick=tick,
            last_reinforced_tick=tick,
        )
        state.insights.append(insight)
        if self.session_factory is not None and owns_state:
            self._save_state(agent_id, state)
        return insight

    async def _reflect_goals(
        self,
        agent_id: str,
        persona: dict,
        goals: dict,
        day_summary: dict,
        tick: int,
    ) -> dict:
        return {}

    def _rebalance_emotions(self, agent_id: str) -> None:
        return None

    def _enforce_insight_cap(
        self,
        agent_id: str,
        max_insights: int = 40,
        state: AgentMemoryState | None = None,
    ) -> None:
        state = state or self.get_state(agent_id)
        if len(state.insights) > max_insights:
            state.insights.sort(
                key=lambda insight: (
                    insight.strength,
                    insight.last_reinforced_tick,
                    insight.insight_id,
                ),
                reverse=True,
            )
            state.insights = state.insights[:max_insights]

    def add_insight(
        self, agent_id: str, content: str, tick: int, category: str = ""
    ) -> Insight:
        state = self.get_state(agent_id)
        insight = Insight(
            insight_id=f"ins_{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            content=content,
            strength=1.0,
            created_tick=tick,
            last_reinforced_tick=tick,
            category=category,
        )
        state.insights.append(insight)
        self._enforce_insight_cap(agent_id, config.max_active_l2_insights_per_agent, state)
        self._save_state(agent_id, state)
        return insight

    def strengthen_insight(
        self, agent_id: str, insight_id: str, tick: int, amount: float = 0.3
    ) -> bool:
        state = self.get_state(agent_id)
        for insight in state.insights:
            if insight.insight_id == insight_id:
                insight.strength = min(2.0, insight.strength + amount)
                insight.last_reinforced_tick = tick
                self._save_state(agent_id, state)
                return True
        return False

    def archive_insight(self, agent_id: str, insight_id: str) -> bool:
        state = self.get_state(agent_id)
        before = len(state.insights)
        state.insights = [
            insight for insight in state.insights if insight.insight_id != insight_id
        ]
        if len(state.insights) == before:
            return False
        self._save_state(agent_id, state)
        return True

    def get_insights_text(self, agent_id: str) -> str:
        state = self.get_state(agent_id)
        return "\n".join(
            f"- {insight.content} (strength={insight.strength:.1f})"
            for insight in state.insights
        )

    def get_long_term_text(self, agent_id: str) -> str:
        """Render bounded L1-L3 context for the agent cognition prompt."""
        state = self.get_state(agent_id)
        sections: list[str] = []
        if state.life_narrative:
            sections.append(f"Life narrative:\n{state.life_narrative[:4000]}")
        recent_summaries = [
            item for item in state.daily_summaries[-2:] if item.get("summary")
        ]
        if recent_summaries:
            sections.append(
                "Recent daily summaries:\n"
                + "\n".join(
                    f"- [tick {item['tick']}] {item['summary']}"
                    for item in recent_summaries
                )
            )
        insights = "\n".join(
            f"- {insight.content} (strength={insight.strength:.1f})"
            for insight in state.insights
        )
        if insights:
            sections.append(f"Semantic insights:\n{insights}")
        return "\n\n".join(sections)

    def _save_state(self, agent_id: str, state: AgentMemoryState) -> None:
        if self.session_factory is None:
            self._agent_states[agent_id] = state
            return
        session = self.session_factory()
        try:
            agent = session.get(Entity, agent_id)
            if agent is None or agent.kind != "agent":
                raise ValueError(f"Agent not found: {agent_id}")
            scope = agent.simulation_id
            profile = session.get(AgentMemoryProfile, agent_id)
            if profile is None:
                profile = AgentMemoryProfile(agent_id=agent_id, simulation_id=scope)
                session.add(profile)
            profile.life_narrative = state.life_narrative
            profile.last_consolidation_tick = state.last_consolidation_tick
            profile.updated_at = _utcnow_naive()

            for item in state.daily_summaries:
                digest = hashlib.sha256(
                    f"{scope}:{agent_id}:{item['tick']}".encode("utf-8")
                ).hexdigest()[:20]
                summary_id = f"sum_{digest}"
                row = session.get(MemorySummary, summary_id)
                if row is None:
                    row = MemorySummary(
                        summary_id=summary_id,
                        simulation_id=scope,
                        agent_id=agent_id,
                        tick=int(item["tick"]),
                    )
                    session.add(row)
                row.summary = str(item.get("summary") or "")
                row.candidates = list(item.get("candidates") or [])

            active_ids = {insight.insight_id for insight in state.insights}
            existing_rows = (
                session.query(SemanticInsight)
                .filter(
                    SemanticInsight.simulation_id == scope,
                    SemanticInsight.agent_id == agent_id,
                )
                .all()
            )
            for row in existing_rows:
                if row.insight_id not in active_ids:
                    row.status = "archived"
            for insight in state.insights:
                row = session.get(SemanticInsight, insight.insight_id)
                if row is None:
                    row = SemanticInsight(
                        insight_id=insight.insight_id,
                        simulation_id=scope,
                        agent_id=agent_id,
                    )
                    session.add(row)
                elif row.simulation_id != scope or row.agent_id != agent_id:
                    raise ValueError("Insight belongs to another agent or simulation")
                row.content = insight.content
                row.strength = insight.strength
                row.created_tick = insight.created_tick
                row.last_reinforced_tick = insight.last_reinforced_tick
                row.category = insight.category
                row.status = "active"
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
