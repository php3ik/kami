"""Determine active kami and agents for a simulation tick."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy.orm import Session

from ..comms.channels import get_forced_wake_agents
from ..config import config
from ..eventbus.bus import EventBus
from ..factstore import tools as fs


DORMANT_ACTIVITY_VALUES = {"sleep", "sleeping", "asleep", "dormant"}


def is_agent_dormant(session: Session, agent_id: str) -> bool:
    """Return whether physical state explicitly suppresses routine cognition."""
    states = {row.attribute: row.value for row in fs.get_state(session, agent_id)}
    if states.get("awake") is False:
        return True
    if states.get("sleeping") is True or states.get("asleep") is True:
        return True
    activity = str(states.get("activity") or states.get("status") or "").lower()
    return activity in DORMANT_ACTIVITY_VALUES


def detect_active_kami(
    session: Session,
    event_bus: EventBus,
    tick: int,
    all_kami_ids: list[str],
    simulation_id: str | None = None,
) -> set[str]:
    """Determine which kami need rendering this tick."""
    active = set()
    simulation_id = simulation_id or fs.resolve_simulation_id(
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

        agents = fs.get_agents_in_kami(session, kami_id)
        if any(not is_agent_dormant(session, agent.entity_id) for agent in agents):
            active.add(kami_id)
            continue

        if kami_id in due_kami_ids:
            active.add(kami_id)
            continue

        pending = event_bus.get_pending_events(tick, kami_id)
        if any(event.salience > config.kami_wake_salience_threshold for event in pending):
            active.add(kami_id)
            continue

        if (
            config.forced_refresh_interval > 0
            and tick > 0
            and tick % config.forced_refresh_interval == 0
        ):
            active.add(kami_id)

    return active


def detect_active_agents(
    session: Session,
    active_kami: Collection[str],
    forced_agent_ids: Collection[str] = (),
) -> dict[str, list[str]]:
    """Get awake or explicitly woken agents per active kami."""
    forced = set(forced_agent_ids)
    result = {}
    for kami_id in sorted(active_kami):
        agents = [
            agent
            for agent in fs.get_agents_in_kami(session, kami_id)
            if agent.entity_id in forced
            or not is_agent_dormant(session, agent.entity_id)
        ]
        result[kami_id] = sorted(agent.entity_id for agent in agents)
    return result
