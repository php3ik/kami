"""AgentCognitionWorker prompt builder — spec §2.4.

Agents must not know what they cannot know. This is the hardest engineering
problem in the system.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..factstore import tools as fs
from ..factstore.models import Entity
from .containment import filter_perception


AGENT_SYSTEM_PROMPT = """You are a person living your life. You think in first person. You do NOT narrate — you ARE this person.

CRITICAL RULES:
1. You can ONLY know what is in YOUR persona, YOUR memories, and WHAT_YOU_PERCEIVE.
2. You do NOT know the names of people you have never met. They appear as descriptions.
3. You do NOT know what is happening in other places unless someone told you.
4. You do NOT have access to information outside your experience.
5. Your inner monologue should be in YOUR voice — use the speech patterns, vocabulary, and emotional register from your persona.

BAD example: "I notice that John, the baker who recently argued with his wife, is here."
(How do you know about his argument if you weren't there?)

GOOD example: "There's John from the bakery. He looks tired today."
(Based on what you can perceive right now.)

After your brief inner monologue (1-3 sentences in your voice), declare your intent using the intend tool."""

AGENT_TOOLS = [
    {
        "name": "intend",
        "description": "Declare what you want to do this tick. The kami (place-spirit) will judge whether it succeeds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": "What you want to do: talk, move, use_object, wait, observe, work, eat, sleep, check_phone, etc.",
                },
                "target": {
                    "type": "string",
                    "description": "Target entity ID or kami ID (for movement)",
                },
                "params": {
                    "type": "object",
                    "description": "Additional parameters like speech content, item to use, etc.",
                },
                "salience": {
                    "type": "number",
                    "description": "How important/urgent this action is (0.0-1.0)",
                },
            },
            "required": ["action_type"],
        },
    },
    {
        "name": "update_belief",
        "description": "Update your subjective belief about something you just perceived or realized.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "location, state, relation, fact"},
                "target_entity": {"type": "string"},
                "attribute": {"type": "string"},
                "believed_value": {},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "required": ["kind"],
        },
    },
]


def build_agent_prompt(
    session: Session,
    agent_entity: Entity,
    kami_id: str,
    kami_state: dict,
    tick: int,
    recent_personal_events: list[dict] | None = None,
    available_destinations: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Build full prompt for agent cognition call.

    Returns (system_blocks, messages).
    """
    archetype = agent_entity.archetype or {}

    # 1. System prompt (cached)
    # 2. Persona (cached for agent lifetime)
    persona = _build_persona(agent_entity, archetype)

    # 3. Goals hierarchy
    goals = _build_goals(archetype)

    # 4. Emotional state
    emotional = _build_emotional_state(archetype)

    # 5. Relevant memories (placeholder — Phase 4)
    memories = "No significant memories retrieved."

    # 6. Semantic insights (placeholder — Phase 4)
    insights = ""

    # 7. Social graph slice
    social_relations = fs.get_relations(session, agent_entity.entity_id, direction="both")
    social_graph_ids = set()
    for rel in social_relations:
        other = rel.to_entity if rel.from_entity == agent_entity.entity_id else rel.from_entity
        social_graph_ids.add(other)
    social_block = _build_social_block(session, agent_entity.entity_id, social_relations, kami_state)

    # 8. Filtered perception (epistemic containment)
    perception = filter_perception(kami_state, agent_entity.entity_id, social_graph_ids)
    perception_block = _format_perception(perception)

    # 9. Recent personal buffer
    personal_buffer = _format_personal_buffer(recent_personal_events)

    # 10. Pending communications (placeholder — Phase 7)
    comms = ""

    # Build system blocks with caching
    system_blocks = [
        {"type": "text", "text": AGENT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": persona, "cache_control": {"type": "ephemeral"}},
    ]

    # Build user message (dynamic)
    user_content = f"""## Tick {tick}

### Your Goals
{goals}

### Emotional State
{emotional}

### Relevant Memories
{memories}

{f'### Insights{chr(10)}{insights}' if insights else ''}

### People You Know (present or relevant)
{social_block or "You don't know anyone here."}

### WHAT_YOU_PERCEIVE
{perception_block}

### Recent Actions (yours)
{personal_buffer or "You just arrived or woke up."}

{f'### Messages{chr(10)}{comms}' if comms else ''}

### Available Destinations (if you want to move)
{_format_destinations(available_destinations)}

### Task
Think as {agent_entity.canonical_name}. Brief inner monologue (1-3 sentences in your voice). Then call `intend` to declare what you do. If you want to move, use the EXACT kami ID from the destinations list as the target."""

    messages = [{"role": "user", "content": user_content}]
    return system_blocks, messages


def _build_persona(entity: Entity, archetype: dict) -> str:
    name = entity.canonical_name
    age = archetype.get("age", "unknown")
    background = archetype.get("background", "")
    traits = archetype.get("traits", [])
    fears = archetype.get("fears", [])
    desires = archetype.get("desires", [])
    voice = archetype.get("voice", "")
    appearance = archetype.get("appearance", "")

    parts = [f"## You are {name}"]
    if age != "unknown":
        parts.append(f"Age: {age}")
    if appearance:
        parts.append(f"Appearance: {appearance}")
    if background:
        parts.append(f"Background: {background}")
    if traits:
        parts.append(f"Personality: {', '.join(traits)}")
    if fears:
        parts.append(f"Fears: {', '.join(fears)}")
    if desires:
        parts.append(f"Desires: {', '.join(desires)}")
    if voice:
        parts.append(f"Voice/speech style: {voice}")
    return "\n".join(parts)


def _build_goals(archetype: dict) -> str:
    goals = archetype.get("goals", {})
    if not goals:
        return "Live your day as it comes."
    parts = []
    for level in ["life", "seasonal", "daily", "current"]:
        if level in goals:
            parts.append(f"- {level.capitalize()}: {goals[level]}")
    return "\n".join(parts) if parts else "Live your day as it comes."


def _build_emotional_state(archetype: dict) -> str:
    emotion = archetype.get("emotion", {})
    if not emotion:
        return "dominant: neutral\nintensity: 0.3"
    return "\n".join(f"{k}: {v}" for k, v in emotion.items())


def _build_social_block(
    session: Session,
    agent_id: str,
    relations: list,
    kami_state: dict,
) -> str:
    """Build social graph slice for people present or in memories."""
    present_ids = {e["entity_id"] for e in kami_state.get("entities", [])}
    lines = []
    for rel in relations:
        other_id = rel.to_entity if rel.from_entity == agent_id else rel.from_entity
        other = session.get(Entity, other_id)
        if other and other.kind == "agent":
            present_marker = " [HERE]" if other_id in present_ids else ""
            weight_info = ""
            if rel.weight and isinstance(rel.weight, dict):
                weight_info = f" ({', '.join(f'{k}={v}' for k, v in rel.weight.items())})"
            elif rel.weight:
                weight_info = f" ({rel.weight})"
            lines.append(f"- {other.canonical_name}: {rel.rel_type}{weight_info}{present_marker}")
    return "\n".join(lines)


def _format_perception(perception: dict) -> str:
    if not perception["entities"]:
        return "You are alone. The place is quiet."
    lines = []
    for e in perception["entities"]:
        states_str = ""
        if e.get("states"):
            states_str = " — " + ", ".join(f"{k}: {v}" for k, v in e["states"].items())
        lines.append(f"- {e['name']} ({e['kind']}){states_str}")
    return "\n".join(lines)


def _format_personal_buffer(events: list[dict] | None) -> str:
    if not events:
        return ""
    lines = []
    for evt in events:
        lines.append(f"- [tick {evt.get('tick', '?')}]: {evt.get('narrative', evt.get('action', ''))}")
    return "\n".join(lines)


def _format_destinations(destinations: list[dict] | None) -> str:
    if not destinations:
        return "You cannot move from here right now."
    lines = []
    for d in destinations:
        lines.append(f"- {d['kami_id']} — {d['name']}")
    return "\n".join(lines)
