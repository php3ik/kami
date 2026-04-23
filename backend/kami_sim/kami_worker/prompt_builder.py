"""KamiWorker prompt builder — assembles context per spec §2.3.1.

Order matters for prompt caching: stable prefix first, dynamic data last.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..eventbus.bus import EventBus
from ..factstore.models import Entity
from ..factstore.tools import get_events, query_kami_state
from ..spatial.graph import SpatialGraph
from sqlalchemy.orm import Session

SYSTEM_PROMPT = (Path(__file__).parent / "system_prompts" / "kami_system.txt").read_text()

# Tool definitions for the LLM
KAMI_TOOLS = [
    {
        "name": "move_entity",
        "description": "Move an entity to an adjacent kami.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "to_kami_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["entity_id", "to_kami_id"],
        },
    },
    {
        "name": "change_state",
        "description": "Change a physical attribute of an entity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "attribute": {"type": "string"},
                "new_value": {},
                "reason": {"type": "string"},
            },
            "required": ["entity_id", "attribute", "new_value"],
        },
    },
    {
        "name": "update_relation",
        "description": "Update a relationship between two entities.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_entity": {"type": "string"},
                "to_entity": {"type": "string"},
                "rel_type": {"type": "string"},
                "weight": {"type": "object"},
            },
            "required": ["from_entity", "to_entity", "rel_type"],
        },
    },
    {
        "name": "create_entity",
        "description": "Create a new entity in this kami (quota-limited).",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "canonical_name": {"type": "string"},
                "archetype": {"type": "object"},
            },
            "required": ["kind", "canonical_name"],
        },
    },
    {
        "name": "emit_event",
        "description": "MANDATORY: Emit a summary event for this tick. Must be called exactly once at the end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "description": "e.g. idle, conversation, action, arrival, departure"},
                "participants": {"type": "array", "items": {"type": "string"}},
                "narrative": {"type": "string", "description": "2-4 sentence description of what happened"},
                "salience": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "payload": {"type": "object"},
            },
            "required": ["event_type", "narrative", "salience"],
        },
    },
    {
        "name": "publish_broadcast",
        "description": "Emit a compressed digest for neighboring kami to perceive.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "One-line digest of notable activity"},
                "salience": {"type": "number"},
            },
            "required": ["text", "salience"],
        },
    },
]


def build_kami_prompt(
    session: Session,
    kami_id: str,
    kami_entity: Any,
    tick: int,
    agent_intents: list[dict],
    event_bus: EventBus,
    spatial_graph: SpatialGraph,
) -> tuple[list[dict], list[dict]]:
    """Build the full prompt for a kami worker call.

    Returns (system_blocks, messages) for the LLM call.
    """
    # 1. System prompt (cached)
    # 2. Kami identity (cached)
    identity = _build_identity(kami_entity)

    # 3. Long-term memory (placeholder for consolidation)
    ltm = ""

    # 4. Recent events
    recent_events = get_events(session, kami_id=kami_id, since_tick=max(0, tick - 15), limit=15)
    recent_block = _format_recent_events(recent_events)

    # 5. Neighbor digests
    neighbor_digest = _build_neighbor_digest(kami_id, tick, event_bus, spatial_graph)

    # 5.5 Adjacent locations (for move_entity tool)
    adjacent_block = _build_adjacent_locations(session, kami_id, spatial_graph)

    # 6. Present entities (YAML, structured — the anti-drift anchor)
    state = query_kami_state(session, kami_id)
    present_entities = _format_present_entities(state)

    # 7. Agent intents
    intents_block = _format_agent_intents(agent_intents)

    # 8. Pending external events
    pending = event_bus.get_pending_events(tick, kami_id)
    pending_block = _format_pending_events(pending)

    # Build system blocks with caching
    system_blocks = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
    ]
    if identity:
        system_blocks.append(
            {"type": "text", "text": identity, "cache_control": {"type": "ephemeral"}}
        )

    # Build user message (dynamic parts — no caching)
    user_content = f"""## Tick {tick}

### Long-term Memory
{ltm or "No significant long-term memories yet."}

### Recent Events
{recent_block or "No recent events."}

### Neighbor Activity
{neighbor_digest or "Nothing notable from neighbors."}

### PRESENT_ENTITIES (authoritative — respect this exactly)
{present_entities}

### Agent Intents This Tick
{intents_block or "No agent intents declared."}

### Pending External Events
{pending_block or "None."}

### Adjacent Locations (valid targets for move_entity — use EXACT kami IDs)
{adjacent_block}

### Task
Resolve this tick. Call tools for any state changes. When using move_entity, you MUST use one of the exact kami IDs listed in Adjacent Locations above. End with exactly one emit_event call summarizing what happened."""

    messages = [{"role": "user", "content": user_content}]

    return system_blocks, messages


def _build_identity(kami_entity: Any) -> str:
    arch = kami_entity.archetype if kami_entity.archetype else {}
    desc = arch.get("description", "A place in town.")
    kind = arch.get("kami_kind", "location")
    ambiance = arch.get("ambiance", "")
    return f"""## Kami Identity
Name: {kami_entity.canonical_name}
Kind: {kind}
Description: {desc}
{f'Ambiance: {ambiance}' if ambiance else ''}"""


def _format_recent_events(events: list) -> str:
    if not events:
        return ""
    lines = []
    for evt in reversed(events):  # chronological
        lines.append(f"- [tick {evt.tick}] ({evt.event_type}, salience={evt.salience}): {evt.narrative}")
    return "\n".join(lines)


def _build_neighbor_digest(
    kami_id: str, tick: int, event_bus: EventBus, spatial_graph: SpatialGraph
) -> str:
    broadcasts = event_bus.get_broadcasts(tick, kami_id)
    if not broadcasts:
        return ""
    return "\n".join(f"- {b}" for b in broadcasts)


def _build_adjacent_locations(
    session: Session, kami_id: str, spatial_graph: SpatialGraph
) -> str:
    """Build list of adjacent kami with their exact IDs."""
    neighbors = spatial_graph.get_neighbors(kami_id)
    if not neighbors:
        return "No adjacent locations."
    lines = []
    for nid in neighbors:
        kami_entity = session.get(Entity, nid)
        name = kami_entity.canonical_name if kami_entity else nid
        edge = spatial_graph.get_edge_data(kami_id, nid)
        edge_type = edge.get("edge_type", "adjacent") if edge else "adjacent"
        lines.append(f"- {nid} — {name} ({edge_type})")
    return "\n".join(lines)


def _format_present_entities(state: dict) -> str:
    """Format as YAML-like structured block — the anti-drift anchor."""
    lines = []
    for entity in state["entities"]:
        lines.append(f"- id: {entity['entity_id']}")
        lines.append(f"  kind: {entity['kind']}")
        lines.append(f"  name: {entity['name']}")
        if entity.get("states"):
            for attr, val in entity["states"].items():
                lines.append(f"  {attr}: {val}")
        if entity.get("archetype"):
            arch = entity["archetype"]
            if arch.get("description"):
                lines.append(f"  description: {arch['description']}")
    return "\n".join(lines) if lines else "No entities present."


def _format_agent_intents(intents: list[dict]) -> str:
    if not intents:
        return ""
    lines = []
    for intent in intents:
        agent = intent.get("agent_name", intent.get("agent_id", "unknown"))
        action = intent.get("action_type", "unknown")
        target = intent.get("target", "")
        params = intent.get("params", {})
        lines.append(f"- {agent} intends to {action}" + (f" targeting {target}" if target else "") + (f" ({params})" if params else ""))
    return "\n".join(lines)


def _format_pending_events(pending: list) -> str:
    if not pending:
        return ""
    lines = []
    for evt in pending:
        lines.append(f"- [{evt.event_type}] from {evt.source_kami_id}: {evt.narrative_digest} (salience={evt.salience:.2f})")
    return "\n".join(lines)
