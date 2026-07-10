"""Application-level lifecycle for persistent agent memory."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import config
from ..factstore.models import Entity
from ..llm.budget import budget
from .consolidator import MemoryConsolidator
from .episodic_store import EpisodicMemory, EpisodicStore

logger = logging.getLogger(__name__)


class MemoryRuntime:
    def __init__(self):
        self.session_factory = None
        self.episodic = EpisodicStore()
        self.consolidator = MemoryConsolidator()

    def configure(
        self,
        session_factory,
        chroma_path: str | Path | None = None,
        vector_backend: str = "sql",
    ) -> None:
        self.session_factory = session_factory
        if session_factory is None:
            self.episodic = EpisodicStore()
            self.consolidator = MemoryConsolidator()
            return
        self.episodic = EpisodicStore(
            session_factory, chroma_path, vector_backend=vector_backend
        )
        self.consolidator = MemoryConsolidator(session_factory)

    def stage_events(
        self, session: Session, simulation_id: str, events: list[dict]
    ) -> list[EpisodicMemory]:
        if self.session_factory is None:
            return []
        return self.episodic.stage_event_memories(session, simulation_id, events)

    def index_committed(self, memories: list[EpisodicMemory]) -> None:
        if self.session_factory is None or not memories:
            return
        try:
            self.episodic.index_committed(memories)
        except Exception:
            logger.exception("Could not update Chroma after memory commit")

    def prompt_context(
        self,
        agent_id: str,
        query: str,
        present_agents: list[str],
        current_tick: int,
        simulation_id: str,
    ) -> tuple[str, str]:
        if self.session_factory is None:
            return "", ""
        memories = self.episodic.recall(
            agent_id,
            query=query,
            k=5,
            present_agents=present_agents,
            current_tick=current_tick,
            simulation_id=simulation_id,
        )
        memory_text = "\n".join(
            f"- [tick {memory.tick}] {memory.content}" for memory in memories
        )
        return memory_text, self.consolidator.get_long_term_text(agent_id)

    async def consolidate_if_due(self, simulation_id: str, tick: int) -> list[dict]:
        if self.session_factory is None:
            return []
        ticks_per_day = max(1, round(1440 / max(1, config.tick_in_sim_minutes)))
        if (tick + 1) % ticks_per_day != 0:
            return []
        start_tick = max(0, tick - ticks_per_day + 1)
        agent_ids = self.episodic.agents_with_memories(
            simulation_id, start_tick, tick
        )
        results: list[dict] = []
        for agent_id in agent_ids:
            session = self.session_factory()
            try:
                agent = session.get(Entity, agent_id)
                if (
                    agent is None
                    or agent.kind != "agent"
                    or agent.simulation_id != simulation_id
                ):
                    continue
                archetype = dict(agent.archetype or {})
            finally:
                session.close()
            memories = self.episodic.get_day_memories(
                agent_id, start_tick, tick, simulation_id
            )
            with budget.scope(simulation_id):
                result = await self.consolidator.consolidate_day(
                    agent_id=agent_id,
                    day_memories=[
                        {"tick": memory.tick, "content": memory.content}
                        for memory in memories
                    ],
                    persona={
                        "name": archetype.get("name", agent_id),
                        "background": archetype.get("background", ""),
                    },
                    goals=dict(archetype.get("goals") or {}),
                    current_tick=tick,
                )
            results.append({"agent_id": agent_id, **result})
        return results

    def delete_simulation(self, simulation_id: str) -> None:
        self.episodic.delete_simulation(simulation_id)


memory_runtime = MemoryRuntime()
