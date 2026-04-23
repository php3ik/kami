"""AgentCognitionWorker — subjective thinking (spec §2.4).

Agents think and declare intents. They do NOT mutate canon directly.
The kami judges intents.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..factstore import tools as fs
from ..factstore.models import Entity
from ..llm.client import llm_client
from ..spatial.graph import SpatialGraph
from .containment import validate_agent_output
from .prompt_builder import AGENT_TOOLS, build_agent_prompt

logger = logging.getLogger(__name__)


class AgentCognitionWorker:
    """Generates thoughts and intents for a single agent."""

    def __init__(self, session: Session, spatial_graph: SpatialGraph | None = None):
        self.session = session
        self.spatial_graph = spatial_graph

    async def think(
        self,
        agent_id: str,
        kami_id: str,
        tick: int,
        recent_personal_events: list[dict] | None = None,
    ) -> dict:
        """Generate agent thoughts and intents for this tick.

        Returns dict with 'intents', 'beliefs', 'inner_monologue'.
        """
        agent_entity = self.session.get(Entity, agent_id)
        if not agent_entity:
            logger.error(f"Agent {agent_id} not found")
            return self._fallback(agent_id)

        # Get kami state for perception
        kami_state = fs.query_kami_state(self.session, kami_id)

        # Build available destinations from spatial graph
        destinations = None
        if self.spatial_graph:
            neighbors = self.spatial_graph.get_neighbors(kami_id)
            destinations = []
            for nid in neighbors:
                kami_entity = self.session.get(Entity, nid)
                name = kami_entity.canonical_name if kami_entity else nid
                destinations.append({"kami_id": nid, "name": name})

        # Build prompt
        system_blocks, messages = build_agent_prompt(
            self.session, agent_entity, kami_id, kami_state, tick,
            recent_personal_events,
            available_destinations=destinations,
        )

        # Call LLM (always cheap tier for agent cognition)
        try:
            response = await llm_client.call(
                messages=messages,
                system=system_blocks,
                tier="cheap",
                component="AgentWorker",
                tick=tick,
                tools=AGENT_TOOLS,
                max_tokens=600,
                temperature=0.8,
            )
        except Exception as e:
            logger.error(f"LLM call failed for agent {agent_id} tick {tick}: {e}")
            return self._fallback(agent_id)

        # Parse response
        return self._parse_response(response, agent_id, agent_entity)

    def _parse_response(self, response: dict, agent_id: str, agent_entity: Entity) -> dict:
        inner_monologue = response.get("content", "")
        intents = []
        beliefs = []

        for tc in response.get("tool_calls", []):
            if tc["name"] == "intend":
                inp = tc["input"]
                intents.append({
                    "agent_id": agent_id,
                    "agent_name": agent_entity.canonical_name,
                    "action_type": inp.get("action_type", "wait"),
                    "target": inp.get("target", ""),
                    "params": inp.get("params", {}),
                    "salience": inp.get("salience", 0.3),
                })
            elif tc["name"] == "update_belief":
                inp = tc["input"]
                beliefs.append({
                    "agent_id": agent_id,
                    "kind": inp.get("kind", "fact"),
                    "target_entity": inp.get("target_entity"),
                    "attribute": inp.get("attribute"),
                    "believed_value": inp.get("believed_value"),
                    "confidence": inp.get("confidence", 0.8),
                })

        # If no intent declared, default to observing
        if not intents:
            intents.append({
                "agent_id": agent_id,
                "agent_name": agent_entity.canonical_name,
                "action_type": "observe",
                "target": "",
                "params": {},
                "salience": 0.1,
            })

        # Epistemic validation
        social_rels = fs.get_relations(self.session, agent_id, direction="both")
        known_names = set()
        for rel in social_rels:
            other_id = rel.to_entity if rel.from_entity == agent_id else rel.from_entity
            other = self.session.get(Entity, other_id)
            if other:
                known_names.add(other.canonical_name)
        known_names.add(agent_entity.canonical_name)

        is_valid, violations = validate_agent_output(
            inner_monologue, known_names, known_names
        )
        if not is_valid:
            logger.warning(
                f"Agent {agent_id} epistemic violation: references {violations}"
            )

        return {
            "agent_id": agent_id,
            "intents": intents,
            "beliefs": beliefs,
            "inner_monologue": inner_monologue,
        }

    def _fallback(self, agent_id: str) -> dict:
        return {
            "agent_id": agent_id,
            "intents": [{
                "agent_id": agent_id,
                "agent_name": "unknown",
                "action_type": "wait",
                "target": "",
                "params": {},
                "salience": 0.1,
            }],
            "beliefs": [],
            "inner_monologue": "",
        }
