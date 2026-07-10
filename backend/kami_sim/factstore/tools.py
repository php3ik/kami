"""FactStore tool functions — validated mutation layer (spec §2.2, §2.3).

All world mutations go through these functions. No free-text mutation allowed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..determinism import generate_id
from .models import (
    VALID_ATTRIBUTES,
    VALID_REL_TYPES,
    AgentIntentRecord,
    AgentBelief,
    AgentNeed,
    ConversationThread,
    Entity,
    Event,
    Location,
    Ownership,
    PhysicalState,
    Relation,
    Schedule,
    Channel,
    Message,
    ReadReceipt,
)


def _gen_id(prefix: str = "") -> str:
    return generate_id(prefix)


def simulation_id_from_scoped_id(value: str | None) -> str:
    if value and value.startswith("sim_") and "__" in value:
        return value[4:].split("__", 1)[0] or "default"
    return "default"


def resolve_simulation_id(
    session: Session,
    *entity_ids: str | None,
    explicit: str | None = None,
) -> str:
    """Resolve one simulation scope and reject cross-world inputs."""
    scopes = set()
    for entity_id in entity_ids:
        if not entity_id:
            continue
        entity = session.get(Entity, entity_id)
        if entity is not None:
            scopes.add(entity.simulation_id)
            continue
        inferred = simulation_id_from_scoped_id(entity_id)
        if inferred != "default":
            scopes.add(inferred)
    if explicit:
        scopes.add(explicit)
    if len(scopes) > 1:
        raise ValueError(f"Cross-simulation operation is not allowed: {sorted(scopes)}")
    return next(iter(scopes), explicit or "default")


# --- Entity operations ---


def create_entity(
    session: Session,
    kind: str,
    canonical_name: str,
    tick: int,
    archetype: dict | None = None,
    entity_id: str | None = None,
    reason_event_id: str | None = None,
    kami_id: str | None = None,
    quota_tracker: dict | None = None,
    simulation_id: str | None = None,
) -> Entity:
    """Create a new entity with quota enforcement."""
    valid_kinds = {
        "agent", "object", "kami", "animal", "plant",
        "vehicle", "document", "channel",
    }
    if kind not in valid_kinds:
        raise ValueError(f"Invalid entity kind: {kind}. Must be one of {valid_kinds}")

    # Quota check for kami-scoped creation
    if quota_tracker is not None and kami_id is not None:
        key = (kami_id, tick)
        count = quota_tracker.get(key, 0)
        if count >= 3:
            raise ValueError(
                f"Entity creation quota exceeded for kami {kami_id} on tick {tick}"
            )
        quota_tracker[key] = count + 1

    eid = entity_id or _gen_id(f"{kind}_")
    scope = resolve_simulation_id(
        session,
        kami_id,
        eid,
        explicit=simulation_id or (archetype or {}).get("simulation_id"),
    )
    entity = Entity(
        entity_id=eid,
        simulation_id=scope,
        kind=kind,
        canonical_name=canonical_name,
        archetype=archetype or {},
        created_at_tick=tick,
        created_by_event=reason_event_id,
    )
    session.add(entity)
    session.flush()
    return entity


def destroy_entity(
    session: Session, entity_id: str, tick: int, reason_event_id: str | None = None
) -> None:
    """Soft delete: close all temporal rows for this entity."""
    entity = session.get(Entity, entity_id)
    if entity is None:
        raise ValueError(f"Entity {entity_id} not found")

    # Close location
    _close_temporal(session, Location, "entity_id", entity_id, tick)
    # Close ownership
    _close_temporal(session, Ownership, "entity_id", entity_id, tick)
    # Close physical states
    _close_temporal(session, PhysicalState, "entity_id", entity_id, tick)
    # Close relations (both directions)
    _close_temporal(session, Relation, "from_entity", entity_id, tick)
    _close_temporal(session, Relation, "to_entity", entity_id, tick)


def _close_temporal(session: Session, model, field_name: str, value: str, tick: int):
    """Close all current temporal rows for a given field/value."""
    rows = (
        session.query(model)
        .filter(
            getattr(model, field_name) == value,
            getattr(model, "valid_until_tick").is_(None),
        )
        .all()
    )
    for row in rows:
        row.valid_until_tick = tick


# --- Location operations ---


def get_current_location(session: Session, entity_id: str) -> Location | None:
    """Get the current location of an entity."""
    scope = resolve_simulation_id(session, entity_id)
    return (
        session.query(Location)
        .filter(
            Location.simulation_id == scope,
            Location.entity_id == entity_id,
            Location.valid_until_tick.is_(None),
        )
        .first()
    )


def move_entity(
    session: Session,
    entity_id: str,
    to_kami_id: str,
    tick: int,
    container_id: str | None = None,
    reason_event_id: str | None = None,
) -> Location:
    """Move entity to a new kami. Enforces single-current-location invariant."""
    # Validate entities exist
    entity = session.get(Entity, entity_id)
    if entity is None:
        raise ValueError(f"Entity {entity_id} not found")
    dest = session.get(Entity, to_kami_id)
    if dest is None:
        raise ValueError(f"Destination kami {to_kami_id} not found")
    if container_id:
        container = session.get(Entity, container_id)
        if container is None:
            raise ValueError(f"Container {container_id} not found")
    scope = resolve_simulation_id(
        session, entity_id, to_kami_id, container_id
    )

    # Close current location
    _close_temporal(session, Location, "entity_id", entity_id, tick)

    # Insert new
    loc = Location(
        simulation_id=scope,
        entity_id=entity_id,
        kami_id=to_kami_id,
        container_id=container_id,
        since_tick=tick,
        valid_until_tick=None,
    )
    session.add(loc)
    session.flush()
    return loc


def place_entity(
    session: Session,
    entity_id: str,
    kami_id: str,
    tick: int,
    container_id: str | None = None,
) -> Location:
    """Initial placement (no prior location required)."""
    entity = session.get(Entity, entity_id)
    kami = session.get(Entity, kami_id)
    if entity is None or kami is None:
        raise ValueError("Entity and destination kami must exist before placement")
    if container_id and session.get(Entity, container_id) is None:
        raise ValueError(f"Container {container_id} not found")
    scope = resolve_simulation_id(session, entity_id, kami_id, container_id)
    loc = Location(
        simulation_id=scope,
        entity_id=entity_id,
        kami_id=kami_id,
        container_id=container_id,
        since_tick=tick,
        valid_until_tick=None,
    )
    session.add(loc)
    session.flush()
    return loc


# --- State operations ---


def change_state(
    session: Session,
    entity_id: str,
    attribute: str,
    new_value: Any,
    tick: int,
    reason_event_id: str | None = None,
) -> PhysicalState:
    """Change a physical state attribute of an entity."""
    entity = session.get(Entity, entity_id)
    if entity is None:
        raise ValueError(f"Entity {entity_id} not found")

    # Close current value for this attribute
    current = (
        session.query(PhysicalState)
        .filter(
            PhysicalState.simulation_id == entity.simulation_id,
            PhysicalState.entity_id == entity_id,
            PhysicalState.attribute == attribute,
            PhysicalState.valid_until_tick.is_(None),
        )
        .first()
    )
    if current:
        # Validate hard transitions
        _validate_state_transition(attribute, current.value, new_value)
        current.valid_until_tick = tick

    state = PhysicalState(
        simulation_id=entity.simulation_id,
        entity_id=entity_id,
        attribute=attribute,
        value=new_value,
        since_tick=tick,
        valid_until_tick=None,
    )
    session.add(state)
    session.flush()
    return state


def _validate_state_transition(attribute: str, old_value: Any, new_value: Any):
    """Validate hard state transitions."""
    # integrity: broken -> intact requires explicit repair event
    if attribute == "integrity":
        if old_value == "broken" and new_value == "intact":
            raise ValueError(
                "Cannot transition integrity from broken to intact without repair event"
            )


def get_state(
    session: Session, entity_id: str, attribute: str | None = None
) -> list[PhysicalState]:
    """Get current physical state(s) of an entity."""
    scope = resolve_simulation_id(session, entity_id)
    q = session.query(PhysicalState).filter(
        PhysicalState.simulation_id == scope,
        PhysicalState.entity_id == entity_id,
        PhysicalState.valid_until_tick.is_(None),
    )
    if attribute:
        q = q.filter(PhysicalState.attribute == attribute)
    return q.all()


# --- Ownership ---


def transfer_ownership(
    session: Session,
    entity_id: str,
    new_owner_id: str,
    tick: int,
    reason_event_id: str | None = None,
) -> Ownership:
    """Transfer ownership of an entity."""
    for eid in (entity_id, new_owner_id):
        entity = session.get(Entity, eid)
        if entity is None:
            raise ValueError(f"Entity {eid} not found")
    scope = resolve_simulation_id(session, entity_id, new_owner_id)

    _close_temporal(session, Ownership, "entity_id", entity_id, tick)

    own = Ownership(
        simulation_id=scope,
        entity_id=entity_id,
        owner_id=new_owner_id,
        since_tick=tick,
        valid_until_tick=None,
    )
    session.add(own)
    session.flush()
    return own


# --- Relations ---


def update_relation(
    session: Session,
    from_entity: str,
    to_entity: str,
    rel_type: str,
    tick: int,
    weight: dict | None = None,
    reason_event_id: str | None = None,
) -> Relation:
    """Create or update a relation between entities."""
    for eid in (from_entity, to_entity):
        if session.get(Entity, eid) is None:
            raise ValueError(f"Entity {eid} not found")
    scope = resolve_simulation_id(session, from_entity, to_entity)

    # Close existing relation of same type
    existing = (
        session.query(Relation)
        .filter(
            Relation.simulation_id == scope,
            Relation.from_entity == from_entity,
            Relation.to_entity == to_entity,
            Relation.rel_type == rel_type,
            Relation.valid_until_tick.is_(None),
        )
        .first()
    )
    if existing:
        existing.valid_until_tick = tick

    rel = Relation(
        simulation_id=scope,
        from_entity=from_entity,
        to_entity=to_entity,
        rel_type=rel_type,
        weight=weight or {},
        since_tick=tick,
        valid_until_tick=None,
    )
    session.add(rel)
    session.flush()
    return rel


def get_relations(
    session: Session,
    entity_id: str,
    rel_type: str | None = None,
    direction: str = "outgoing",
) -> list[Relation]:
    """Get current relations for an entity."""
    scope = resolve_simulation_id(session, entity_id)
    if direction == "outgoing":
        q = session.query(Relation).filter(
            Relation.simulation_id == scope,
            Relation.from_entity == entity_id,
            Relation.valid_until_tick.is_(None),
        )
    elif direction == "incoming":
        q = session.query(Relation).filter(
            Relation.simulation_id == scope,
            Relation.to_entity == entity_id,
            Relation.valid_until_tick.is_(None),
        )
    else:  # both
        q = session.query(Relation).filter(
            Relation.simulation_id == scope,
            (Relation.from_entity == entity_id) | (Relation.to_entity == entity_id),
            Relation.valid_until_tick.is_(None),
        )
    if rel_type:
        q = q.filter(Relation.rel_type == rel_type)
    return q.all()


# --- Events ---


def emit_event(
    session: Session,
    tick: int,
    kami_id: str | None,
    event_type: str,
    participants: list[str] | None = None,
    payload: dict | None = None,
    salience: float = 0.5,
    narrative: str = "",
    causes: list[str] | None = None,
    event_id: str | None = None,
    simulation_id: str | None = None,
) -> Event:
    """Emit an event to the log."""
    eid = event_id or _gen_id("evt_")
    scope = resolve_simulation_id(
        session,
        kami_id,
        *(participants or []),
        explicit=simulation_id,
    )
    event = Event(
        event_id=eid,
        simulation_id=scope,
        tick=tick,
        kami_id=kami_id,
        event_type=event_type,
        participants=participants or [],
        payload=payload or {},
        salience=salience,
        narrative=narrative,
        causes=causes or [],
    )
    session.add(event)
    session.flush()
    return event


def get_events(
    session: Session,
    kami_id: str | None = None,
    since_tick: int | None = None,
    until_tick: int | None = None,
    limit: int = 20,
    simulation_id: str | None = None,
) -> list[Event]:
    """Query events with optional filters."""
    q = session.query(Event)
    scope = simulation_id
    if scope is None and kami_id:
        scope = resolve_simulation_id(session, kami_id)
    if scope is not None:
        q = q.filter(Event.simulation_id == scope)
    if kami_id:
        q = q.filter(Event.kami_id == kami_id)
    if since_tick is not None:
        q = q.filter(Event.tick >= since_tick)
    if until_tick is not None:
        q = q.filter(Event.tick <= until_tick)
    return q.order_by(Event.tick.desc()).limit(limit).all()


# --- Query helpers ---


def query_kami_state(session: Session, kami_id: str) -> dict:
    """Get full state snapshot for a kami: entities, states, relations."""
    # All entities currently in this kami
    scope = resolve_simulation_id(session, kami_id)
    locations = (
        session.query(Location)
        .filter(
            Location.simulation_id == scope,
            Location.kami_id == kami_id,
            Location.valid_until_tick.is_(None),
        )
        .all()
    )
    entity_ids = [loc.entity_id for loc in locations]
    entities = []
    for eid in entity_ids:
        entity = session.get(Entity, eid)
        if entity:
            states = get_state(session, eid)
            entities.append({
                "entity_id": entity.entity_id,
                "kind": entity.kind,
                "name": entity.canonical_name,
                "archetype": entity.archetype,
                "states": {s.attribute: s.value for s in states},
            })

    kami_entity = session.get(Entity, kami_id)
    kami_states = get_state(session, kami_id) if kami_entity else []

    return {
        "kami_id": kami_id,
        "name": kami_entity.canonical_name if kami_entity else kami_id,
        "archetype": kami_entity.archetype if kami_entity else {},
        "states": {s.attribute: s.value for s in kami_states},
        "entities": entities,
        "entity_count": len(entities),
    }


def get_entities_in_kami(session: Session, kami_id: str) -> list[Entity]:
    """Get all entities currently located in a kami."""
    scope = resolve_simulation_id(session, kami_id)
    locations = (
        session.query(Location)
        .filter(
            Location.simulation_id == scope,
            Location.kami_id == kami_id,
            Location.valid_until_tick.is_(None),
        )
        .all()
    )
    entity_ids = [loc.entity_id for loc in locations]
    return [session.get(Entity, eid) for eid in entity_ids if session.get(Entity, eid)]


def get_agents_in_kami(session: Session, kami_id: str) -> list[Entity]:
    """Get all agents currently in a kami."""
    return [e for e in get_entities_in_kami(session, kami_id) if e.kind == "agent"]


# --- Agent beliefs ---


def update_belief(
    session: Session,
    agent_id: str,
    kind: str,
    tick: int,
    target_entity: str | None = None,
    attribute: str | None = None,
    believed_value: Any = None,
    confidence: float = 0.8,
    source_event_id: str | None = None,
) -> AgentBelief:
    """Update or create an agent's subjective belief."""
    agent = session.get(Entity, agent_id)
    if agent is None:
        raise ValueError(f"Agent {agent_id} not found")
    scope = resolve_simulation_id(session, agent_id, target_entity)
    belief = AgentBelief(
        belief_id=_gen_id("blf_"),
        simulation_id=scope,
        agent_id=agent_id,
        kind=kind,
        target_entity=target_entity,
        attribute=attribute,
        believed_value=believed_value,
        confidence=confidence,
        since_tick=tick,
        source_event_id=source_event_id,
    )
    session.add(belief)
    session.flush()
    return belief


def get_beliefs(
    session: Session, agent_id: str, kind: str | None = None
) -> list[AgentBelief]:
    """Get an agent's current beliefs."""
    scope = resolve_simulation_id(session, agent_id)
    q = session.query(AgentBelief).filter(
        AgentBelief.simulation_id == scope,
        AgentBelief.agent_id == agent_id,
    )
    if kind:
        q = q.filter(AgentBelief.kind == kind)
    return q.all()


# --- Embodiment, intent memory, and live scenes ---


DEFAULT_NEEDS = {
    "fatigue": 0.0,
    "hunger": 0.0,
    "stress": 0.15,
    "social": 0.35,
    "task_pressure": 0.4,
}


def set_agent_need(
    session: Session,
    agent_id: str,
    need: str,
    value: float,
    tick: int,
) -> AgentNeed:
    agent = session.get(Entity, agent_id)
    if agent is None:
        raise ValueError(f"Agent {agent_id} not found")
    value = max(0.0, min(1.0, float(value)))
    existing = (
        session.query(AgentNeed)
        .filter(
            AgentNeed.simulation_id == agent.simulation_id,
            AgentNeed.agent_id == agent_id,
            AgentNeed.need == need,
            AgentNeed.valid_until_tick.is_(None),
        )
        .first()
    )
    if existing:
        existing.valid_until_tick = tick
    row = AgentNeed(
        simulation_id=agent.simulation_id,
        agent_id=agent_id,
        need=need,
        value=value,
        since_tick=tick,
    )
    session.add(row)
    session.flush()
    return row


def get_agent_needs(session: Session, agent_id: str) -> dict[str, float]:
    scope = resolve_simulation_id(session, agent_id)
    rows = (
        session.query(AgentNeed)
        .filter(
            AgentNeed.simulation_id == scope,
            AgentNeed.agent_id == agent_id,
            AgentNeed.valid_until_tick.is_(None),
        )
        .all()
    )
    values = dict(DEFAULT_NEEDS)
    values.update({row.need: row.value for row in rows})
    return values


def advance_agent_needs(
    session: Session,
    agent_id: str,
    tick: int,
    deltas: dict[str, float] | None = None,
) -> dict[str, float]:
    values = get_agent_needs(session, agent_id)
    baseline = {
        "fatigue": 0.015,
        "hunger": 0.008,
        "stress": 0.0,
        "social": 0.002,
        "task_pressure": 0.004,
    }
    if deltas:
        baseline.update({k: baseline.get(k, 0.0) + float(v) for k, v in deltas.items()})
    for need, delta in baseline.items():
        set_agent_need(session, agent_id, need, values.get(need, 0.0) + delta, tick)
    return get_agent_needs(session, agent_id)


def record_agent_intent(
    session: Session,
    tick: int,
    agent_id: str,
    kami_id: str | None,
    action_type: str,
    target: str | None = None,
    params: dict | None = None,
    salience: float = 0.3,
    intent_id: str | None = None,
    pressure: dict | None = None,
) -> AgentIntentRecord:
    agent = session.get(Entity, agent_id)
    if agent is None:
        raise ValueError(f"Agent {agent_id} not found")
    scope = resolve_simulation_id(session, agent_id, kami_id)
    row = AgentIntentRecord(
        intent_id=intent_id or _gen_id("int_"),
        simulation_id=scope,
        tick=tick,
        agent_id=agent_id,
        kami_id=kami_id,
        action_type=action_type,
        target=target,
        params=params or {},
        salience=float(salience),
        pressure=pressure or {},
    )
    session.add(row)
    session.flush()
    return row


def get_recent_intents(
    session: Session,
    agent_id: str | None = None,
    kami_id: str | None = None,
    limit: int = 8,
) -> list[AgentIntentRecord]:
    q = session.query(AgentIntentRecord)
    scope = resolve_simulation_id(session, agent_id, kami_id)
    q = q.filter(AgentIntentRecord.simulation_id == scope)
    if agent_id:
        q = q.filter(AgentIntentRecord.agent_id == agent_id)
    if kami_id:
        q = q.filter(AgentIntentRecord.kami_id == kami_id)
    return q.order_by(AgentIntentRecord.tick.desc()).limit(limit).all()


def mark_intent_result(
    session: Session,
    intent_id: str,
    status: str,
    result_summary: str = "",
    result_event_id: str | None = None,
    blockers: list | None = None,
) -> AgentIntentRecord | None:
    row = session.get(AgentIntentRecord, intent_id)
    if not row:
        return None
    row.status = status
    row.result_summary = result_summary
    row.result_event_id = result_event_id
    row.blockers = blockers or []
    session.flush()
    return row


def settle_tick_intents(
    session: Session,
    tick: int,
    event_id: str,
    participants: list[str],
    narrative: str,
) -> None:
    event = session.get(Event, event_id)
    scope = event.simulation_id if event is not None else resolve_simulation_id(
        session, *(participants or [])
    )
    rows = session.query(AgentIntentRecord).filter(
        AgentIntentRecord.simulation_id == scope,
        AgentIntentRecord.tick == tick,
    ).all()
    participant_set = set(participants or [])
    for row in rows:
        if row.status != "pending":
            continue
        if row.agent_id in participant_set:
            row.status = "resolved"
            row.result_event_id = event_id
            row.result_summary = narrative[:500]
        else:
            row.status = "stalled"
            row.result_summary = "No visible outcome this tick."
    session.flush()


def upsert_conversation_thread(
    session: Session,
    tick: int,
    kami_id: str | None,
    participants: list[str],
    topic: str,
    summary: str,
    status: str = "active",
    tension: float = 0.0,
    momentum: float = 0.5,
    open_question: str | None = None,
    thread_id: str | None = None,
    last_event_id: str | None = None,
) -> ConversationThread:
    scope = resolve_simulation_id(session, kami_id, *(participants or []))
    row = session.get(ConversationThread, thread_id) if thread_id else None
    if row is not None and row.simulation_id != scope:
        raise ValueError("Conversation thread belongs to another simulation")
    if row is None:
        participant_set = set(participants or [])
        active = (
            session.query(ConversationThread)
            .filter(
                ConversationThread.simulation_id == scope,
                ConversationThread.kami_id == kami_id,
                ConversationThread.status == "active",
            )
            .all()
        )
        for candidate in active:
            if set(candidate.participants or []) == participant_set:
                row = candidate
                break
    if row is None:
        row = ConversationThread(
            thread_id=thread_id or _gen_id("thr_"),
            simulation_id=scope,
            kami_id=kami_id,
            participants=participants or [],
            topic=topic or "unfinished exchange",
            created_at_tick=tick,
        )
        session.add(row)

    row.kami_id = kami_id
    row.participants = participants or row.participants or []
    row.topic = topic or row.topic
    row.status = status
    row.tension = max(0.0, min(1.0, float(tension)))
    row.momentum = max(0.0, min(1.0, float(momentum)))
    row.summary = summary or row.summary
    row.open_question = open_question
    row.last_tick = tick
    row.last_event_id = last_event_id or row.last_event_id
    session.flush()
    return row


def get_active_conversations(
    session: Session,
    kami_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 5,
) -> list[ConversationThread]:
    scope = resolve_simulation_id(session, kami_id, agent_id)
    q = session.query(ConversationThread).filter(
        ConversationThread.simulation_id == scope,
        ConversationThread.status == "active",
    )
    if kami_id:
        q = q.filter(ConversationThread.kami_id == kami_id)
    rows = q.order_by(ConversationThread.last_tick.desc()).limit(limit * 3).all()
    if agent_id:
        rows = [row for row in rows if agent_id in (row.participants or [])]
    return rows[:limit]


# --- Schedules ---


def create_schedule(
    session: Session,
    fires_at_tick: int,
    kami_id: str,
    event_template: dict,
) -> Schedule:
    scope = resolve_simulation_id(session, kami_id)
    sched = Schedule(
        schedule_id=_gen_id("sched_"),
        simulation_id=scope,
        fires_at_tick=fires_at_tick,
        kami_id=kami_id,
        event_template=event_template,
    )
    session.add(sched)
    session.flush()
    return sched


def get_due_schedules(
    session: Session,
    tick: int,
    simulation_id: str | None = None,
) -> list[Schedule]:
    query = session.query(Schedule).filter(Schedule.fires_at_tick == tick)
    if simulation_id is not None:
        query = query.filter(Schedule.simulation_id == simulation_id)
    return query.all()
