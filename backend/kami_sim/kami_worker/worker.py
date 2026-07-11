"""KamiWorker — scene rendering as game master (spec §2.3).

For each active kami on each tick: collect context, call LLM, parse tool calls
into FactStore mutations and emitted events.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..config import config
from ..eventbus.bus import EventBus
from ..factstore import tools as fs
from ..llm.client import llm_client
from ..language import (
    choose,
    get_simulation_language,
    language_instruction,
    message,
    structured_text_matches_language,
    text_matches_language,
)
from ..spatial.graph import SpatialGraph
from .prompt_builder import KAMI_TOOLS, build_kami_prompt
from .scene_dynamics import analyze_scene_dynamics, apply_scene_guardrails

logger = logging.getLogger(__name__)


NARRATIVE_TOOLS = [{
    "name": "submit_narrative",
    "description": "Render the committed factual scene without adding new events or dialogue.",
    "input_schema": {
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": "Two to four grounded diegetic sentences based only on the committed diff",
            },
        },
        "required": ["narrative"],
    },
}]


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
        if not agent_intents and not self._has_resolution_pressure(
            kami_id,
            tick,
            kami_entity.simulation_id,
        ):
            result = self._deterministic_resolution(kami_id, tick, [], "")
            result["fallback"] = False
            result["deterministic_idle"] = True
            result["resolution_plan"] = scene_dynamics.to_resolution_plan()
            return result

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
            reason = "timeout" if isinstance(e, TimeoutError) else "llm_error"
            result = self._fallback_result(kami_id, tick, agent_intents, reason)
            self._append_comms_intents(result, agent_intents)
            result = apply_scene_guardrails(
                result, scene_dynamics, kami_id, tick, agent_intents
            )
            result["resolution_plan"] = scene_dynamics.to_resolution_plan()
            return result

        # Parse tool calls into propose-list
        result = self._parse_response(response, kami_id, tick, agent_intents)
        content_language = get_simulation_language(
            self.session, kami_entity.simulation_id
        )
        language_payload = {
            "events": [event.get("narrative", "") for event in result.get("events", [])],
            "mutations": [
                {
                    key: mutation.get(key)
                    for key in ("summary", "topic", "reason", "fact", "text", "content")
                }
                for mutation in result.get("mutations", [])
            ],
        }
        if not structured_text_matches_language(language_payload, content_language):
            logger.warning(
                "Kami %s violated simulation language %s", kami_id, content_language
            )
            result = self._fallback_result(
                kami_id, tick, agent_intents, reason="language_mismatch"
            )
        else:
            result["fallback"] = False
        self._append_comms_intents(result, agent_intents)
        result = apply_scene_guardrails(
            result, scene_dynamics, kami_id, tick, agent_intents
        )
        result["resolution_plan"] = scene_dynamics.to_resolution_plan()
        return result

    async def render_committed_narrative(
        self,
        kami_id: str,
        tick: int,
        proposal: dict,
        committed_events: list[dict],
    ) -> str:
        """Render prose from a committed scene; never participate in resolution."""
        factual = " ".join(
            str(event.get("narrative") or "").strip()
            for event in committed_events
            if str(event.get("narrative") or "").strip()
        ).strip()
        if not committed_events or all(
            event.get("event_type") in {"idle", "background"}
            or float(event.get("salience", 0.0)) <= 0.15
            for event in committed_events
        ):
            return factual

        state = fs.query_kami_state(self.session, kami_id)
        entities = {
            entity["entity_id"]: entity["name"]
            for entity in state.get("entities", [])
        }
        participant_ids = {
            participant
            for event in committed_events
            for participant in event.get("participants", [])
        }
        for participant_id in participant_ids - entities.keys():
            participant = self.session.get(fs.Entity, participant_id)
            if participant is not None:
                entities[participant_id] = participant.canonical_name
        event_facts = [
            {
                "event_id": event.get("event_id"),
                "event_type": event.get("event_type"),
                "participants": [
                    {"id": participant, "name": entities.get(participant, participant)}
                    for participant in event.get("participants", [])
                ],
                "factual_summary": event.get("narrative", ""),
                "salience": event.get("salience", 0.3),
                "causes": event.get("causes", []),
            }
            for event in committed_events
        ]
        final_state = [
            {
                "id": entity["entity_id"],
                "name": entity["name"],
                "kind": entity["kind"],
                "states": entity.get("states") or {},
            }
            for entity in state.get("entities", [])
        ]
        committed_diff = [
            _narrative_safe_mutation(mutation)
            for mutation in proposal.get("mutations", [])
        ]
        prompt = (
            f"{language_instruction(get_simulation_language(self.session, fs.resolve_simulation_id(self.session, kami_id)))}\n\n"
            "Render this already-committed local scene in 2-4 concise diegetic sentences. "
            "Do not invent actions, dialogue, thoughts, identities, objects, outcomes, or state "
            "changes. Preserve uncertainty in the factual summary. Use names only from the "
            "participant mapping. Return the submit_narrative tool.\n\n"
            f"Tick: {tick}\nKami: {kami_id}\n"
            f"Committed events: {json.dumps(event_facts, ensure_ascii=False)}\n"
            f"Committed diff: {json.dumps(committed_diff, ensure_ascii=False)}\n"
            f"Final local state: {json.dumps(final_state, ensure_ascii=False)}"
        )
        try:
            response = await llm_client.call(
                messages=[{"role": "user", "content": prompt}],
                system=(
                    "You are a diegetic scene renderer. The world is already committed. "
                    "You may improve clarity and atmosphere but may not add facts."
                ),
                tier="cheap",
                component="NarrativeRenderer",
                tick=tick,
                tools=NARRATIVE_TOOLS,
                max_tokens=320,
                temperature=0.55,
            )
            for tool_call in response.get("tool_calls", []):
                if tool_call.get("name") != "submit_narrative":
                    continue
                narrative = self._clean_narrative(
                    str((tool_call.get("input") or {}).get("narrative") or "")
                )
                content_language = get_simulation_language(
                    self.session,
                    fs.resolve_simulation_id(self.session, kami_id),
                )
                if narrative and text_matches_language(narrative, content_language):
                    return narrative
        except Exception as exc:
            logger.warning(
                "Committed narrative rendering failed for %s tick %s: %s",
                kami_id,
                tick,
                exc,
            )
        return factual

    def _select_tier(self, agents: list, intents: list[dict]) -> str:
        """Route to cheap or strong model based on scene complexity."""
        if len(agents) > config.kami_strong_model_threshold_agents:
            max_salience = max(
                (i.get("salience", 0.5) for i in intents), default=0.0
            )
            if max_salience > config.kami_strong_model_threshold_salience:
                return "strong"
        return "cheap"

    def _has_resolution_pressure(
        self,
        kami_id: str,
        tick: int,
        simulation_id: str,
    ) -> bool:
        if self.event_bus.get_pending_events(tick, kami_id):
            return True
        if self.event_bus.get_broadcasts(tick, kami_id):
            return True
        return any(
            schedule.kami_id == kami_id
            for schedule in fs.get_due_schedules(
                self.session,
                tick,
                simulation_id,
            )
        )

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
                narrative_text = self._clean_narrative(
                    inp.get("summary") or inp.get("narrative", "")
                )
                if not narrative_text:
                    narrative_text = self._factual_event_fallback(
                        kami_id,
                        inp.get("event_type", "idle"),
                    )
                payload = dict(inp.get("payload", {}))
                payload["resolution_summary"] = narrative_text
                events.append({
                    "kami_id": kami_id,
                    "tick": tick,
                    "event_type": inp.get("event_type", "idle"),
                    "participants": self._normalize_participants(
                        inp.get("participants", []), kami_id
                    ),
                    "narrative": narrative_text,
                    "salience": inp.get("salience", 0.3),
                    "payload": payload,
                    "causes": list(inp.get("causes", [])),
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
            elif name == "imprint_on_kami":
                mutations.append({
                    "type": "imprint_on_kami",
                    "kami_id": kami_id,
                    "fact": inp["fact"],
                    "importance": inp.get("importance", 0.9),
                    "category": inp.get("category", "event"),
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
            elif name == "emit_message":
                mutations.append({
                    "type": "emit_message",
                    "channel_id": inp["channel_id"],
                    "sender_id": inp["sender_id"],
                    "content": inp["content"],
                    "salience": inp.get("salience", 0.5),
                    "intent_id": inp.get("intent_id"),
                })
            elif name == "initiate_call":
                mutations.append({
                    "type": "make_call",
                    "sender_id": inp["sender_id"],
                    "recipient_id": inp["recipient_id"],
                    "channel_id": inp.get("channel_id"),
                    "intent_id": inp.get("intent_id"),
                })
            elif name == "update_call_state":
                mutations.append({
                    "type": "update_call_state",
                    "channel_id": inp["channel_id"],
                    "agent_id": inp["agent_id"],
                    "state": inp["state"],
                    "intent_id": inp.get("intent_id"),
                })

        # If no emit_event was called, add a fallback idle event
        if not events:
            return self._deterministic_resolution(kami_id, tick, agent_intents or [], narrative)

        factual_narrative = " ".join(
            event["narrative"] for event in events if event.get("narrative")
        )
        return {
            "events": events,
            "mutations": mutations,
            "broadcasts": broadcasts,
            "narrative": factual_narrative or narrative,
        }

    def _append_comms_intents(self, result: dict, intents: list[dict]) -> None:
        """Guarantee that valid communication intents reach the committer."""
        mutations = result.setdefault("mutations", [])
        handled = {
            mutation.get("intent_id")
            for mutation in mutations
            if mutation.get("type") in {"emit_message", "make_call", "update_call_state"}
        }
        handled_messages = {
            (mutation.get("sender_id"), mutation.get("channel_id"))
            for mutation in mutations
            if mutation.get("type") == "emit_message"
        }
        handled_calls = {
            (mutation.get("sender_id"), mutation.get("recipient_id"))
            for mutation in mutations
            if mutation.get("type") == "make_call"
        }
        handled_call_updates = {
            (mutation.get("agent_id"), mutation.get("channel_id"), mutation.get("state"))
            for mutation in mutations
            if mutation.get("type") == "update_call_state"
        }
        for intent in intents:
            intent_id = intent.get("intent_id")
            if intent_id and intent_id in handled:
                continue
            action = intent.get("action_type")
            params = intent.get("params") or {}
            if action == "send_message":
                channel_id = intent.get("target") or params.get("channel_id")
                if (intent.get("agent_id"), channel_id) in handled_messages:
                    continue
                mutations.append({
                    "type": "emit_message",
                    "channel_id": channel_id,
                    "sender_id": intent.get("agent_id"),
                    "content": params.get("content") or intent.get("utterance") or "",
                    "salience": intent.get("salience", 0.5),
                    "intent_id": intent_id,
                })
            elif action == "make_call":
                recipient_id = intent.get("target") or params.get("recipient_id")
                if (intent.get("agent_id"), recipient_id) in handled_calls:
                    continue
                mutations.append({
                    "type": "make_call",
                    "sender_id": intent.get("agent_id"),
                    "recipient_id": recipient_id,
                    "channel_id": params.get("channel_id"),
                    "salience": intent.get("salience", 0.95),
                    "intent_id": intent_id,
                })
            elif action in {"answer_call", "decline_call", "end_call"}:
                channel_id = intent.get("target") or params.get("channel_id")
                state = {
                    "answer_call": "active",
                    "decline_call": "declined",
                    "end_call": "ended",
                }[action]
                if (intent.get("agent_id"), channel_id, state) in handled_call_updates:
                    continue
                mutations.append({
                    "type": "update_call_state",
                    "channel_id": channel_id,
                    "agent_id": intent.get("agent_id"),
                    "state": state,
                    "intent_id": intent_id,
                })

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
            "я вирішу",
            "як камі",
            "симуляція",
            "мовна модель",
            "виклик інструмента",
            "намір агента",
        ]
        if any(marker in lowered for marker in blocked):
            return ""
        return text

    def _factual_event_fallback(self, kami_id: str, event_type: str) -> str:
        kami = self.session.get(fs.Entity, kami_id)
        language = get_simulation_language(
            self.session, kami.simulation_id if kami is not None else None
        )
        place = kami.canonical_name if kami is not None else choose(
            language, en="the location", uk="цьому місці"
        )
        if event_type in {"idle", "background"}:
            return choose(
                language,
                en=f"Nothing changes in {place} during this tick.",
                uk=f"У {place} протягом цього тіку нічого не змінюється.",
            )
        return choose(
            language,
            en=f"The attempted {event_type} produces no additional committed change in {place}.",
            uk=f"Спроба дії типу {event_type} не створює додаткових зафіксованих змін у {place}.",
        )
    def _normalize_participants(
        self, participants: list, kami_id: str
    ) -> list[str]:
        if not participants:
            return []
        simulation_id = fs.resolve_simulation_id(self.session, kami_id)
        entities = self.session.query(fs.Entity).filter(
            fs.Entity.simulation_id == simulation_id,
            fs.Entity.kind == "agent",
        ).all()
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
        self,
        kami_id: str,
        tick: int,
        agent_intents: list[dict] | None = None,
        reason: str = "worker_error",
    ) -> dict:
        result = self._deterministic_resolution(kami_id, tick, agent_intents or [], "")
        result["fallback"] = True
        result["fallback_reason"] = reason
        return result

    def fallback(
        self,
        kami_id: str,
        tick: int,
        agent_intents: list[dict] | None = None,
        reason: str = "worker_error",
    ) -> dict:
        result = self._fallback_result(kami_id, tick, agent_intents, reason)
        self._append_comms_intents(result, agent_intents or [])
        return result

    def _deterministic_resolution(
        self, kami_id: str, tick: int, agent_intents: list[dict], fallback_text: str
    ) -> dict:
        simulation_id = fs.resolve_simulation_id(self.session, kami_id)
        language = get_simulation_language(self.session, simulation_id)
        if not agent_intents:
            idle_text = message("idle_tick", language)
            return {
                "events": [{
                    "kami_id": kami_id,
                    "tick": tick,
                    "event_type": "idle",
                    "participants": [],
                    "narrative": fallback_text or idle_text,
                    "salience": 0.1,
                    "payload": {},
                }],
                "mutations": [],
                "broadcasts": [],
                "narrative": fallback_text or idle_text,
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
            agent_name = intent.get("agent_name") or agent_id or message("someone", language)
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
                    "intent_id": intent.get("intent_id"),
                })
                beats.append(choose(
                    language,
                    en=f"{agent_name} pushes off toward {target}, turning intention into motion.",
                    uk=f"{agent_name} рушає до {target}, перетворюючи намір на дію.",
                ))
            elif action == "talk":
                line = choose(
                    language,
                    en=f' and says, "{utterance}"' if utterance else "",
                    uk=f' і каже: «{utterance}»' if utterance else "",
                )
                beats.append(choose(
                    language,
                    en=f"{agent_name} opens a concrete exchange{line}.",
                    uk=f"{agent_name} починає предметну розмову{line}.",
                ))
                if target:
                    participants.append(target)
                mutations.append({
                    "type": "update_conversation_thread",
                    "kami_id": kami_id,
                    "participants": [p for p in [agent_id, target] if p],
                    "topic": intent.get("goal") or choose(
                        language,
                        en="unfinished conversation",
                        uk="незавершена розмова",
                    ),
                    "summary": utterance or choose(
                        language,
                        en=f"{agent_name} tries to pull the conversation forward.",
                        uk=f"{agent_name} намагається просунути розмову вперед.",
                    ),
                    "status": "active",
                    "tension": 0.45,
                    "momentum": 0.55,
                    "open_question": intent.get("expected_outcome") or None,
                })
            elif action in {"work", "use_object"}:
                beats.append(choose(
                    language,
                    en=f"{agent_name} starts hands-on work" + (f" on {target}" if target else "") + ".",
                    uk=f"{agent_name} береться до практичної роботи" + (f" з {target}" if target else "") + ".",
                ))
                mutations.append({
                    "type": "adjust_need",
                    "agent_id": agent_id,
                    "need": "task_pressure",
                    "delta": -0.03,
                })
            elif action == "observe":
                beats.append(choose(
                    language,
                    en=f"{agent_name} studies the scene" + (f" around {target}" if target else "") + ", looking for a useful next move.",
                    uk=f"{agent_name} уважно оглядає місце" + (f" біля {target}" if target else "") + ", шукаючи корисний наступний крок.",
                ))
            else:
                beats.append(choose(
                    language,
                    en=f"{agent_name} chooses to {action}" + (f" around {target}" if target else "") + ".",
                    uk=f"{agent_name} обирає дію {action}" + (f" біля {target}" if target else "") + ".",
                ))

            if intent.get("intent_id"):
                mutations.append({
                    "type": "record_intent_result",
                    "intent_id": intent["intent_id"],
                    "status": "partial",
                    "summary": beats[-1],
                })

        narrative = " ".join(beats[:4])
        if len(beats) > 4:
            narrative += choose(
                language,
                en=" The rest of the room shifts around those choices.",
                uk=" Решта присутніх реагує на ці рішення.",
            )
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


def _narrative_safe_mutation(mutation: dict) -> dict:
    allowed = {
        "type",
        "entity_id",
        "to_kami_id",
        "attribute",
        "new_value",
        "from_entity",
        "to_entity",
        "rel_type",
        "agent_id",
        "need",
        "delta",
        "value",
        "status",
        "summary",
        "blockers",
        "topic",
        "participants",
        "canonical_name",
        "kind",
        "reason",
    }
    return {key: value for key, value in mutation.items() if key in allowed}
