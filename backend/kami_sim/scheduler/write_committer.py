"""WriteCommitter — single-threaded mutation applier (spec §2.5).

Consumes propose-lists and applies them in deterministic order.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..eventbus.bus import EventBus
from ..factstore import tools as fs
from ..spatial.graph import SpatialGraph

logger = logging.getLogger(__name__)


def compute_initiative(agent_id: str, tick: int, fatigue: float = 0.0) -> float:
    """Compute initiative for action ordering.

    Function of reaction speed * (1 - fatigue) * surprise.
    Hash-based tiebreak for determinism.
    """
    # Deterministic hash for tiebreaking
    h = hashlib.sha256(f"{agent_id}:{tick}".encode()).hexdigest()
    hash_factor = int(h[:8], 16) / 0xFFFFFFFF
    return (1.0 - fatigue) * hash_factor


def commit_proposals(
    session: Session,
    tick: int,
    proposals: list[dict],
    event_bus: EventBus,
    spatial_graph: SpatialGraph,
) -> list[dict]:
    """Apply all proposals from all kami in deterministic order.

    Returns list of committed events.
    """
    committed_events = []
    failed_mutations = []

    for proposal in proposals:
        kami_id = proposal.get("kami_id")

        # Apply mutations
        for mutation in proposal.get("mutations", []):
            try:
                _apply_mutation(session, tick, mutation, spatial_graph)
            except Exception as e:
                logger.warning(f"Mutation failed in {kami_id}: {mutation['type']} — {e}")
                failed_mutations.append({
                    "mutation": mutation,
                    "error": str(e),
                    "kami_id": kami_id,
                })

        # Commit events
        for event_data in proposal.get("events", []):
            try:
                event = fs.emit_event(
                    session,
                    tick=tick,
                    kami_id=event_data.get("kami_id"),
                    event_type=event_data["event_type"],
                    participants=event_data.get("participants", []),
                    payload=event_data.get("payload", {}),
                    salience=event_data.get("salience", 0.3),
                    narrative=event_data.get("narrative", ""),
                )
                committed_events.append({
                    "event_id": event.event_id,
                    "kami_id": event.kami_id,
                    "event_type": event.event_type,
                    "narrative": event.narrative,
                    "salience": event.salience,
                })
            except Exception as e:
                logger.error(f"Event emit failed: {e}")

        # Propagate broadcasts to neighbors
        for broadcast in proposal.get("broadcasts", []):
            if kami_id:
                neighbors = spatial_graph.get_neighbors(kami_id)
                attenuation_map = {}
                for n in neighbors:
                    edge = spatial_graph.get_edge_data(kami_id, n)
                    if edge:
                        attenuation_map[n] = edge.get("audio_attenuation", 0.2)
                event_bus.publish_broadcast(
                    source_kami_id=kami_id,
                    text=broadcast["text"],
                    salience=broadcast.get("salience", 0.3),
                    current_tick=tick,
                    neighbor_kami_ids=neighbors,
                    attenuation_map=attenuation_map,
                )

    session.commit()
    return committed_events


def _resolve_kami_id(session: Session, kami_id_raw: str, spatial_graph: SpatialGraph) -> str:
    """Try to resolve a possibly-hallucinated kami ID to a real one."""
    # Exact match
    if session.get(fs.Entity, kami_id_raw):
        return kami_id_raw
    # Try common hallucination patterns: spaces, missing prefix, wrong separators
    all_kami = spatial_graph.all_kami_ids()
    raw_lower = kami_id_raw.lower().replace(" ", "_").replace("-", "_")
    # Add kami_ prefix if missing
    candidates = [raw_lower, f"kami_{raw_lower}"]
    for candidate in candidates:
        for real_id in all_kami:
            if candidate == real_id.lower():
                return real_id
    # Substring match: if the hallucinated ID is contained in a real one or vice versa
    for real_id in all_kami:
        if raw_lower in real_id.lower() or real_id.lower() in raw_lower:
            return real_id
    # No match found
    return kami_id_raw


def _apply_mutation(session: Session, tick: int, mutation: dict, spatial_graph: SpatialGraph | None = None):
    """Apply a single mutation to FactStore."""
    mtype = mutation["type"]

    if mtype == "move_entity":
        to_kami = mutation["to_kami_id"]
        if spatial_graph:
            to_kami = _resolve_kami_id(session, to_kami, spatial_graph)
        fs.move_entity(
            session,
            entity_id=mutation["entity_id"],
            to_kami_id=to_kami,
            tick=tick,
        )
    elif mtype == "change_state":
        fs.change_state(
            session,
            entity_id=mutation["entity_id"],
            attribute=mutation["attribute"],
            new_value=mutation["new_value"],
            tick=tick,
        )
    elif mtype == "update_relation":
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
    elif mtype == "create_entity":
        entity = fs.create_entity(
            session,
            kind=mutation["kind"],
            canonical_name=mutation["canonical_name"],
            tick=tick,
            archetype=mutation.get("archetype"),
            kami_id=mutation.get("kami_id"),
        )
        # Place it in the kami
        if mutation.get("kami_id"):
            fs.place_entity(session, entity.entity_id, mutation["kami_id"], tick)
    elif mtype == "transfer_ownership":
        fs.transfer_ownership(
            session,
            entity_id=mutation["entity_id"],
            new_owner_id=mutation["new_owner_id"],
            tick=tick,
        )
    else:
        logger.warning(f"Unknown mutation type: {mtype}")
