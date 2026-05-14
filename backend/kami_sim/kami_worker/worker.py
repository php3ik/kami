"""KamiWorker — scene rendering as game master (spec §2.3).

For each active kami on each tick: collect context, call LLM, parse tool calls
into FactStore mutations and emitted events.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..config import config
from ..eventbus.bus import EventBus
from ..factstore import tools as fs
from ..llm.client import llm_client
from ..spatial.graph import SpatialGraph
from .prompt_builder import KAMI_TOOLS, build_kami_prompt
from .scene_dynamics import analyze_scene_dynamics, apply_scene_guardrails

logger = logging.getLogger(__name__)


class KamiWorker:
    """Renders a single kami tick."""

    def __init__(
        self,
        session: Session,
        event_bus: EventBus,
        spatial_graph: SpatialGraph,
        quota_tracker: dict | None = None,
    ):
        self.session = session
        self.event_bus = event_bus
        self.spatial_graph = spatial_graph
        self.quota_tracker = quota_tracker or {}

    async def render_tick(
        self,
        kami_id: str,
        tick: int,
        agent_intents: list[dict],
    ) -> dict:
        """Render one tick for one kami. Returns propose-list of mutations."""
        kami_entity = self.session.get(fs.Entity, kami_id)
        if not kami_entity:
            logger.error(f"Kami {kami_id} not found")
            return {"events": [], "mutations": [], "narrative": ""}

        # Determine model tier
        agents = fs.get_agents_in_kami(self.session, kami_id)
        tier = self._select_tier(agents, agent_intents)
        recent_events = fs.get_events(
            self.session, kami_id=kami_id, since_tick=max(0, tick - 8), limit=12
        )
        active_threads = fs.get_active_conversations(
            self.session, kami_id=kami_id, limit=5
        )
        scene_dynamics = analyze_scene_dynamics(
            self.session,
            kami_id=kami_id,
            tick=tick,
            agent_intents=agent_intents,
            recent_events=recent_events,
            active_threads=active_threads,
            spatial_graph=self.spatial_graph,
        )

        # Build prompt
        system_blocks, messages = build_kami_prompt(
            self.session, kami_id, kami_entity, tick,
            agent_intents, self.event_bus, self.spatial_graph,
            scene_dynamics=scene_dynamics,
        )

        # Call LLM
        try:
            response = await llm_client.call(
                messages=messages,
                system=system_blocks,
                tier=tier,
                component="KamiWorker",
                tick=tick,
                tools=KAMI_TOOLS,
                max_tokens=1500,
                temperature=0.7,
            )
        except Exception as e:
            logger.error(f"LLM call failed for kami {kami_id} tick {tick}: {e}")
            result = self._fallback_result(kami_id, tick, agent_intents)
            return apply_scene_guardrails(
                result, scene_dynamics, kami_id, tick, agent_intents
            )

        # Parse tool calls into propose-list
        result = self._parse_response(response, kami_id, tick, agent_intents)
        return apply_scene_guardrails(
            result, scene_dynamics, kami_id, tick, agent_intents
        )

    def _select_tier(self, agents: list, intents: list[dict]) -> str:
        """Route to cheap or strong model based on scene complexity."""
        if len(agents) > config.kami_strong_model_threshold_agents:
            max_salience = max(
                (i.get("salience", 0.5) for i in intents), default=0.0
            )
            if max_salience > config.kami_strong_model_threshold_salience:
                return "strong"
        return "cheap"

    def _parse_response(
        self, response: dict, kami_id: str, tick: int, agent_intents: list[dict] | None = None
    ) -> dict:
        """Parse LLM response into structured propose-list."""
        mutations = []
        events = []
        narrative = response.get("content", "")
        broadcasts = []

        for tc in response.get("tool_calls", []):
            name = tc["name"]
            inp = tc["input"]

            if name == "emit_event":
                narrative_text = self._clean_narrative(inp.get("narrative", ""))
                events.append({
                    "kami_id": kami_id,
                    "tick": tick,
                    "event_type": inp.get("event_type", "idle"),
                    "participants": self._normalize_participants(inp.get("participants", [])),
                    "narrative": narrative_text,
                    "salience": inp.get("salience", 0.3),
                    "payload": inp.get("payload", {}),
                })
            elif name == "move_entity":
                mutations.append({
                    "type": "move_entity",
                    "entity_id": inp["entity_id"],
                    "to_kami_id": inp["to_kami_id"],
                    "reason": inp.get("reason", ""),
                })
            elif name == "change_state":
                mutations.append({
                    "type": "change_state",
                    "entity_id": inp["entity_id"],
                    "attribute": inp["attribute"],
                    "new_value": inp["new_value"],
                })
            elif name == "update_relation":
                mutations.append({
                    "type": "update_relation",
                    "from_entity": inp["from_entity"],
                    "to_entity": inp["to_entity"],
                    "rel_type": inp["rel_type"],
                    "weight": inp.get("weight", {}),
                })
            elif name == "create_entity":
                mutations.append({
                    "type": "create_entity",
                    "kind": inp["kind"],
                    "canonical_name": inp["canonical_name"],
                    "archetype": inp.get("archetype", {}),
                    "kami_id": kami_id,
                })
            elif name == "publish_broadcast":
                broadcasts.append({
                    "text": inp["text"],
                    "salience": inp.get("salience", 0.3),
                })
            elif name == "update_conversation_thread":
                mutations.append({
                    "type": "update_conversation_thread",
                    "kami_id": kami_id,
                    "thread_id": inp.get("thread_id"),
                    "participants": inp.get("participants", []),
                    "topic": inp.get("topic", "unfinished exchange"),
                    "status": inp.get("status", "active"),
                    "summary": inp.get("summary", ""),
                    "tension": inp.get("tension", 0.0),
                    "momentum": inp.get("momentum", 0.5),
                    "open_question": inp.get("open_question"),
                })
            elif name == "record_intent_result":
                mutations.append({
                    "type": "record_intent_result",
                    "intent_id": inp["intent_id"],
                    "status": inp.get("status", "resolved"),
                    "summary": inp.get("summary", ""),
                    "blockers": inp.get("blockers", []),
                })
            elif name == "adjust_need":
                mutation = {
                    "type": "adjust_need",
                    "agent_id": inp["agent_id"],
                    "need": inp["need"],
                    "reason": inp.get("reason", ""),
                }
                if "value" in inp:
                    mutation["value"] = inp["value"]
                if "delta" in inp:
                    mutation["delta"] = inp["delta"]
                mutations.append(mutation)

        # If no emit_event was called, add a fallback idle event
        if not events:
            return self._deterministic_resolution(kami_id, tick, agent_intents or [], narrative)

        return {
            "events": events,
            "mutations": mutations,
            "broadcasts": broadcasts,
            "narrative": narrative,
        }

    def _clean_narrative(self, narrative: str) -> str:
        text = (narrative or "").strip()
        lowered = text.lower()
        blocked = [
            "i will resolve",
            "i'll resolve",
            "as a kami",
            "as the kami",
            "the simulation",
            "the model",
            "tool call",
            "agent intent",
        ]
        if any(marker in lowered for marker in blocked):
            return "The moment tightens into a concrete beat, and the people present respond to what is directly in front of them."
        return text

    def _normalize_participants(self, participants: list) -> list[str]:
        if not participants:
            return []
        entities = self.session.query(fs.Entity).filter(fs.Entity.kind == "agent").all()
        by_name = {e.canonical_name.lower(): e.entity_id for e in entities}
        by_id = {e.entity_id for e in entities}
        normalized = []
        for participant in participants:
            raw = str(participant)
            if raw in by_id:
                normalized.append(raw)
            elif raw.lower() in by_name:
                normalized.append(by_name[raw.lower()])
        return list(dict.fromkeys(normalized))

    def _fallback_result(
        self, kami_id: str, tick: int, agent_intents: list[dict] | None = None
    ) -> dict:
        return self._deterministic_resolution(kami_id, tick, agent_intents or [], "")

    def _deterministic_resolution(
        self, kami_id: str, tick: int, agent_intents: list[dict], fallback_text: str
    ) -> dict:
        if not agent_intents:
            return {
                "events": [{
                    "kami_id": kami_id,
                    "tick": tick,
                    "event_type": "idle",
                    "participants": [],
                    "narrative": fallback_text or "The station hums through an uneventful minute.",
                    "salience": 0.1,
                    "payload": {},
                }],
                "mutations": [],
                "broadcasts": [],
                "narrative": fallback_text or "The station hums through an uneventful minute.",
            }

        mutations = []
        participants = []
        beats = []
        max_salience = 0.2
        neighbors = set(self.spatial_graph.get_neighbors(kami_id))
        for intent in agent_intents:
            agent_id = intent.get("agent_id")
            if agent_id:
                participants.append(agent_id)
            agent_name = intent.get("agent_name") or agent_id or "Someone"
            action = intent.get("action_type", "wait")
            target = intent.get("target") or ""
            utterance = intent.get("utterance") or intent.get("params", {}).get("speech") or ""
            max_salience = max(max_salience, float(intent.get("salience", 0.3)))

            if action == "move" and target in neighbors:
                mutations.append({
                    "type": "move_entity",
                    "entity_id": agent_id,
                    "to_kami_id": target,
                    "reason": "agent intent fallback resolution",
                })
                beats.append(f"{agent_name} pushes off toward {target}, turning intention into motion.")
            elif action == "talk":
                line = f' and says, "{utterance}"' if utterance else ""
                beats.append(f"{agent_name} opens a concrete exchange{line}.")
                if target:
                    participants.append(target)
                mutations.append({
                    "type": "update_conversation_thread",
                    "kami_id": kami_id,
                    "participants": [p for p in [agent_id, target] if p],
                    "topic": intent.get("goal") or "unfinished crew exchange",
                    "summary": utterance or f"{agent_name} tries to pull the conversation forward.",
                    "status": "active",
                    "tension": 0.45,
                    "momentum": 0.55,
                    "open_question": intent.get("expected_outcome") or None,
                })
            elif action in {"work", "use_object"}:
                beats.append(f"{agent_name} starts hands-on work" + (f" on {target}" if target else "") + ".")
                mutations.append({
                    "type": "adjust_need",
                    "agent_id": agent_id,
                    "need": "task_pressure",
                    "delta": -0.03,
                })
            elif action == "observe":
                beats.append(f"{agent_name} studies the scene" + (f" around {target}" if target else "") + ", looking for a useful next move.")
            else:
                beats.append(f"{agent_name} chooses to {action}" + (f" around {target}" if target else "") + ".")

            if intent.get("intent_id"):
                mutations.append({
                    "type": "record_intent_result",
                    "intent_id": intent["intent_id"],
                    "status": "partial",
                    "summary": beats[-1],
                })

        narrative = " ".join(beats[:4])
        if len(beats) > 4:
            narrative += " The rest of the room shifts around those choices."
        return {
            "events": [{
                "kami_id": kami_id,
                "tick": tick,
                "event_type": "action",
                "participants": list(dict.fromkeys(participants)),
                "narrative": narrative,
                "salience": min(0.85, max_salience),
                "payload": {"fallback_resolution": True},
            }],
            "mutations": mutations,
            "broadcasts": [],
            "narrative": narrative,
        }
