"""Adaptive dense/sparse tick selection."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..comms.channels import (
    get_next_call_transition_tick,
    get_next_forced_wake_tick,
)
from ..config import config
from ..eventbus.bus import EventBus
from ..factstore.models import Schedule
from ..spatial.transit import has_due_transit, next_transit_tick
from .activity_detector import detect_active_kami


def select_next_tick(
    session: Session,
    event_bus: EventBus,
    current_tick: int,
    all_kami_ids: list[str],
    simulation_id: str,
) -> int:
    """Return current tick in dense mode or the next meaningful wake tick."""
    if not config.adaptive_time_stepping:
        return current_tick
    if has_due_transit(session, simulation_id, current_tick):
        return current_tick
    if detect_active_kami(
        session,
        event_bus,
        current_tick,
        all_kami_ids,
        simulation_id=simulation_id,
    ):
        return current_tick

    candidates = [
        _next_schedule_tick(session, simulation_id, current_tick),
        next_transit_tick(session, simulation_id, current_tick),
        get_next_forced_wake_tick(session, simulation_id, current_tick),
        get_next_call_transition_tick(session, simulation_id, current_tick),
        event_bus.next_delivery_tick(
            current_tick, min_salience=config.kami_wake_salience_threshold
        ),
        _next_forced_refresh_tick(current_tick, bool(all_kami_ids)),
    ]
    future = [tick for tick in candidates if tick is not None and tick > current_tick]
    return min(future) if future else current_tick


def _next_schedule_tick(
    session: Session, simulation_id: str, current_tick: int
) -> int | None:
    row = session.query(Schedule).filter(
        Schedule.simulation_id == simulation_id,
        Schedule.fires_at_tick > current_tick,
    ).order_by(Schedule.fires_at_tick).first()
    return row.fires_at_tick if row is not None else None


def _next_forced_refresh_tick(
    current_tick: int, has_kami: bool
) -> int | None:
    interval = int(config.forced_refresh_interval)
    if not has_kami or interval <= 0:
        return None
    return ((current_tick // interval) + 1) * interval
