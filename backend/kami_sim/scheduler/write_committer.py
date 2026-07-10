"""Apply scene proposals to FactStore in a deterministic order."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..eventbus.bus import EventBus
from ..factstore import tools as fs
from ..spatial.graph import SpatialGraph

logger = logging.getLogger(__name__)


@dataclass
class StagedProposals:
    """Database changes staged in the caller's open tick transaction."""

    events: list[dict] = field(default_factory=list)
    failed_mutations: list[dict] = field(default_factory=list)
    accepted: list[tuple[dict, str | None]] = field(default_factory=list)


def compute_initiative(agent_id: str, tick: int, fatigue: float = 0.0) -> float:
    """Compute a deterministic initiative score for action ordering."""
    digest = hashlib.sha256(f"{agent_id}:{tick}".encode()).hexdigest()
    hash_factor = int(digest[:8], 16) / 0xFFFFFFFF
    return (1.0 - fatigue) * hash_factor


def commit_proposals(
    session: Session,
    tick: int,
    proposals: list[dict],
    event_bus: EventBus,
    spatial_graph: SpatialGraph,
) -> tuple[list[dict], list[dict]]:
    """Compatibility wrapper for callers that own no outer tick transaction."""
    staged = stage_proposals(session, tick, proposals, spatial_graph)
    session.commit()
    try:
        publish_staged_broadcasts(staged, tick, event_bus, spatial_graph)
    except Exception:
        logger.exception("Post-commit broadcast publication failed for tick %s", tick)
    return staged.events, staged.failed_mutations


def stage_proposals(
    session: Session,
    tick: int,
    proposals: list[dict],
    spatial_graph: SpatialGraph,
) -> StagedProposals:
    """Apply accepted scenes without committing or publishing side effects."""
    _ensure_physical_transaction(session)
    staged = StagedProposals()

    for proposal in sorted(proposals, key=lambda item: item.get("kami_id") or ""):
        kami_id = proposal.get("kami_id")
        proposal_events: list[dict] = []

        try:
            # A scene is atomic: reject its narrative if any mutation fails.
            with session.begin_nested():
                for mutation in proposal.get("mutations", []):
                    try:
                        _apply_mutation(session, tick, mutation, spatial_graph)
                    except Exception as exc:
                        staged.failed_mutations.append({
                            "mutation": mutation,
                            "error": str(exc),
                            "kami_id": kami_id,
                        })
                        raise

                for event_data in proposal.get("events", []):
                    event = fs.emit_event(
                        session,
                        tick=tick,
                        kami_id=event_data.get("kami_id") or kami_id,
                        event_type=event_data["event_type"],
                        participants=event_data.get("participants", []),
                        payload=event_data.get("payload", {}),
                        salience=event_data.get("salience", 0.3),
                        narrative=event_data.get("narrative", ""),
                        causes=event_data.get("causes", []),
                    )
                    proposal_events.append({
                        "event_id": event.event_id,
                        "kami_id": event.kami_id,
                        "event_type": event.event_type,
                        "narrative": event.narrative,
                        "salience": event.salience,
                        "participants": event.participants,
                        "payload": event.payload,
                        "causes": event.causes,
                    })
                    fs.settle_tick_intents(
                        session,
                        tick=tick,
                        event_id=event.event_id,
                        participants=event.participants or [],
                        narrative=event.narrative or "",
                    )
        except Exception as exc:
            logger.warning("Proposal rejected in %s: %s", kami_id, exc)
            if (
                not staged.failed_mutations
                or staged.failed_mutations[-1].get("kami_id") != kami_id
            ):
                staged.failed_mutations.append({
                    "mutation": None,
                    "error": str(exc),
                    "kami_id": kami_id,
                })
            continue

        staged.events.extend(proposal_events)
        staged.accepted.append((proposal, kami_id))

    return staged


def _ensure_physical_transaction(session: Session) -> None:
    """Make SQLite savepoints participate in the caller's outer transaction."""
    connection = session.connection()
    if connection.dialect.name != "sqlite":
        return
    driver_connection = connection.connection.driver_connection
    if not driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def publish_staged_broadcasts(
    staged: StagedProposals,
    tick: int,
    event_bus: EventBus,
    spatial_graph: SpatialGraph,
) -> None:
    """Publish ephemeral broadcasts only after the database commit succeeds."""
    for proposal, kami_id in staged.accepted:
        _publish_broadcasts(proposal, kami_id, tick, event_bus, spatial_graph)


def _publish_broadcasts(
    proposal: dict,
    kami_id: str | None,
    tick: int,
    event_bus: EventBus,
    spatial_graph: SpatialGraph,
) -> None:
    if not kami_id:
        return

    for broadcast in proposal.get("broadcasts", []):
        neighbors = spatial_graph.get_neighbors(kami_id)
        attenuation_map = {}
        for neighbor_id in neighbors:
            edge = spatial_graph.get_edge_data(kami_id, neighbor_id)
            if edge:
                attenuation_map[neighbor_id] = edge.get("audio_attenuation", 0.2)
        event_bus.publish_broadcast(
            source_kami_id=kami_id,
            text=broadcast["text"],
            salience=broadcast.get("salience", 0.3),
            current_tick=tick,
            neighbor_kami_ids=neighbors,
            attenuation_map=attenuation_map,
        )


def _resolve_kami_id(
    session: Session, kami_id_raw: str, spatial_graph: SpatialGraph
) -> str:
    """Try to resolve a possibly hallucinated Kami ID to a real one."""
    if session.get(fs.Entity, kami_id_raw):
        return kami_id_raw

    all_kami = spatial_graph.all_kami_ids()
    raw_lower = kami_id_raw.lower().replace(" ", "_").replace("-", "_")
    candidates = [raw_lower, f"kami_{raw_lower}"]
    for candidate in candidates:
        for real_id in all_kami:
            if candidate == real_id.lower():
                return real_id
    for real_id in all_kami:
        if raw_lower in real_id.lower() or real_id.lower() in raw_lower:
            return real_id
    return kami_id_raw


def _apply_mutation(
    session: Session,
    tick: int,
    mutation: dict,
    spatial_graph: SpatialGraph | None = None,
) -> None:
    """Apply one validated mutation to FactStore."""
    mutation_type = mutation["type"]

    if mutation_type == "move_entity":
        to_kami = mutation["to_kami_id"]
        if spatial_graph:
            to_kami = _resolve_kami_id(session, to_kami, spatial_graph)
        fs.move_entity(
            session,
            entity_id=mutation["entity_id"],
            to_kami_id=to_kami,
            tick=tick,
        )
    elif mutation_type == "change_state":
        fs.change_state(
            session,
            entity_id=mutation["entity_id"],
            attribute=mutation["attribute"],
            new_value=mutation["new_value"],
            tick=tick,
        )
    elif mutation_type == "update_relation":
        weight = mutation.get("weight")
        if weight and not isinstance(weight, dict):
            weight = {"value": weight}
        fs.update_relation(
            session,
            from_entity=mutation["from_entity"],
            to_entity=mutation["to_entity"],
            rel_type=mutation["rel_type"],
            tick=tick,
            weight=weight,
        )
    elif mutation_type == "create_entity":
        entity = fs.create_entity(
            session,
            kind=mutation["kind"],
            canonical_name=mutation["canonical_name"],
            tick=tick,
            archetype=mutation.get("archetype"),
            kami_id=mutation.get("kami_id"),
        )
        if mutation.get("kami_id"):
            fs.place_entity(session, entity.entity_id, mutation["kami_id"], tick)
    elif mutation_type == "transfer_ownership":
        fs.transfer_ownership(
            session,
            entity_id=mutation["entity_id"],
            new_owner_id=mutation["new_owner_id"],
            tick=tick,
        )
    elif mutation_type == "record_intent_result":
        fs.mark_intent_result(
            session,
            intent_id=mutation["intent_id"],
            status=mutation.get("status", "resolved"),
            result_summary=mutation.get("summary", ""),
            blockers=mutation.get("blockers", []),
        )
    elif mutation_type == "update_conversation_thread":
        fs.upsert_conversation_thread(
            session,
            tick=tick,
            kami_id=mutation.get("kami_id"),
            participants=mutation.get("participants", []),
            topic=mutation.get("topic", "unfinished exchange"),
            summary=mutation.get("summary", ""),
            status=mutation.get("status", "active"),
            tension=mutation.get("tension", 0.0),
            momentum=mutation.get("momentum", 0.5),
            open_question=mutation.get("open_question"),
            thread_id=mutation.get("thread_id"),
            last_event_id=mutation.get("last_event_id"),
        )
    elif mutation_type == "adjust_need":
        current = fs.get_agent_needs(session, mutation["agent_id"])
        need = mutation["need"]
        value = mutation.get("value")
        if value is None:
            value = current.get(need, 0.0) + mutation.get("delta", 0.0)
        fs.set_agent_need(session, mutation["agent_id"], need, value, tick)
    else:
        raise ValueError(f"Unknown mutation type: {mutation_type}")
