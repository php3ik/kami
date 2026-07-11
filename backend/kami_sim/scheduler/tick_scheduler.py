"""TickScheduler — BSP-style coordination (spec §2.5).

Two-phase tick model: READ -> COMPUTE -> WRITE -> PROPAGATE.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..comms.channels import expire_ringing_calls, get_forced_wake_agents
from ..comms.inbox import process_read
from ..agent_worker.worker import AgentCognitionWorker
from ..config import config
from ..determinism import tick_metadata, tick_scope
from ..eventbus.bus import EventBus
from ..factstore import tools as fs
from ..factstore.models import Simulation, SimulationTick
from ..kami_worker.worker import KamiWorker
from ..llm.budget import budget
from ..memory import memory_runtime
from ..spatial.graph import SpatialGraph
from ..spatial.transit import advance_transit
from .activity_detector import detect_active_agents, detect_active_kami
from .conflict_resolver import order_intents_by_initiative
from .write_committer import publish_staged_broadcasts, stage_proposals
from .time_step import select_next_tick

logger = logging.getLogger(__name__)


class TickScheduler:
    """Coordinates parallel rendering of kami and agents per tick."""

    def __init__(
        self,
        session_factory,
        spatial_graph: SpatialGraph,
        event_bus: EventBus | None = None,
        simulation_id: str | None = None,
    ):
        self.session_factory = session_factory
        self.spatial_graph = spatial_graph
        self.event_bus = event_bus or EventBus()
        self.simulation_id = simulation_id
        self.current_tick = 0
        self.last_time_mode = "dense"
        self.last_skipped_ticks = 0
        self.tick_log: list[dict] = []

    async def run(self, num_ticks: int, start_tick: int | None = None, progress_callback=None) -> list[dict]:
        """Run the simulation for num_ticks ticks."""
        if start_tick is not None:
            self.current_tick = start_tick
        logger.info(f"Starting simulation: {num_ticks} ticks from tick {self.current_tick}")
        run_results: list[dict] = []

        for i in range(num_ticks):
            requested_tick = self.current_tick
            tick = requested_tick
            tick_cost_before = 0.0
            cost_tracking_started = False
            tick_start = time.time()
            tick_succeeded = False

            session = self.session_factory()
            try:
                tick = (
                    select_next_tick(
                        session,
                        self.event_bus,
                        requested_tick,
                        self.spatial_graph.all_kami_ids(),
                        self._simulation_scope(),
                    )
                    if isinstance(session, Session)
                    else requested_tick
                )
                skipped_ticks = max(0, tick - requested_tick)
                time_context = {
                    "time_mode": "sparse" if skipped_ticks else "dense",
                    "jumped_from_tick": requested_tick if skipped_ticks else None,
                    "skipped_ticks": skipped_ticks,
                    "elapsed_ticks": skipped_ticks + 1,
                    "next_tick": tick + 1,
                }
                tick_cost_before = budget.get_tick_cost(tick, self.simulation_id)
                cost_tracking_started = True
                with budget.scope(self.simulation_id), tick_scope(
                    self._simulation_scope(), tick
                ):
                    if skipped_ticks:
                        tick_result = await self._run_tick(
                            session,
                            tick,
                            progress_callback,
                            time_context=time_context,
                        )
                    else:
                        tick_result = await self._run_tick(
                            session, tick, progress_callback
                        )
                tick_result["wall_time_ms"] = int((time.time() - tick_start) * 1000)
                tick_result["tick_cost_usd"] = round(
                    budget.get_tick_cost(tick, self.simulation_id) - tick_cost_before,
                    6,
                )
                self.tick_log.append(tick_result)
                run_results.append(tick_result)
                self.last_time_mode = tick_result.get("time_mode", "dense")
                self.last_skipped_ticks = int(tick_result.get("skipped_ticks", 0))
                tick_succeeded = True

                if (i + 1) % 10 == 0 or i == 0:
                    logger.info(
                        f"Tick {tick}: {tick_result['active_kami_count']} active kami, "
                        f"{tick_result['active_agent_count']} agents, "
                        f"${tick_result['tick_cost_usd']:.4f}, "
                        f"{tick_result['wall_time_ms']}ms"
                    )
            except Exception as e:
                session.rollback()
                logger.error(f"Tick {tick} failed: {e}", exc_info=True)
                tick_result = {
                    "tick": tick,
                    "error": str(e),
                    "active_kami_count": 0,
                    "active_agent_count": 0,
                    "tick_cost_usd": (
                        round(
                            budget.get_tick_cost(tick, self.simulation_id)
                            - tick_cost_before,
                            6,
                        )
                        if cost_tracking_started
                        else 0.0
                    ),
                    "events": [],
                    "narratives": {},
                    "failed_mutations": [],
                    "time_mode": "sparse" if tick > requested_tick else "dense",
                    "jumped_from_tick": requested_tick if tick > requested_tick else None,
                    "skipped_ticks": max(0, tick - requested_tick),
                    "elapsed_ticks": max(0, tick - requested_tick) + 1,
                    "next_tick": tick + 1,
                    "determinism": tick_metadata(self._simulation_scope(), tick),
                    "fallbacks": {"agents": 0, "kami": 0, "total": 0},
                }
                self._record_tick_failure(tick, tick_result)
                self.tick_log.append(tick_result)
                run_results.append(tick_result)
            finally:
                session.close()

            if not tick_succeeded:
                break

            self.current_tick = tick + 1

            # Cleanup old event bus data
            self.event_bus.cleanup_before(tick)
            self.event_bus.cleanup_tick(tick)

        return run_results

    async def _run_tick(
        self,
        session: Session,
        tick: int,
        progress_callback=None,
        time_context: dict | None = None,
    ) -> dict:
        """Execute one complete BSP tick."""
        replay = self._committed_tick_result(session, tick)
        if replay is not None:
            return {**replay, "idempotent_replay": True}

        all_kami = self.spatial_graph.all_kami_ids()

        expire_ringing_calls(session, self._simulation_scope(), tick)
        transit_events, transit_transitions = advance_transit(
            session, self._simulation_scope(), tick
        )

        # === READ PHASE ===
        active_kami = sorted(
            detect_active_kami(
                session,
                self.event_bus,
                tick,
                all_kami,
                simulation_id=self._simulation_scope(),
            )
        )
        forced_agents = get_forced_wake_agents(
            session, self._simulation_scope(), tick
        )
        agents_by_kami = detect_active_agents(
            session, active_kami, forced_agent_ids=forced_agents
        )

        total_agents = sum(len(agents) for agents in agents_by_kami.values())

        if not active_kami:
            result = self._with_time_context({
                "tick": tick,
                "active_kami_count": 0,
                "active_agent_count": 0,
                "events": transit_events,
                "narratives": {},
                "failed_mutations": [],
                "transit": transit_transitions,
                "determinism": tick_metadata(self._simulation_scope(), tick),
                "fallbacks": {"agents": 0, "kami": 0, "total": 0},
            }, time_context)
            staged_memories = memory_runtime.stage_events(
                session, self._simulation_scope(), transit_events
            )
            self._commit_tick(session, tick, result)
            memory_runtime.index_committed(staged_memories)
            await self._consolidate_memory(tick)
            return result

        # === COMPUTE PHASE 1: Agent cognition (parallel) ===
        agent_worker = AgentCognitionWorker(session, spatial_graph=self.spatial_graph)
        all_intents: dict[str, list[dict]] = {k: [] for k in active_kami}
        all_monologues: dict[str, str] = {}

        agent_tasks = []
        for kami_id in sorted(agents_by_kami):
            for agent_id in sorted(agents_by_kami[kami_id]):
                # Only include events the agent participated in or was present to observe.
                recent = fs.get_agent_observed_events(
                    session,
                    agent_id=agent_id,
                    since_tick=max(0, tick - 5),
                    until_tick=tick - 1,
                    limit=5,
                )
                recent_dicts = [
                    {
                        "event_id": e.event_id,
                        "tick": e.tick,
                        "narrative": e.narrative,
                        "event_type": e.event_type,
                        "source": "participated" if agent_id in (e.participants or []) else "witnessed",
                    }
                    for e in recent
                ]
                agent_tasks.append(
                    (kami_id, agent_id, recent_dicts)
                )

        # Run agent cognition calls (parallel)
        agent_coros = []
        for kami_id, agent_id, recent in agent_tasks:
            async def think_task(k_id, a_id, r):
                await self._report_progress(progress_callback, {
                    "step": "agent_think_start",
                    "agent_id": a_id,
                    "kami_id": k_id,
                })
                try:
                    res = await agent_worker.think(
                        agent_id=a_id,
                        kami_id=k_id,
                        tick=tick,
                        recent_personal_events=r,
                    )
                except Exception:
                    logger.exception(
                        "Agent worker crashed for %s at tick %s; using fallback",
                        a_id,
                        tick,
                    )
                    res = agent_worker.fallback(a_id, reason="worker_error")
                await self._report_progress(progress_callback, {
                    "step": "agent_think_end",
                    "agent_id": a_id,
                    "kami_id": k_id,
                    "inner_monologue": res.get("inner_monologue", ""),
                    "fallback": bool(res.get("fallback")),
                })
                return k_id, a_id, res
            agent_coros.append(think_task(kami_id, agent_id, recent))

        agent_results = await asyncio.gather(*agent_coros)

        for kami_id, agent_id, result in agent_results:
            enriched_intents = []
            fs.advance_agent_needs(session, agent_id, tick)
            for intent in result.get("intents", []):
                try:
                    intent["target"] = self._resolve_intent_target(
                        session,
                        kami_id=kami_id,
                        raw_target=intent.get("target", ""),
                    )
                    pressure = {
                        "goal": intent.get("goal", ""),
                        "expected_outcome": intent.get("expected_outcome", ""),
                        "exit_condition": intent.get("exit_condition", ""),
                    }
                    record = fs.record_agent_intent(
                        session,
                        tick=tick,
                        agent_id=agent_id,
                        kami_id=kami_id,
                        action_type=intent.get("action_type", "wait"),
                        target=intent.get("target", ""),
                        params=intent.get("params", {}),
                        salience=intent.get("salience", 0.3),
                        pressure=pressure,
                    )
                    intent["intent_id"] = record.intent_id
                except Exception as e:
                    logger.warning(f"Intent record failed: {e}")
                enriched_intents.append(intent)
            all_intents[kami_id].extend(enriched_intents)
            all_monologues[agent_id] = result.get("inner_monologue", "")

            process_read(
                session,
                agent_id,
                result.get("processed_message_ids", []),
                tick,
            )

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
        kami_coros = []

        for kami_id in active_kami:
            async def render_task(k_id):
                await self._report_progress(progress_callback, {
                    "step": "kami_render_start", "kami_id": k_id
                })
                ints = order_intents_by_initiative(all_intents.get(k_id, []), tick)
                try:
                    res = await kami_worker.render_tick(k_id, tick, ints)
                except Exception:
                    logger.exception(
                        "Kami worker crashed for %s at tick %s; using fallback",
                        k_id,
                        tick,
                    )
                    res = kami_worker.fallback(
                        k_id, tick, ints, reason="worker_error"
                    )
                await self._report_progress(progress_callback, {
                    "step": "kami_render_end",
                    "kami_id": k_id,
                    "narrative": res.get("narrative", ""),
                    "fallback": bool(res.get("fallback")),
                })
                res["kami_id"] = k_id
                return res
            kami_coros.append(render_task(kami_id))

        proposals = await asyncio.gather(*kami_coros)

        # === WRITE PHASE ===
        staged = stage_proposals(
            session,
            tick,
            proposals,
            self.spatial_graph,
            active_kami_ids=set(active_kami),
        )
        committed_events = [*transit_events, *staged.events]
        staged_memories = memory_runtime.stage_events(
            session, self._simulation_scope(), committed_events
        )

        # Build tick result
        narratives = {}
        for p in proposals:
            narratives[p["kami_id"]] = p.get("narrative", "")
        agent_fallbacks = sum(
            1 for _, _, worker_result in agent_results
            if worker_result.get("fallback")
        )
        kami_fallbacks = sum(1 for proposal in proposals if proposal.get("fallback"))

        result = self._with_time_context({
            "tick": tick,
            "active_kami_count": len(active_kami),
            "active_agent_count": total_agents,
            "active_kami": list(active_kami),
            "events": committed_events,
            "failed_mutations": staged.failed_mutations,
            "narratives": narratives,
            "monologues": all_monologues,
            "transit": transit_transitions,
            "determinism": tick_metadata(self._simulation_scope(), tick),
            "fallbacks": {
                "agents": agent_fallbacks,
                "kami": kami_fallbacks,
                "total": agent_fallbacks + kami_fallbacks,
            },
        }, time_context)
        self._commit_tick(session, tick, result)
        await self._finalize_committed_narratives(
            session,
            kami_worker,
            proposals,
            staged,
            result,
            tick,
            progress_callback,
        )
        memory_runtime.index_committed(staged_memories)

        # === PROPAGATE PHASE ===
        # These notifications are ephemeral. Canonical state is already durable,
        # so a listener failure must not cause the same tick to execute twice.
        try:
            publish_staged_broadcasts(
                staged, tick, self.event_bus, self.spatial_graph
            )
        except Exception:
            logger.exception("Post-commit broadcasts failed for tick %s", tick)
        try:
            self._propagate_committed_events(staged.events, tick)
        except Exception:
            logger.exception("Post-commit event propagation failed for tick %s", tick)
        await self._consolidate_memory(tick)
        return result

    async def _finalize_committed_narratives(
        self,
        session: Session,
        kami_worker,
        proposals: list[dict],
        staged,
        result: dict,
        tick: int,
        progress_callback=None,
    ) -> None:
        """Render prose after canonical state commits; factual summaries remain fallback."""
        renderer = getattr(kami_worker, "render_committed_narrative", None)
        if not callable(renderer) or not staged.events:
            return

        accepted_ids = {id(proposal) for proposal, _ in staged.accepted}

        async def render_one(proposal: dict):
            if id(proposal) not in accepted_ids:
                return proposal.get("kami_id"), "", []
            kami_id = proposal.get("kami_id")
            events = [
                event for event in staged.events if event.get("kami_id") == kami_id
            ]
            if not kami_id or not events:
                return kami_id, "", events
            try:
                narrative = await renderer(kami_id, tick, proposal, events)
            except Exception:
                logger.exception(
                    "Post-commit narrative renderer crashed for %s tick %s",
                    kami_id,
                    tick,
                )
                narrative = ""
            return kami_id, str(narrative or "").strip(), events

        rendered = await asyncio.gather(*(render_one(proposal) for proposal in proposals))
        updates: dict[str, str] = {}
        narratives = dict(result.get("narratives") or {})
        for kami_id, narrative, events in rendered:
            if not narrative or not events:
                continue
            narratives[kami_id] = narrative
            for event in events:
                if (
                    event.get("event_id")
                    and str(event.get("narrative") or "").strip() != narrative
                ):
                    updates[event["event_id"]] = narrative

        if not updates:
            return

        final_events = []
        for event in result.get("events", []):
            updated = dict(event)
            if event.get("event_id") in updates:
                updated["narrative"] = updates[event["event_id"]]
            final_events.append(updated)
        final_result = {**result, "events": final_events, "narratives": narratives}

        try:
            for event_id, narrative in updates.items():
                row = session.get(fs.Event, event_id)
                if row is not None:
                    row.narrative = narrative
            tick_record = (
                session.query(SimulationTick)
                .filter(
                    SimulationTick.simulation_id == self._simulation_scope(),
                    SimulationTick.tick == tick,
                    SimulationTick.status == "committed",
                )
                .one_or_none()
            )
            if tick_record is not None:
                tick_record.result = final_result
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "Could not persist post-commit narratives for tick %s; factual summaries remain canonical",
                tick,
            )
            return

        result.clear()
        result.update(final_result)
        for event in staged.events:
            if event.get("event_id") in updates:
                event["narrative"] = updates[event["event_id"]]
        await self._report_progress(progress_callback, {
            "step": "narrative_finalize_end",
            "tick": tick,
            "kami_count": len({kami_id for kami_id, _, _ in rendered if kami_id}),
        })

    @staticmethod
    async def _report_progress(callback, data: dict) -> None:
        if callback is None:
            return
        try:
            await callback({"type": "progress", "data": data})
        except Exception:
            logger.exception("Tick progress callback failed at step %s", data.get("step"))

    async def _consolidate_memory(self, tick: int) -> None:
        try:
            await memory_runtime.consolidate_if_due(
                self._simulation_scope(), tick
            )
        except Exception:
            logger.exception("Post-commit memory consolidation failed for tick %s", tick)

    def _simulation_scope(self) -> str:
        return self.simulation_id or "default"

    @staticmethod
    def _with_time_context(result: dict, time_context: dict | None) -> dict:
        payload = {
            **result,
            "time_mode": "dense",
            "jumped_from_tick": None,
            "skipped_ticks": 0,
            "elapsed_ticks": 1,
            "next_tick": int(result["tick"]) + 1,
            **(time_context or {}),
        }
        payload["sim_time_minutes"] = (
            int(payload["next_tick"]) * config.tick_in_sim_minutes
        )
        return payload

    def _committed_tick_result(self, session: Session, tick: int) -> dict | None:
        record = (
            session.query(SimulationTick)
            .filter(
                SimulationTick.simulation_id == self._simulation_scope(),
                SimulationTick.tick == tick,
                SimulationTick.status == "committed",
            )
            .one_or_none()
        )
        return dict(record.result or {}) if record is not None else None

    def _commit_tick(self, session: Session, tick: int, result: dict) -> None:
        scope = self._simulation_scope()
        now = datetime.now(UTC).replace(tzinfo=None)
        record = (
            session.query(SimulationTick)
            .filter(
                SimulationTick.simulation_id == scope,
                SimulationTick.tick == tick,
            )
            .one_or_none()
        )
        if record is None:
            record = SimulationTick(
                simulation_id=scope,
                tick=tick,
                attempt_count=1,
                started_at=now,
            )
            session.add(record)
        else:
            record.attempt_count = int(record.attempt_count or 0) + 1
            record.started_at = now
        record.status = "committed"
        record.result = result
        record.error_message = None
        record.completed_at = now

        simulation = session.get(Simulation, scope)
        if simulation is not None:
            simulation.current_tick = max(
                int(simulation.current_tick or 0), tick + 1
            )
            simulation.updated_at = now
        session.commit()

    def _record_tick_failure(self, tick: int, result: dict) -> None:
        session = self.session_factory()
        try:
            scope = self._simulation_scope()
            now = datetime.now(UTC).replace(tzinfo=None)
            record = (
                session.query(SimulationTick)
                .filter(
                    SimulationTick.simulation_id == scope,
                    SimulationTick.tick == tick,
                )
                .one_or_none()
            )
            if record is not None and record.status == "committed":
                return
            if record is None:
                record = SimulationTick(
                    simulation_id=scope,
                    tick=tick,
                    attempt_count=1,
                    started_at=now,
                )
                session.add(record)
            else:
                record.attempt_count = int(record.attempt_count or 0) + 1
            record.status = "failed"
            record.result = result
            record.error_message = str(result.get("error", ""))[:2000]
            record.completed_at = now
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Could not persist failure record for tick %s", tick)
        finally:
            session.close()

    def _propagate_committed_events(self, events: list[dict], tick: int) -> None:
        for event in events:
            if event["salience"] < config.kami_wake_salience_threshold:
                continue
            kami_id = event.get("kami_id")
            if not kami_id:
                continue
            for neighbor in self.spatial_graph.get_neighbors(kami_id):
                edge = self.spatial_graph.get_edge_data(kami_id, neighbor)
                attenuation = edge.get("audio_attenuation", 0.2) if edge else 0.2
                effective_salience = event["salience"] * (1.0 - attenuation)
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

    def _resolve_intent_target(
        self,
        session: Session,
        kami_id: str,
        raw_target: str | None,
    ) -> str:
        """Normalize obvious target names to canonical entity/kami IDs.

        Agents are instructed to use IDs, but LLMs occasionally return a visible
        name. We accept exact visible names while leaving ambiguous descriptions
        for the kami guardrails to block.
        """
        target = (raw_target or "").strip()
        if not target:
            return ""
        target_entity = session.get(fs.Entity, target)
        simulation_id = fs.resolve_simulation_id(session, kami_id)
        if (
            target_entity is not None
            and target_entity.simulation_id == simulation_id
        ):
            return target
        if target in self.spatial_graph.get_neighbors(kami_id):
            return target

        target_key = target.casefold()
        visible = fs.get_entities_in_kami(session, kami_id)
        matches = [
            entity.entity_id
            for entity in visible
            if entity.canonical_name.casefold() == target_key
        ]
        if len(matches) == 1:
            return matches[0]

        neighbors = self.spatial_graph.get_neighbors(kami_id)
        neighbor_matches = []
        for neighbor_id in neighbors:
            entity = session.get(fs.Entity, neighbor_id)
            if entity and entity.canonical_name.casefold() == target_key:
                neighbor_matches.append(neighbor_id)
        if len(neighbor_matches) == 1:
            return neighbor_matches[0]
        return target
