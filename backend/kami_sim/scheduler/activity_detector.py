"""Activity detector — determines which kami are active each tick (spec §2.5)."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy.orm import Session

from ..comms.channels import get_forced_wake_agents
from ..config import config
from ..eventbus.bus import EventBus
from ..factstore import tools as fs
from ..factstore.models import Entity, Location


def detect_active_kami(
    session: Session,
    event_bus: EventBus,
    tick: int,
    all_kami_ids: list[str],
) -> set[str]:
    """Determine which kami need rendering this tick."""
    active = set()
    simulation_id = fs.resolve_simulation_id(
        session, all_kami_ids[0] if all_kami_ids else None
    )
    due_kami_ids = {
        schedule.kami_id
        for schedule in fs.get_due_schedules(session, tick, simulation_id)
    }
    forced_wake_kami = set()
    for agent_id in get_forced_wake_agents(session, simulation_id, tick):
        location = fs.get_current_location(session, agent_id)
        if location is not None:
            forced_wake_kami.add(location.kami_id)

    for kami_id in all_kami_ids:
        if kami_id in forced_wake_kami:
            active.add(kami_id)
            continue
        # (a) Kami with at least one agent inside
        agents = fs.get_agents_in_kami(session, kami_id)
        if agents:
            active.add(kami_id)
            continue

        # (b) Kami with a scheduled event firing this tick
        if kami_id in due_kami_ids:
            active.add(kami_id)
            continue

        # (c) Inbound propagated event with salience > threshold
        pending = event_bus.get_pending_events(tick, kami_id)
        if any(e.salience > config.kami_wake_salience_threshold for e in pending):
            active.add(kami_id)
            continue

        # (d) Forced refresh
        if tick > 0 and tick % config.forced_refresh_interval == 0:
            active.add(kami_id)

    return active


def detect_active_agents(
    session: Session,
    active_kami: Collection[str],
) -> dict[str, list[str]]:
    """Get agents per active kami. Returns {kami_id: [agent_ids]}."""
    result = {}
    for kami_id in sorted(active_kami):
        agents = fs.get_agents_in_kami(session, kami_id)
        if agents:
            result[kami_id] = sorted(a.entity_id for a in agents)
        else:
            result[kami_id] = []
    return result
