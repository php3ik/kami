"""TickScheduler — BSP-style coordination (spec §2.5).

Two-phase tick model: READ -> COMPUTE -> WRITE -> PROPAGATE.
"""

from __future__ import annotations

import asyncio
import logging
import json
import time
from typing import Any

from sqlalchemy.orm import Session

from ..agent_worker.worker import AgentCognitionWorker
from ..config import config
from ..eventbus.bus import EventBus
from ..factstore import tools as fs
from ..factstore.models import init_db
from ..kami_worker.worker import KamiWorker
from ..llm.budget import budget
from ..spatial.graph import SpatialGraph
from .activity_detector import detect_active_agents, detect_active_kami
from .conflict_resolver import order_intents_by_initiative
from .write_committer import commit_proposals

logger = logging.getLogger(__name__)


class TickScheduler:
    """Coordinates parallel rendering of kami and agents per tick."""

    def __init__(
        self,
        session_factory,
        spatial_graph: SpatialGraph,
        event_bus: EventBus | None = None,
    ):
        self.session_factory = session_factory
        self.spatial_graph = spatial_graph
        self.event_bus = event_bus or EventBus()
        self.current_tick = 0
        self.tick_log: list[dict] = []

    async def run(self, num_ticks: int, start_tick: int = 0) -> list[dict]:
        """Run the simulation for num_ticks ticks."""
        self.current_tick = start_tick
        logger.info(f"Starting simulation: {num_ticks} ticks from tick {start_tick}")

        for i in range(num_ticks):
            tick = self.current_tick
            tick_start = time.time()

            session = self.session_factory()
            try:
                tick_result = await self._run_tick(session, tick)
                tick_result["wall_time_ms"] = int((time.time() - tick_start) * 1000)
                tick_result["tick_cost_usd"] = round(budget.get_tick_cost(tick), 6)
                self.tick_log.append(tick_result)

                if (i + 1) % 10 == 0 or i == 0:
                    logger.info(
                        f"Tick {tick}: {tick_result['active_kami_count']} active kami, "
                        f"{tick_result['active_agent_count']} agents, "
                        f"${tick_result['tick_cost_usd']:.4f}, "
                        f"{tick_result['wall_time_ms']}ms"
                    )
            except Exception as e:
                logger.error(f"Tick {tick} failed: {e}", exc_info=True)
                self.tick_log.append({
                    "tick": tick,
                    "error": str(e),
                    "active_kami_count": 0,
                    "active_agent_count": 0,
                })
            finally:
                session.close()

            self.current_tick += 1

            # Cleanup old event bus data
            self.event_bus.cleanup_tick(tick)

        return self.tick_log

    async def _run_tick(self, session: Session, tick: int) -> dict:
        """Execute one complete BSP tick."""
        all_kami = self.spatial_graph.all_kami_ids()

        # === READ PHASE ===
        active_kami = detect_active_kami(session, self.event_bus, tick, all_kami)
        agents_by_kami = detect_active_agents(session, active_kami)

        total_agents = sum(len(agents) for agents in agents_by_kami.values())

        if not active_kami:
            return {
                "tick": tick,
                "active_kami_count": 0,
                "active_agent_count": 0,
                "events": [],
                "narratives": {},
            }

        # === COMPUTE PHASE 1: Agent cognition (parallel) ===
        agent_worker = AgentCognitionWorker(session, spatial_graph=self.spatial_graph)
        all_intents: dict[str, list[dict]] = {k: [] for k in active_kami}
        all_monologues: dict[str, str] = {}

        agent_tasks = []
        for kami_id, agent_ids in agents_by_kami.items():
            for agent_id in agent_ids:
                # Get recent personal events
                recent = fs.get_events(
                    session, kami_id=kami_id,
                    since_tick=max(0, tick - 5), limit=5,
                )
                recent_dicts = [
                    {"tick": e.tick, "narrative": e.narrative, "event_type": e.event_type}
                    for e in recent
                ]
                agent_tasks.append(
                    (kami_id, agent_id, recent_dicts)
                )

        # Run agent cognition calls (sequential for MVP, parallel later)
        for kami_id, agent_id, recent in agent_tasks:
            result = await agent_worker.think(
                agent_id=agent_id,
                kami_id=kami_id,
                tick=tick,
                recent_personal_events=recent,
            )
            all_intents[kami_id].extend(result.get("intents", []))
            all_monologues[agent_id] = result.get("inner_monologue", "")

            # Apply belief updates
            for belief in result.get("beliefs", []):
                try:
                    fs.update_belief(
                        session,
                        agent_id=belief["agent_id"],
                        kind=belief["kind"],
                        tick=tick,
                        target_entity=belief.get("target_entity"),
                        attribute=belief.get("attribute"),
                        believed_value=belief.get("believed_value"),
                        confidence=belief.get("confidence", 0.8),
                    )
                except Exception as e:
                    logger.warning(f"Belief update failed: {e}")

        # === COMPUTE PHASE 2: Kami resolution (parallel) ===
        kami_worker = KamiWorker(session, self.event_bus, self.spatial_graph)
        proposals = []

        for kami_id in active_kami:
            intents = order_intents_by_initiative(all_intents.get(kami_id, []), tick)
            result = await kami_worker.render_tick(kami_id, tick, intents)
            result["kami_id"] = kami_id
            proposals.append(result)

        # === WRITE PHASE ===
        committed_events = commit_proposals(
            session, tick, proposals, self.event_bus, self.spatial_graph,
        )

        # === PROPAGATE PHASE ===
        # Events are already propagated via EventBus in commit_proposals
        # Additional propagation for high-salience events
        for event in committed_events:
            if event["salience"] >= config.kami_wake_salience_threshold:
                kami_id = event.get("kami_id")
                if kami_id:
                    neighbors = self.spatial_graph.get_neighbors(kami_id)
                    for neighbor in neighbors:
                        edge = self.spatial_graph.get_edge_data(kami_id, neighbor)
                        att = edge.get("audio_attenuation", 0.2) if edge else 0.2
                        effective_salience = event["salience"] * (1.0 - att)
                        if effective_salience > config.kami_wake_salience_threshold:
                            self.event_bus.propagate_event(
                                source_event_id=event["event_id"],
                                source_kami_id=kami_id,
                                target_kami_id=neighbor,
                                event_type=event["event_type"],
                                narrative_digest=event["narrative"][:100],
                                salience=effective_salience,
                                current_tick=tick,
                            )

        # Build tick result
        narratives = {}
        for p in proposals:
            narratives[p["kami_id"]] = p.get("narrative", "")

        return {
            "tick": tick,
            "active_kami_count": len(active_kami),
            "active_agent_count": total_agents,
            "active_kami": list(active_kami),
            "events": committed_events,
            "narratives": narratives,
            "monologues": all_monologues,
        }
