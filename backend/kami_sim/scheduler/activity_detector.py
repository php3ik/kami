"""Activity detector — determines which kami are active each tick (spec §2.5)."""

from __future__ import annotations

from sqlalchemy.orm import Session

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

    for kami_id in all_kami_ids:
        # (a) Kami with at least one agent inside
        agents = fs.get_agents_in_kami(session, kami_id)
        if agents:
            active.add(kami_id)
            continue

        # (b) Kami with a scheduled event firing this tick
        schedules = fs.get_due_schedules(session, tick)
        if any(s.kami_id == kami_id for s in schedules):
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
    active_kami: set[str],
) -> dict[str, list[str]]:
    """Get agents per active kami. Returns {kami_id: [agent_ids]}."""
    result = {}
    for kami_id in active_kami:
        agents = fs.get_agents_in_kami(session, kami_id)
        if agents:
            result[kami_id] = [a.entity_id for a in agents]
        else:
            result[kami_id] = []
    return result
