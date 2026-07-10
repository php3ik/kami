"""Two-phase durable movement between adjacent kami."""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..factstore import tools as fs
from ..determinism import generate_id
from ..factstore.models import Entity, Event, TransitJourney
from .graph import SpatialGraph


ACTIVE_TRANSIT_STATUSES = {"scheduled", "in_transit"}


def transit_entity_id(simulation_id: str) -> str:
    return f"sim_{simulation_id}__kami_transit"


def ensure_transit_entity(
    session: Session, simulation_id: str, tick: int
) -> Entity:
    entity_id = transit_entity_id(simulation_id)
    entity = session.get(Entity, entity_id)
    if entity is None:
        entity = Entity(
            entity_id=entity_id,
            simulation_id=simulation_id,
            kind="kami_transit",
            canonical_name="In transit",
            created_at_tick=tick,
            archetype={
                "description": "A non-rendered system location used while travelling.",
                "system": True,
            },
        )
        session.add(entity)
        session.flush()
    elif entity.simulation_id != simulation_id:
        raise ValueError("Transit entity belongs to another simulation")
    return entity


def begin_transit(
    session: Session,
    entity_id: str,
    to_kami_id: str,
    tick: int,
    spatial_graph: SpatialGraph,
    *,
    reason: str = "",
    intent_id: str | None = None,
) -> TransitJourney:
    """Schedule departure at N+1 and arrival at N+2."""
    entity = session.get(Entity, entity_id)
    destination = session.get(Entity, to_kami_id)
    if entity is None or entity.kind != "agent":
        raise ValueError("Only agents use inter-kami transit")
    if destination is None or destination.kind != "kami":
        raise ValueError(f"Destination kami {to_kami_id} not found")
    simulation_id = fs.resolve_simulation_id(session, entity_id, to_kami_id)
    location = fs.get_current_location(session, entity_id)
    if location is None:
        raise ValueError(f"Agent {entity_id} has no current location")
    if location.kami_id == to_kami_id:
        raise ValueError("Agent is already at the destination")
    if location.kami_id == transit_entity_id(simulation_id):
        raise ValueError("Agent is already in transit")
    if to_kami_id not in spatial_graph.get_neighbors(location.kami_id):
        raise ValueError("Transit destination is not adjacent to current kami")
    existing = session.query(TransitJourney).filter(
        TransitJourney.simulation_id == simulation_id,
        TransitJourney.entity_id == entity_id,
        TransitJourney.status.in_(ACTIVE_TRANSIT_STATUSES),
    ).one_or_none()
    if existing is not None:
        raise ValueError(f"Agent already has active journey {existing.journey_id}")
    ensure_transit_entity(session, simulation_id, tick)
    journey = TransitJourney(
        journey_id=generate_id("journey_"),
        simulation_id=simulation_id,
        entity_id=entity_id,
        from_kami_id=location.kami_id,
        to_kami_id=to_kami_id,
        requested_at_tick=tick,
        depart_at_tick=tick + 1,
        arrive_at_tick=tick + 2,
        status="scheduled",
        metadata_={"reason": reason, "intent_id": intent_id},
    )
    session.add(journey)
    session.flush()
    return journey


def advance_transit(
    session: Session, simulation_id: str, tick: int
) -> tuple[list[dict], list[dict]]:
    """Advance at most one phase per journey and return event/result payloads."""
    event_rows: list[Event] = []
    transitions: list[dict] = []

    arrivals = session.query(TransitJourney).filter(
        TransitJourney.simulation_id == simulation_id,
        TransitJourney.status == "in_transit",
        TransitJourney.arrive_at_tick <= tick,
    ).order_by(TransitJourney.arrive_at_tick, TransitJourney.journey_id).all()
    for journey in arrivals:
        fs.move_entity(session, journey.entity_id, journey.to_kami_id, tick)
        journey.status = "arrived"
        event_rows.append(fs.emit_event(
            session,
            tick,
            journey.to_kami_id,
            "transit_arrival",
            participants=[journey.entity_id],
            payload={
                "journey_id": journey.journey_id,
                "from_kami_id": journey.from_kami_id,
                "to_kami_id": journey.to_kami_id,
            },
            salience=0.45,
            narrative=f"{_entity_name(session, journey.entity_id)} arrives from {journey.from_kami_id}.",
        ))
        transitions.append(_journey_payload(journey, "arrived"))

    departures = session.query(TransitJourney).filter(
        TransitJourney.simulation_id == simulation_id,
        TransitJourney.status == "scheduled",
        TransitJourney.depart_at_tick <= tick,
    ).order_by(TransitJourney.depart_at_tick, TransitJourney.journey_id).all()
    transit_id = transit_entity_id(simulation_id)
    for journey in departures:
        ensure_transit_entity(session, simulation_id, tick)
        fs.move_entity(session, journey.entity_id, transit_id, tick)
        journey.status = "in_transit"
        event_rows.append(fs.emit_event(
            session,
            tick,
            journey.from_kami_id,
            "transit_departure",
            participants=[journey.entity_id],
            payload={
                "journey_id": journey.journey_id,
                "from_kami_id": journey.from_kami_id,
                "to_kami_id": journey.to_kami_id,
            },
            salience=0.4,
            narrative=f"{_entity_name(session, journey.entity_id)} leaves for {journey.to_kami_id}.",
        ))
        transitions.append(_journey_payload(journey, "departed"))

    return [_event_payload(event) for event in event_rows], transitions


def has_due_transit(session: Session, simulation_id: str, tick: int) -> bool:
    return session.query(TransitJourney).filter(
        TransitJourney.simulation_id == simulation_id,
        or_(
            (
                (TransitJourney.status == "scheduled")
                & (TransitJourney.depart_at_tick <= tick)
            ),
            (
                (TransitJourney.status == "in_transit")
                & (TransitJourney.arrive_at_tick <= tick)
            ),
        ),
    ).first() is not None


def next_transit_tick(
    session: Session, simulation_id: str, after_tick: int
) -> int | None:
    candidates = []
    for journey in session.query(TransitJourney).filter(
        TransitJourney.simulation_id == simulation_id,
        TransitJourney.status.in_(ACTIVE_TRANSIT_STATUSES),
    ):
        candidate = (
            journey.depart_at_tick
            if journey.status == "scheduled"
            else journey.arrive_at_tick
        )
        if candidate > after_tick:
            candidates.append(candidate)
        elif candidate <= after_tick:
            return after_tick
    return min(candidates) if candidates else None


def get_agent_transit(
    session: Session,
    agent_id: str,
    *,
    include_completed: bool = True,
    at_tick: int | None = None,
) -> TransitJourney | None:
    simulation_id = fs.resolve_simulation_id(session, agent_id)
    query = session.query(TransitJourney).filter(
        TransitJourney.simulation_id == simulation_id,
        TransitJourney.entity_id == agent_id,
    )
    if not include_completed:
        query = query.filter(TransitJourney.status.in_(ACTIVE_TRANSIT_STATUSES))
    if at_tick is not None:
        query = query.filter(TransitJourney.requested_at_tick <= at_tick)
    return query.order_by(
        TransitJourney.requested_at_tick.desc(), TransitJourney.journey_id.desc()
    ).first()


def _event_payload(event: Event) -> dict:
    return {
        "event_id": event.event_id,
        "tick": event.tick,
        "kami_id": event.kami_id,
        "event_type": event.event_type,
        "narrative": event.narrative,
        "salience": event.salience,
        "participants": event.participants,
        "payload": event.payload,
        "causes": event.causes,
    }


def _journey_payload(journey: TransitJourney, transition: str) -> dict:
    return {
        "journey_id": journey.journey_id,
        "entity_id": journey.entity_id,
        "from_kami_id": journey.from_kami_id,
        "to_kami_id": journey.to_kami_id,
        "status": journey.status,
        "transition": transition,
        "depart_at_tick": journey.depart_at_tick,
        "arrive_at_tick": journey.arrive_at_tick,
    }


def _entity_name(session: Session, entity_id: str) -> str:
    entity = session.get(Entity, entity_id)
    return entity.canonical_name if entity else entity_id
