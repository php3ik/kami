"""Persistent four-level memory consolidation for simulated agents."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..config import config
from ..determinism import generate_id
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
    provenance: list[dict] = field(default_factory=list)


@dataclass
class AgentMemoryState:
    daily_summaries: list[dict] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)
    archived_insights: list[Insight] = field(default_factory=list)
    life_narrative: str = ""
    last_consolidation_tick: int = 0
    last_narrative_tick: int = -1


INSIGHT_INTEGRATION_TOOL = {
    "name": "integrate_insights",
    "description": "Apply grounded changes to the agent's semantic insights.",
    "input_schema": {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "strengthen", "modify", "archive"],
                        },
                        "insight_id": {"type": "string"},
                        "content": {"type": "string"},
                        "category": {"type": "string"},
                        "source_candidate": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                    "required": ["action"],
                },
            }
        },
        "required": ["operations"],
    },
}


def _extract_json_object(text: str) -> dict:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, flags=re.S)
        if match is None:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


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
            insight_rows = (
                session.query(SemanticInsight)
                .filter(SemanticInsight.agent_id == agent_id)
                .order_by(
                    SemanticInsight.strength.desc(),
                    SemanticInsight.last_reinforced_tick.desc(),
                )
                .all()
            )
            profile = session.get(AgentMemoryProfile, agent_id)
            insights = [
                Insight(
                    insight_id=row.insight_id,
                    agent_id=row.agent_id,
                    content=row.content,
                    strength=float(row.strength),
                    created_tick=row.created_tick,
                    last_reinforced_tick=row.last_reinforced_tick,
                    category=row.category,
                    provenance=list(row.provenance or []),
                )
                for row in insight_rows
            ]
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
                    insight
                    for insight, row in zip(insights, insight_rows)
                    if row.status == "active"
                ],
                archived_insights=[
                    insight
                    for insight, row in zip(insights, insight_rows)
                    if row.status != "active"
                ],
                life_narrative=profile.life_narrative if profile else "",
                last_consolidation_tick=(
                    profile.last_consolidation_tick if profile else 0
                ),
                last_narrative_tick=(profile.last_narrative_tick if profile else -1),
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
        candidates = [
            str(candidate).strip()
            for candidate in summary.get("candidate_insights", [])
            if str(candidate).strip()
        ]
        await self._integrate_candidates(agent_id, candidates, current_tick, state)
        goal_deltas = await self._reflect_goals(
            agent_id, persona, goals, summary, state, current_tick
        )
        emotion = self._rebalance_emotions(
            dict(persona.get("emotion") or {}), day_memories
        )
        self._decay_insights(state, current_tick)
        self._enforce_insight_cap(
            agent_id,
            max_insights=config.max_active_l2_insights_per_agent,
            state=state,
            tick=current_tick,
        )
        narrative_updated = False
        if self._narrative_update_due(state, current_tick):
            narrative, challenge_operations = await self._update_life_narrative(
                agent_id,
                persona,
                {**goals, **goal_deltas},
                state,
                current_tick,
            )
            if narrative:
                state.life_narrative = narrative
                state.last_narrative_tick = current_tick
                narrative_updated = True
            self._apply_insight_operations(
                agent_id,
                challenge_operations,
                current_tick,
                state,
                source="weekly challenge pass",
            )
        state.last_consolidation_tick = current_tick
        self._save_state(
            agent_id,
            state,
            goal_deltas=goal_deltas,
            emotion=emotion,
        )
        return {
            "summary": summary,
            "goal_deltas": goal_deltas,
            "active_insights": len(state.insights),
            "life_narrative_updated": narrative_updated,
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

    def _llm_available(self, tier: str = "strong") -> bool:
        model = (
            config.strong_model_name if tier == "strong" else config.cheap_model_name
        )
        provider = (
            model.split(":", 1)[0].strip().lower()
            if ":" in model
            else config.llm_provider.strip().lower()
        )
        return bool(
            {
                "anthropic": config.anthropic_api_key,
                "openai": config.openai_api_key,
                "gemini": config.gemini_api_key,
            }.get(provider)
        )

    async def _integrate_candidates(
        self,
        agent_id: str,
        candidates: list[str],
        tick: int,
        state: AgentMemoryState,
    ) -> None:
        if not candidates:
            return
        if not self._llm_available():
            for candidate in candidates:
                await self._integrate_insight(agent_id, candidate, tick, state=state)
            return
        insight_context = [
            {
                "insight_id": insight.insight_id,
                "content": insight.content,
                "strength": insight.strength,
                "last_reinforced_tick": insight.last_reinforced_tick,
            }
            for insight in state.insights
        ]
        try:
            response = await llm_client.call(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Integrate every candidate into the existing insight set. "
                            "Prefer strengthening or modifying a matching insight over "
                            "adding a duplicate. Archive only when a candidate directly "
                            "contradicts an insight that is no longer defensible.\n\n"
                            f"Existing insights:\n{json.dumps(insight_context, ensure_ascii=False)}\n\n"
                            f"Candidates:\n{json.dumps(candidates, ensure_ascii=False)}"
                        ),
                    }
                ],
                system=(
                    "You maintain a psychologically coherent semantic memory. Use the "
                    "integrate_insights tool once and ground every operation in a candidate."
                ),
                tier="strong",
                component="MemoryConsolidator",
                tick=tick,
                tools=[INSIGHT_INTEGRATION_TOOL],
                max_tokens=1200,
                temperature=0.2,
            )
            payload = next(
                (
                    call.get("input") or {}
                    for call in response.get("tool_calls", [])
                    if call.get("name") == "integrate_insights"
                ),
                _extract_json_object(response.get("content", "")),
            )
            operations = payload.get("operations", [])
            if not isinstance(operations, list) or not operations:
                raise ValueError("Insight integration returned no operations")
            self._apply_insight_operations(
                agent_id, operations, tick, state, source="nightly integration"
            )
        except Exception as exc:
            logger.warning("Insight integration failed for %s: %s", agent_id, exc)
            for candidate in candidates:
                await self._integrate_insight(agent_id, candidate, tick, state=state)

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
                existing.provenance.append(
                    {
                        "tick": tick,
                        "action": "strengthen",
                        "source": candidate,
                    }
                )
                if self.session_factory is not None and owns_state:
                    self._save_state(agent_id, state)
                return existing
        insight = Insight(
            insight_id=generate_id("ins_"),
            agent_id=agent_id,
            content=candidate,
            strength=1.0,
            created_tick=tick,
            last_reinforced_tick=tick,
            provenance=[{"tick": tick, "action": "add", "source": candidate}],
        )
        state.insights.append(insight)
        if self.session_factory is not None and owns_state:
            self._save_state(agent_id, state)
        return insight

    def _apply_insight_operations(
        self,
        agent_id: str,
        operations: list[dict],
        tick: int,
        state: AgentMemoryState,
        source: str,
    ) -> None:
        for operation in operations[:100]:
            if not isinstance(operation, dict):
                continue
            action = str(operation.get("action") or "").strip().lower()
            insight_id = str(operation.get("insight_id") or "")
            insight = next(
                (item for item in state.insights if item.insight_id == insight_id),
                None,
            )
            candidate = str(
                operation.get("source_candidate") or operation.get("content") or source
            ).strip()
            provenance = {"tick": tick, "action": action, "source": candidate[:2000]}
            if action == "add":
                content = str(operation.get("content") or "").strip()
                if not content:
                    continue
                duplicate = next(
                    (
                        item
                        for item in state.insights
                        if item.content.casefold() == content.casefold()
                    ),
                    None,
                )
                if duplicate is not None:
                    duplicate.strength = min(2.0, duplicate.strength + 0.2)
                    duplicate.last_reinforced_tick = tick
                    duplicate.provenance.append(
                        {**provenance, "action": "strengthen"}
                    )
                    continue
                state.insights.append(
                    Insight(
                        insight_id=generate_id("ins_"),
                        agent_id=agent_id,
                        content=content[:4000],
                        strength=1.0,
                        created_tick=tick,
                        last_reinforced_tick=tick,
                        category=str(operation.get("category") or "")[:100],
                        provenance=[provenance],
                    )
                )
            elif insight is not None and action == "strengthen":
                try:
                    amount = float(operation.get("amount", 0.2))
                except (TypeError, ValueError):
                    amount = 0.2
                insight.strength = min(2.0, insight.strength + max(0.05, min(0.5, amount)))
                insight.last_reinforced_tick = tick
                insight.provenance.append(provenance)
            elif insight is not None and action == "modify":
                content = str(operation.get("content") or "").strip()
                if not content:
                    continue
                provenance["previous_content"] = insight.content[:2000]
                insight.content = content[:4000]
                insight.last_reinforced_tick = tick
                insight.provenance.append(provenance)
            elif insight is not None and action == "archive":
                insight.provenance.append(provenance)
                state.insights.remove(insight)
                state.archived_insights.append(insight)

    async def _reflect_goals(
        self,
        agent_id: str,
        persona: dict,
        goals: dict,
        day_summary: dict,
        state: AgentMemoryState,
        tick: int,
    ) -> dict:
        if not self._llm_available():
            return {}
        try:
            response = await llm_client.call(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Return a JSON object with an 'updates' object. Only include "
                            "goal levels that should actually change: life, seasonal, "
                            "daily, or current. Keep goals concrete and consistent with "
                            "the person's identity.\n\n"
                            f"Person: {persona.get('name', agent_id)}\n"
                            f"Current goals: {json.dumps(goals, ensure_ascii=False)}\n"
                            f"Day summary: {json.dumps(day_summary, ensure_ascii=False)}\n"
                            f"Active insights: {json.dumps([item.content for item in state.insights], ensure_ascii=False)}"
                        ),
                    }
                ],
                system="You update a person's goal hierarchy through conservative deltas.",
                tier="strong",
                component="MemoryConsolidator",
                tick=tick,
                response_format={"type": "json_object"},
                max_tokens=700,
                temperature=0.2,
            )
            payload = _extract_json_object(response.get("content", ""))
            updates = payload.get("updates", {})
            if not isinstance(updates, dict):
                return {}
            return {
                level: str(value).strip()[:2000]
                for level, value in updates.items()
                if level in {"life", "seasonal", "daily", "current"}
                and str(value).strip()
                and str(value).strip() != str(goals.get(level) or "").strip()
            }
        except Exception as exc:
            logger.warning("Goal reflection failed for %s: %s", agent_id, exc)
            return {}

    @staticmethod
    def _rebalance_emotions(emotion: dict, memories: list[dict]) -> dict:
        try:
            intensity = float(emotion.get("intensity", 0.3))
        except (TypeError, ValueError):
            intensity = 0.3
        peak = (0.0, {})
        for memory in memories:
            try:
                importance = float(memory.get("importance", 0.5))
            except (TypeError, ValueError):
                importance = 0.5
            importance = max(0.0, min(1.0, importance))
            if importance > peak[0]:
                peak = (importance, memory)
        intensity = max(0.0, min(1.0, intensity * 0.82))
        if peak[0] >= 0.85:
            intensity = max(intensity, min(1.0, peak[0] * 0.8))
            emotion["last_trigger"] = str(peak[1].get("content") or "")[:1000]
        loads = emotion.get("loads")
        if isinstance(loads, dict):
            emotion["loads"] = {
                key: round(max(0.0, float(value) * (0.96 if key == "trauma" else 0.82)), 4)
                for key, value in loads.items()
                if isinstance(value, (int, float))
            }
        emotion["intensity"] = round(intensity, 4)
        if intensity < 0.1:
            emotion["dominant"] = "neutral"
        else:
            emotion.setdefault("dominant", "neutral")
        return emotion

    def _decay_insights(self, state: AgentMemoryState, tick: int) -> None:
        ticks_per_day = max(1, round(1440 / max(1, config.tick_in_sim_minutes)))
        cutoff = config.insight_decay_days_without_reinforcement * ticks_per_day
        for insight in list(state.insights):
            if tick - insight.last_reinforced_tick < cutoff:
                continue
            insight.strength = max(0.0, insight.strength - 0.08)
            insight.provenance.append(
                {"tick": tick, "action": "decay", "source": "age threshold"}
            )
            if insight.strength <= 0.2:
                state.insights.remove(insight)
                state.archived_insights.append(insight)

    @staticmethod
    def _narrative_update_due(state: AgentMemoryState, tick: int) -> bool:
        ticks_per_day = max(1, round(1440 / max(1, config.tick_in_sim_minutes)))
        interval = max(1, config.consolidation_phase_5_interval_days) * ticks_per_day
        if state.last_narrative_tick >= 0:
            return tick - state.last_narrative_tick >= interval
        return tick + 1 >= interval

    async def _update_life_narrative(
        self,
        agent_id: str,
        persona: dict,
        goals: dict,
        state: AgentMemoryState,
        tick: int,
    ) -> tuple[str | None, list[dict]]:
        if not self._llm_available():
            return None, []
        challenge_sample = sorted(
            state.insights,
            key=lambda insight: (insight.last_reinforced_tick, insight.strength),
        )[:8]
        try:
            response = await llm_client.call(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Return JSON with 'life_narrative' and 'insight_operations'. "
                            "Rewrite the first-person life narrative in 500-1000 tokens, "
                            "preserving stable identity while incorporating grounded change. "
                            "Challenge the sampled insights and emit modify/archive/strengthen "
                            "operations only when recent evidence warrants it.\n\n"
                            f"Person: {json.dumps(persona, ensure_ascii=False)}\n"
                            f"Current goals: {json.dumps(goals, ensure_ascii=False)}\n"
                            f"Current narrative: {state.life_narrative}\n"
                            f"Recent summaries: {json.dumps(state.daily_summaries[-7:], ensure_ascii=False)}\n"
                            f"Challenge sample: {json.dumps([{'insight_id': item.insight_id, 'content': item.content, 'strength': item.strength} for item in challenge_sample], ensure_ascii=False)}"
                        ),
                    }
                ],
                system="You maintain a coherent life narrative without inventing events.",
                tier="strong",
                component="MemoryConsolidator",
                tick=tick,
                response_format={"type": "json_object"},
                max_tokens=1800,
                temperature=0.3,
            )
            payload = _extract_json_object(response.get("content", ""))
            narrative = str(payload.get("life_narrative") or "").strip()
            operations = payload.get("insight_operations", [])
            return (
                narrative[:8000] or None,
                operations if isinstance(operations, list) else [],
            )
        except Exception as exc:
            logger.warning("Life narrative update failed for %s: %s", agent_id, exc)
            return None, []

    def _enforce_insight_cap(
        self,
        agent_id: str,
        max_insights: int = 40,
        state: AgentMemoryState | None = None,
        tick: int | None = None,
    ) -> None:
        state = state or self.get_state(agent_id)
        max_insights = max(0, max_insights)
        if len(state.insights) > max_insights:
            state.insights.sort(
                key=lambda insight: (
                    insight.strength,
                    insight.last_reinforced_tick,
                    insight.insight_id,
                ),
                reverse=True,
            )
            archived = state.insights[max_insights:]
            for insight in archived:
                insight.provenance.append(
                    {
                        "tick": tick if tick is not None else state.last_consolidation_tick,
                        "action": "archive",
                        "source": "active insight cap",
                    }
                )
            state.insights = state.insights[:max_insights]
            state.archived_insights.extend(archived)

    def add_insight(
        self, agent_id: str, content: str, tick: int, category: str = ""
    ) -> Insight:
        value = content.strip()
        if not value:
            raise ValueError("Insight content cannot be empty")
        state = self.get_state(agent_id)
        insight = Insight(
            insight_id=generate_id("ins_"),
            agent_id=agent_id,
            content=value[:4000],
            strength=1.0,
            created_tick=tick,
            last_reinforced_tick=tick,
            category=category.strip()[:100],
            provenance=[{"tick": tick, "action": "add", "source": "manual"}],
        )
        state.insights.append(insight)
        self._enforce_insight_cap(
            agent_id,
            config.max_active_l2_insights_per_agent,
            state,
            tick=tick,
        )
        self._save_state(agent_id, state)
        return insight

    def strengthen_insight(
        self, agent_id: str, insight_id: str, tick: int, amount: float = 0.3
    ) -> bool:
        state = self.get_state(agent_id)
        for insight in state.insights:
            if insight.insight_id == insight_id:
                amount = max(0.05, min(0.5, float(amount)))
                insight.strength = min(2.0, insight.strength + amount)
                insight.last_reinforced_tick = tick
                insight.provenance.append(
                    {"tick": tick, "action": "strengthen", "source": "manual"}
                )
                self._save_state(agent_id, state)
                return True
        return False

    def modify_insight(
        self, agent_id: str, insight_id: str, content: str, tick: int
    ) -> bool:
        value = content.strip()
        if not value:
            raise ValueError("Insight content cannot be empty")
        state = self.get_state(agent_id)
        for insight in state.insights:
            if insight.insight_id == insight_id:
                insight.provenance.append(
                    {
                        "tick": tick,
                        "action": "modify",
                        "source": "manual",
                        "previous_content": insight.content[:2000],
                    }
                )
                insight.content = value[:4000]
                insight.last_reinforced_tick = tick
                self._save_state(agent_id, state)
                return True
        return False

    def archive_insight(
        self, agent_id: str, insight_id: str, tick: int | None = None
    ) -> bool:
        state = self.get_state(agent_id)
        insight = next(
            (item for item in state.insights if item.insight_id == insight_id), None
        )
        if insight is None:
            return False
        insight.provenance.append(
            {
                "tick": tick if tick is not None else state.last_consolidation_tick,
                "action": "archive",
                "source": "manual",
            }
        )
        state.insights.remove(insight)
        state.archived_insights.append(insight)
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

    def _save_state(
        self,
        agent_id: str,
        state: AgentMemoryState,
        goal_deltas: dict | None = None,
        emotion: dict | None = None,
    ) -> None:
        if self.session_factory is None:
            self._agent_states[agent_id] = state
            return
        session = self.session_factory()
        try:
            agent = session.get(Entity, agent_id)
            if agent is None or agent.kind != "agent":
                raise ValueError(f"Agent not found: {agent_id}")
            scope = agent.simulation_id
            if goal_deltas or emotion is not None:
                archetype = dict(agent.archetype or {})
                if goal_deltas:
                    goals = dict(archetype.get("goals") or {})
                    goals.update(goal_deltas)
                    archetype["goals"] = goals
                if emotion is not None:
                    archetype["emotion"] = emotion
                agent.archetype = archetype
            profile = session.get(AgentMemoryProfile, agent_id)
            if profile is None:
                profile = AgentMemoryProfile(agent_id=agent_id, simulation_id=scope)
                session.add(profile)
            profile.life_narrative = state.life_narrative
            profile.last_consolidation_tick = state.last_consolidation_tick
            profile.last_narrative_tick = state.last_narrative_tick
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
            for insight, status in (
                [(item, "active") for item in state.insights]
                + [(item, "archived") for item in state.archived_insights]
            ):
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
                row.status = status
                row.provenance = list(insight.provenance)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
