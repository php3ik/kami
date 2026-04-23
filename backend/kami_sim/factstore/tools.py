"""FactStore tool functions — validated mutation layer (spec §2.2, §2.3).

All world mutations go through these functions. No free-text mutation allowed.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from .models import (
    VALID_ATTRIBUTES,
    VALID_REL_TYPES,
    AgentBelief,
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
    return f"{prefix}{uuid.uuid4().hex[:12]}"


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
    entity = Entity(
        entity_id=eid,
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
    return (
        session.query(Location)
        .filter(
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

    # Close current location
    _close_temporal(session, Location, "entity_id", entity_id, tick)

    # Insert new
    loc = Location(
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
    loc = Location(
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
    q = session.query(PhysicalState).filter(
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
        if session.get(Entity, eid) is None:
            raise ValueError(f"Entity {eid} not found")

    _close_temporal(session, Ownership, "entity_id", entity_id, tick)

    own = Ownership(
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

    # Close existing relation of same type
    existing = (
        session.query(Relation)
        .filter(
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
    if direction == "outgoing":
        q = session.query(Relation).filter(
            Relation.from_entity == entity_id,
            Relation.valid_until_tick.is_(None),
        )
    elif direction == "incoming":
        q = session.query(Relation).filter(
            Relation.to_entity == entity_id,
            Relation.valid_until_tick.is_(None),
        )
    else:  # both
        q = session.query(Relation).filter(
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
) -> Event:
    """Emit an event to the log."""
    eid = event_id or _gen_id("evt_")
    event = Event(
        event_id=eid,
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
) -> list[Event]:
    """Query events with optional filters."""
    q = session.query(Event)
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
    locations = (
        session.query(Location)
        .filter(Location.kami_id == kami_id, Location.valid_until_tick.is_(None))
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
    locations = (
        session.query(Location)
        .filter(Location.kami_id == kami_id, Location.valid_until_tick.is_(None))
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
    belief = AgentBelief(
        belief_id=_gen_id("blf_"),
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
    q = session.query(AgentBelief).filter(AgentBelief.agent_id == agent_id)
    if kind:
        q = q.filter(AgentBelief.kind == kind)
    return q.all()


# --- Schedules ---


def create_schedule(
    session: Session,
    fires_at_tick: int,
    kami_id: str,
    event_template: dict,
) -> Schedule:
    sched = Schedule(
        schedule_id=_gen_id("sched_"),
        fires_at_tick=fires_at_tick,
        kami_id=kami_id,
        event_template=event_template,
    )
    session.add(sched)
    session.flush()
    return sched


def get_due_schedules(session: Session, tick: int) -> list[Schedule]:
    return session.query(Schedule).filter(Schedule.fires_at_tick == tick).all()
