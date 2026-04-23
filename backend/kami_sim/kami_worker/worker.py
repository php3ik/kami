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

        # Build prompt
        system_blocks, messages = build_kami_prompt(
            self.session, kami_id, kami_entity, tick,
            agent_intents, self.event_bus, self.spatial_graph,
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
            # Fallback: emit idle event
            return self._fallback_result(kami_id, tick)

        # Parse tool calls into propose-list
        return self._parse_response(response, kami_id, tick)

    def _select_tier(self, agents: list, intents: list[dict]) -> str:
        """Route to cheap or strong model based on scene complexity."""
        if len(agents) > config.kami_strong_model_threshold_agents:
            max_salience = max(
                (i.get("salience", 0.5) for i in intents), default=0.0
            )
            if max_salience > config.kami_strong_model_threshold_salience:
                return "strong"
        return "cheap"

    def _parse_response(self, response: dict, kami_id: str, tick: int) -> dict:
        """Parse LLM response into structured propose-list."""
        mutations = []
        events = []
        narrative = response.get("content", "")
        broadcasts = []

        for tc in response.get("tool_calls", []):
            name = tc["name"]
            inp = tc["input"]

            if name == "emit_event":
                events.append({
                    "kami_id": kami_id,
                    "tick": tick,
                    "event_type": inp.get("event_type", "idle"),
                    "participants": inp.get("participants", []),
                    "narrative": inp.get("narrative", ""),
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

        # If no emit_event was called, add a fallback idle event
        if not events:
            events.append({
                "kami_id": kami_id,
                "tick": tick,
                "event_type": "idle",
                "participants": [],
                "narrative": narrative or "It was quiet.",
                "salience": 0.1,
                "payload": {},
            })

        return {
            "events": events,
            "mutations": mutations,
            "broadcasts": broadcasts,
            "narrative": narrative,
        }

    def _fallback_result(self, kami_id: str, tick: int) -> dict:
        return {
            "events": [{
                "kami_id": kami_id,
                "tick": tick,
                "event_type": "idle",
                "participants": [],
                "narrative": "It was quiet.",
                "salience": 0.1,
                "payload": {},
            }],
            "mutations": [],
            "broadcasts": [],
            "narrative": "It was quiet.",
        }
