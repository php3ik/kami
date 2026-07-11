"""AgentCognitionWorker prompt builder — spec §2.4.

Agents must not know what they cannot know. This is the hardest engineering
problem in the system.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..comms.channels import get_agent_channels
from ..factstore import tools as fs
from ..factstore.models import Entity
from ..memory import memory_runtime
from ..language import get_simulation_language, language_instruction
from .containment import filter_perception


AGENT_SYSTEM_PROMPT = """You are a person living your life. You think in first person. You do NOT narrate — you ARE this person.

CRITICAL RULES:
1. You can ONLY know what is in YOUR persona, YOUR memories, and WHAT_YOU_PERCEIVE.
2. You do NOT know the names of people you have never met. They appear as descriptions.
3. You do NOT know what is happening in other places unless someone told you.
4. You do NOT have access to information outside your experience.
EPISTEMIC LABELS: OBSERVED and OBSERVED_EVENT are evidence; REMEMBERED may be incomplete; BELIEVED and REFLECTION may be wrong; TOLD_BY and FEED_POST are claims, not automatically facts.
BELIEF DISCIPLINE: Update a belief only when this tick gives you evidence. Never turn another person's private attitude or an unverified message into objective truth.
5. Your inner monologue should be in YOUR voice — use the speech patterns, vocabulary, and emotional register from your persona.

BAD example: "I notice that John, the baker who recently argued with his wife, is here."
(How do you know about his argument if you weren't there?)

GOOD example: "There's John from the bakery. He looks tired today."
(Based on what you can perceive right now.)

LIVENESS RULES:
1. Every tick must do one of these: advance a personal goal, answer another person, satisfy a need, investigate a concrete clue, or deliberately rest.
2. If your last intent stalled, do not repeat it verbatim. Escalate, pivot, ask for help, or choose a smaller physical action.
3. Conversations must add new content: a question, admission, refusal, joke, practical offer, or silence with a reason.
4. Your body matters. Fatigue, hunger, stress, social tension, and task pressure should shape choices.
5. If you speak to someone you do not know, use the perceived target_id shown in WHAT_YOU_PERCEIVE. Never invent targets like "unknown_person_1".
6. If a thread has an open question and you cannot answer it, refuse, defer, leave, or ask one different concrete question. Do not keep pressing the same line.

COMMUNICATION RULES:
1. Message content below is known to you because you actually opened it this tick.
2. To write remotely, intend action_type=send_message with the exact channel ID as target and content in params.content.
3. To call, intend action_type=make_call with an agent ID as target, or answer_call/end_call with a phone channel ID.
4. Social feeds are pull-based. Use action_type=check_feed; posts become visible on your next cognition tick.
5. If a direct message asks a question or requests action, answer or explicitly defer through send_message. Do not silently observe and ignore it.

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
                    "description": "What you want to do: talk, move, send_message, make_call, answer_call, decline_call, end_call, check_feed, use_object, wait, observe, work, etc.",
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
                "goal": {"type": "string", "description": "The concrete goal this intent advances."},
                "utterance": {"type": "string", "description": "Exact short line you say if action_type is talk."},
                "expected_outcome": {"type": "string", "description": "What you hope will change if this works."},
                "continues_thread_id": {"type": "string", "description": "Existing conversation thread ID if this continues one."},
                "exit_condition": {"type": "string", "description": "When you will stop repeating this action."},
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
    pending_communications: list[dict] | None = None,
    feed_posts: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Build full prompt for agent cognition call.

    Returns (system_blocks, messages).
    """
    archetype = agent_entity.archetype or {}
    content_language = get_simulation_language(
        session, agent_entity.simulation_id
    )
    language_contract = language_instruction(content_language)

    # 1. System prompt (cached)
    # 2. Persona (cached for agent lifetime)
    persona = _build_persona(agent_entity, archetype)

    # 3. Goals hierarchy
    goals = _build_goals(archetype)

    # 4. Emotional state
    emotional = _build_emotional_state(archetype)

    # 5. Relevant memories (placeholder — Phase 4)
    recent_intents = fs.get_recent_intents(session, agent_id=agent_entity.entity_id, limit=6)
    intent_memories = _format_recent_intents(recent_intents)
    present_agent_ids = [
        entity["entity_id"]
        for entity in kami_state.get("entities", [])
        if entity.get("kind") == "agent"
    ]
    memory_query = " ".join(
        str(event.get("narrative") or "")
        for event in (recent_personal_events or [])[-3:]
    )
    episodic_memories, long_term_memory = memory_runtime.prompt_context(
        agent_id=agent_entity.entity_id,
        query=memory_query,
        present_agents=present_agent_ids,
        current_tick=tick,
        simulation_id=agent_entity.simulation_id,
    )
    memories = "\n".join(
        block for block in (episodic_memories, intent_memories) if block
    ) or "No significant memories are currently salient."

    # 7. Social graph slice
    social_relations = fs.get_relations(session, agent_entity.entity_id, direction="both")
    social_graph_ids = set()
    for rel in social_relations:
        if relation_is_visible_to_agent(rel, agent_entity.entity_id):
            other = rel.to_entity if rel.from_entity == agent_entity.entity_id else rel.from_entity
            social_graph_ids.add(other)
    social_block = _build_social_block(session, agent_entity.entity_id, social_relations, kami_state)
    beliefs_block = _format_beliefs(fs.get_beliefs(session, agent_entity.entity_id))
    needs_block = _format_needs(fs.get_agent_needs(session, agent_entity.entity_id))
    threads_block = _format_threads(
        fs.get_active_conversations(session, kami_id=kami_id, agent_id=agent_entity.entity_id)
    )

    # 8. Filtered perception (epistemic containment)
    perception = filter_perception(kami_state, agent_entity.entity_id, social_graph_ids)
    perception_block = _format_perception(perception, tick)

    # 9. Recent personal buffer
    personal_buffer = _format_personal_buffer(recent_personal_events)

    # 10. Pending communications and available channels
    comms = _format_communications(pending_communications)
    feed = _format_feed(feed_posts)
    channels = _format_channels(
        session,
        agent_entity.entity_id,
        get_agent_channels(session, agent_entity.entity_id),
    )

    # Build system blocks with caching
    system_blocks = [
        {"type": "text", "text": AGENT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": language_contract, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": persona, "cache_control": {"type": "ephemeral"}},
    ]

    # Build user message (dynamic)
    user_content = f"""## Tick {tick}

### Your Goals
{goals}

### Emotional State
{emotional}

### Current Needs
{needs_block}

### Relevant Memories
{memories}

### Current Beliefs (subjective, not canon)
{beliefs_block or "You have no explicit current beliefs beyond direct perception and memory."}

### Active Social Threads
{threads_block or "No active thread involving you here."}

{f'### Long-Term Understanding (subjective reflection){chr(10)}[REFLECTION]{chr(10)}{long_term_memory}' if long_term_memory else ''}

### People You Know (present or relevant)
{social_block or "You don't know anyone here."}

### WHAT_YOU_PERCEIVE
{perception_block}

### Recent Observed Events
{personal_buffer or "You just arrived or woke up."}

{f'### Messages{chr(10)}{comms}' if comms else ''}

{f'### Social Feed You Checked{chr(10)}{feed}' if feed else ''}

### Communication Channels
{channels or "You have no communication channels."}

### Available Destinations (if you want to move)
{_format_destinations(available_destinations)}

### Task
Think as {agent_entity.canonical_name}. Use the required content language for your inner monologue, spoken utterance, message content, goals, expected outcomes, and belief text. Brief inner monologue (1-3 sentences in your voice). Then call `intend` to declare what you do. If you want to move, use the EXACT kami ID from the destinations list as the target."""

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
            outgoing = rel.from_entity == agent_id
            if not relation_is_visible_to_agent(rel, agent_id):
                continue
            present_marker = " [HERE]" if other_id in present_ids else ""
            weight_info = _visible_relation_weight(
                rel.weight,
                agent_id,
                allow_default=outgoing,
            )
            direction = "your relation to" if outgoing else "known relation toward you from"
            lines.append(
                f"- [KNOWN_RELATION since={rel.since_tick}] {direction} "
                f"{other.canonical_name}: {rel.rel_type}{weight_info}{present_marker}"
            )
    return "\n".join(lines)


def relation_is_visible_to_agent(relation, agent_id: str) -> bool:
    if relation.from_entity == agent_id:
        return True
    weight = relation.weight if isinstance(relation.weight, dict) else {}
    visibility = str(weight.get("visibility") or "").casefold()
    if visibility in {"public", "shared", "to_target"}:
        return True
    return relation.rel_type in {
        "married_to",
        "friends_with",
        "sibling_of",
        "parent_of",
        "child_of",
        "employs",
        "owes",
        "knows",
    }


def _visible_relation_weight(
    weight: Any,
    agent_id: str,
    *,
    allow_default: bool,
) -> str:
    if not isinstance(weight, dict):
        return ""
    visible_keys = {
        "affection",
        "dependence",
        "familiarity",
        "strength",
        "tension",
        "trust",
        "value",
    }
    raw_allowed = weight.get("visible_to") or []
    allowed = (
        {str(item) for item in raw_allowed}
        if isinstance(raw_allowed, list)
        else {str(raw_allowed)}
    )
    visibility = str(weight.get("visibility") or "").casefold()
    explicitly_visible = (
        visibility in {"public", "shared", "to_target"}
        or agent_id in allowed
    )
    visible = {
        key: value
        for key, value in weight.items()
        if key in visible_keys and (allow_default or explicitly_visible)
    }
    if explicitly_visible:
        if weight.get("known_history"):
            visible["known_history"] = weight["known_history"]
    if not visible:
        return ""
    return f" ({', '.join(f'{key}={value}' for key, value in visible.items())})"


def _format_perception(perception: dict, tick: int) -> str:
    if not perception["entities"]:
        return "You are alone. The place is quiet."
    lines = []
    for e in perception["entities"]:
        states_str = ""
        if e.get("states"):
            states_str = " — " + ", ".join(f"{k}: {v}" for k, v in e["states"].items())
        lines.append(
            f"- [OBSERVED tick={tick}] target_id: {e['entity_id']} | "
            f"{e['name']} ({e['kind']}){states_str}"
        )
    return "\n".join(lines)


def _format_personal_buffer(events: list[dict] | None) -> str:
    if not events:
        return ""
    lines = []
    for evt in reversed(events):
        source = evt.get("source", "observed")
        event_id = evt.get("event_id", "unknown")
        lines.append(
            f"- [OBSERVED_EVENT tick={evt.get('tick', '?')} source={source} "
            f"event={event_id}]: {evt.get('narrative', evt.get('action', ''))}"
        )
    return "\n".join(lines)


def _format_recent_intents(intents: list) -> str:
    lines = []
    for intent in reversed(intents):
        target = f" -> {intent.target}" if intent.target else ""
        result = intent.result_summary or "pending"
        lines.append(
            f"- [ACTION_RESULT tick={intent.tick}] {intent.action_type}{target}: "
            f"{intent.status}; {result}"
        )
    return "\n".join(lines)


def _format_needs(needs: dict[str, float]) -> str:
    return "\n".join(f"- {need}: {value:.2f}" for need, value in sorted(needs.items()))


def _format_beliefs(beliefs: list) -> str:
    latest = {}
    for belief in sorted(beliefs, key=lambda item: item.since_tick, reverse=True):
        key = (belief.kind, belief.target_entity, belief.attribute)
        latest.setdefault(key, belief)

    lines = []
    for belief in list(latest.values())[:12]:
        subject = belief.target_entity or "unspecified subject"
        attribute = f".{belief.attribute}" if belief.attribute else ""
        source = belief.source_event_id or "own_inference"
        lines.append(
            f"- [BELIEVED tick={belief.since_tick} confidence={belief.confidence:.2f} "
            f"source={source}] {belief.kind}: {subject}{attribute} = {belief.believed_value!r}"
        )
    return "\n".join(lines)


def _format_threads(threads: list) -> str:
    lines = []
    for thread in threads:
        participants = ", ".join(thread.participants or [])
        line = (
            f"- {thread.thread_id}: {thread.topic} [{thread.status}], "
            f"participants={participants}, tension={thread.tension:.2f}, "
            f"momentum={thread.momentum:.2f}; {thread.summary}"
        )
        if thread.open_question:
            line += f" Open question: {thread.open_question}"
        lines.append(line)
    return "\n".join(lines)


def _format_destinations(destinations: list[dict] | None) -> str:
    if not destinations:
        return "You cannot move from here right now."
    lines = []
    for d in destinations:
        lines.append(f"- {d['kami_id']} — {d['name']}")
    return "\n".join(lines)


def _format_communications(messages: list[dict] | None) -> str:
    if not messages:
        return ""
    return "\n".join(
        f"- [TOLD_BY tick={message['sent_at_tick']} sender={message['sender_id']}] "
        f"message_id={message['message_id']} | channel={message['channel_id']} | "
        f"from {message['sender_name']} ({message['sender_id']}) at tick "
        f"{message['sent_at_tick']}: {message['content']}"
        for message in messages
    )


def _format_feed(messages: list[dict] | None) -> str:
    if not messages:
        return ""
    return "\n".join(
        f"- [FEED_POST channel={message['channel_id']}] "
        f"{message['sender_name']}: {message['content']}"
        for message in messages
    )


def _format_channels(session: Session, agent_id: str, channels: list) -> str:
    lines = []
    for channel in channels:
        members = []
        for member_id in channel.participants or []:
            if member_id == agent_id:
                continue
            entity = session.get(Entity, member_id)
            members.append(
                f"{entity.canonical_name} ({member_id})" if entity else member_id
            )
        state = (channel.metadata_ or {}).get("call_state")
        lines.append(
            f"- {channel.channel_id}: {channel.kind}; contacts="
            f"{', '.join(members) or 'broadcast'}"
            + (f"; call_state={state}" if state else "")
        )
    return "\n".join(lines)
