"""KamiWorker prompt builder — assembles context per spec §2.3.1.

Order matters for prompt caching: stable prefix first, dynamic data last.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..comms.channels import get_kami_notifications
from ..eventbus.bus import EventBus
from ..factstore.models import Entity
from ..factstore.tools import (
    get_active_conversations,
    get_due_schedules,
    get_events,
    query_kami_state,
)
from ..memory import memory_runtime
from ..language import get_simulation_language, language_instruction
from ..spatial.graph import SpatialGraph
from sqlalchemy.orm import Session
from .scene_dynamics import SceneDynamics

SYSTEM_PROMPT = (Path(__file__).parent / "system_prompts" / "kami_system.txt").read_text()

# Tool definitions for the LLM
KAMI_TOOLS = [
    {
        "name": "move_entity",
        "description": "Schedule an agent to leave for an adjacent kami. Departure occurs next tick and arrival one tick later.",
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
        "description": "MANDATORY: Emit one factual event record for this tick after all proposed mutations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "description": "e.g. idle, conversation, action, arrival, departure"},
                "participants": {"type": "array", "items": {"type": "string"}},
                "summary": {
                    "type": "string",
                    "description": "One factual sentence describing only resolved actions and consequences; no literary narration",
                },
                "narrative": {
                    "type": "string",
                    "description": "Backward-compatible factual summary; prefer summary",
                },
                "salience": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "payload": {"type": "object"},
                "causes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "IDs of earlier events that directly caused this event",
                },
            },
            "required": ["event_type", "summary", "salience"],
        },
    },
    {
        "name": "update_conversation_thread",
        "description": "Create or update a live conversation/social thread carried across ticks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string"},
                "participants": {"type": "array", "items": {"type": "string"}},
                "topic": {"type": "string"},
                "status": {"type": "string", "description": "active, paused, resolved, ruptured"},
                "summary": {"type": "string"},
                "tension": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "momentum": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "open_question": {"type": "string"},
            },
            "required": ["participants", "topic", "summary"],
        },
    },
    {
        "name": "record_intent_result",
        "description": "Mark a specific agent intent as resolved, blocked, partial, or failed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "intent_id": {"type": "string"},
                "status": {"type": "string"},
                "summary": {"type": "string"},
                "blockers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["intent_id", "status", "summary"],
        },
    },
    {
        "name": "adjust_need",
        "description": "Adjust an embodied agent need after the scene has physical or emotional consequences.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "need": {"type": "string", "description": "fatigue, hunger, stress, social, task_pressure"},
                "delta": {"type": "number"},
                "value": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["agent_id", "need"],
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
    {
        "name": "imprint_on_kami",
        "description": "Permanently attach a rare, identity-changing fact or physical trace to this place.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string"},
                "importance": {"type": "number", "minimum": 0.8, "maximum": 1.0},
                "category": {"type": "string"},
            },
            "required": ["fact"],
        },
    },
    {
        "name": "emit_message",
        "description": "Validate and emit a remote message requested by an agent intent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "sender_id": {"type": "string"},
                "content": {"type": "string"},
                "salience": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "intent_id": {"type": "string"},
            },
            "required": ["channel_id", "sender_id", "content"],
        },
    },
    {
        "name": "initiate_call",
        "description": "Start a phone call requested by an agent intent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sender_id": {"type": "string"},
                "recipient_id": {"type": "string"},
                "channel_id": {"type": "string"},
                "intent_id": {"type": "string"},
            },
            "required": ["sender_id", "recipient_id"],
        },
    },
    {
        "name": "update_call_state",
        "description": "Answer, decline, or end an existing phone call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "state": {"type": "string", "enum": ["active", "declined", "ended"]},
                "intent_id": {"type": "string"},
            },
            "required": ["channel_id", "agent_id", "state"],
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
    scene_dynamics: SceneDynamics | None = None,
) -> tuple[list[dict], list[dict]]:
    """Build the full prompt for a kami worker call.

    Returns (system_blocks, messages) for the LLM call.
    """
    # 1. System prompt (cached)
    content_language = get_simulation_language(
        session, kami_entity.simulation_id
    )
    language_contract = language_instruction(content_language)
    # 2. Kami identity (cached)
    identity = _build_identity(kami_entity)

    # 3. Long-term memory
    ltm = memory_runtime.kami_prompt_context(
        kami_id, kami_entity.simulation_id
    )

    # 4. Recent events
    recent_events = get_events(session, kami_id=kami_id, since_tick=max(0, tick - 15), limit=15)
    recent_block = _format_recent_events(recent_events)
    active_threads = _format_threads(get_active_conversations(session, kami_id=kami_id, limit=5))
    dynamics_block = scene_dynamics.to_prompt_block() if scene_dynamics else ""

    # 5. Neighbor digests
    neighbor_digest = _build_neighbor_digest(kami_id, tick, event_bus, spatial_graph)

    # 5.5 Adjacent locations (for move_entity tool)
    adjacent_block = _build_adjacent_locations(session, kami_id, spatial_graph)

    # 6. Present entities (YAML, structured — the anti-drift anchor)
    state = query_kami_state(session, kami_id)
    present_entities = _format_present_entities(state)

    # 7. Agent intents
    intents_block = _format_agent_intents(agent_intents, scene_dynamics)

    # 8. Pending external events
    pending = event_bus.get_pending_events(tick, kami_id)
    pending_block = _format_pending_events(pending)
    scheduled_block = _format_schedules(
        schedule
        for schedule in get_due_schedules(
            session, tick, kami_entity.simulation_id
        )
        if schedule.kami_id == kami_id
    )
    notification_block = _format_notifications(
        get_kami_notifications(session, kami_id, tick)
    )

    # Build system blocks with caching
    system_blocks = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": language_contract, "cache_control": {"type": "ephemeral"}},
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

### Active Conversation Threads
{active_threads or "No active conversation threads."}

### Scene Dynamics Guardrails
{dynamics_block or "No special scene pressure."}

### Neighbor Activity
{neighbor_digest or "Nothing notable from neighbors."}

### PRESENT_ENTITIES (authoritative — respect this exactly)
{present_entities}

### Agent Intents This Tick
{intents_block or "No agent intents declared."}

### Pending External Events
{pending_block or "None."}

### Scheduled Events Due Now
{scheduled_block or "None."}

### Device Notifications In This Place
{notification_block or "None."}

### Adjacent Locations (valid targets for move_entity — use EXACT kami IDs)
{adjacent_block}

### Task
Adjudicate this tick as a typed state diff. Reject blocked preconditions, resolve conflicts in the listed initiative order, and call tools only for validated consequences. Write every human-readable tool field in the required content language, while keeping enum values and IDs unchanged. When using move_entity, use one exact adjacent kami ID. Agent movement schedules travel; do not claim immediate arrival. End with exactly one emit_event call containing a concise factual summary. Do not write literary narrative; a separate renderer runs only after the diff commits."""

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
            uses = arch.get("uses") or arch.get("affordances")
            if uses:
                if isinstance(uses, str):
                    uses = [uses]
                lines.append(f"  affordances: {json.dumps(uses[:8], ensure_ascii=False)}")
            if arch.get("condition"):
                lines.append(f"  declared_condition: {arch['condition']}")
    return "\n".join(lines) if lines else "No entities present."


def _format_agent_intents(
    intents: list[dict],
    scene_dynamics: SceneDynamics | None = None,
) -> str:
    if not intents:
        return ""
    lines = []
    assessment_by_id = {
        item.get("intent_id"): item
        for item in (scene_dynamics.intent_assessments if scene_dynamics else [])
    }
    for intent in intents:
        agent = intent.get("agent_name", intent.get("agent_id", "unknown"))
        action = intent.get("action_type", "unknown")
        target = intent.get("target", "")
        params = intent.get("params", {})
        intent_id = intent.get("intent_id", "")
        utterance = intent.get("utterance", "")
        goal = intent.get("goal", "")
        details = []
        if params:
            details.append(str(params))
        if goal:
            details.append(f"goal={goal}")
        if utterance:
            details.append(f"says={utterance}")
        if intent_id:
            details.append(f"intent_id={intent_id}")
        assessment = assessment_by_id.get(intent_id)
        status = assessment.get("status", "unassessed") if assessment else "unassessed"
        if assessment and assessment.get("reason"):
            details.append(f"precondition={assessment['reason']}")
        lines.append(
            f"- [{status.upper()}] {agent} intends to {action}"
            + (f" targeting {target}" if target else "")
            + (f" ({'; '.join(details)})" if details else "")
        )
    return "\n".join(lines)


def _format_threads(threads: list) -> str:
    lines = []
    for thread in threads:
        participants = ", ".join(thread.participants or [])
        lines.append(
            f"- {thread.thread_id}: {thread.topic}; participants={participants}; "
            f"status={thread.status}; tension={thread.tension:.2f}; "
            f"momentum={thread.momentum:.2f}; summary={thread.summary}"
            + (f"; open={thread.open_question}" if thread.open_question else "")
        )
    return "\n".join(lines)


def _format_pending_events(pending: list) -> str:
    if not pending:
        return ""
    lines = []
    for evt in pending:
        lines.append(f"- [{evt.event_type}] from {evt.source_kami_id}: {evt.narrative_digest} (salience={evt.salience:.2f})")
    return "\n".join(lines)


def _format_notifications(notifications: list[dict]) -> str:
    return "\n".join(
        f"- {item['description']} for {item['recipient_id']} "
        f"(salience={item['salience']:.2f}, message_id={item['message_id']})"
        for item in notifications
    )


def _format_schedules(schedules) -> str:
    return "\n".join(
        f"- schedule_id={schedule.schedule_id}: "
        f"{json.dumps(schedule.event_template or {}, ensure_ascii=False)}"
        for schedule in schedules
    )
