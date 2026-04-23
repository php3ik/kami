"""EventBus — event propagation with one-tick causality lag (spec §2.9).

MVP: in-memory queue. Events propagate to neighboring kami with salience
attenuation based on edge properties.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PropagatedEvent:
    """An event propagated to a neighbor kami."""

    source_event_id: str
    source_kami_id: str
    target_kami_id: str
    event_type: str
    narrative_digest: str
    salience: float
    delivery_tick: int  # tick when it should be delivered (source_tick + 1)
    payload: dict = field(default_factory=dict)


class EventBus:
    """In-memory event bus with tick-delayed delivery."""

    def __init__(self):
        # tick -> list of PropagatedEvents to deliver
        self._pending: dict[int, list[PropagatedEvent]] = defaultdict(list)
        # Broadcast snippets: tick -> kami_id -> list of digest strings
        self._broadcasts: dict[int, dict[str, list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def propagate_event(
        self,
        source_event_id: str,
        source_kami_id: str,
        target_kami_id: str,
        event_type: str,
        narrative_digest: str,
        salience: float,
        current_tick: int,
        payload: dict | None = None,
    ):
        """Queue an event for delivery next tick."""
        evt = PropagatedEvent(
            source_event_id=source_event_id,
            source_kami_id=source_kami_id,
            target_kami_id=target_kami_id,
            event_type=event_type,
            narrative_digest=narrative_digest,
            salience=salience,
            delivery_tick=current_tick + 1,
            payload=payload or {},
        )
        self._pending[current_tick + 1].append(evt)

    def publish_broadcast(
        self,
        source_kami_id: str,
        text: str,
        salience: float,
        current_tick: int,
        neighbor_kami_ids: list[str],
        attenuation_map: dict[str, float] | None = None,
    ):
        """Publish a broadcast digest to neighbors."""
        attenuation = attenuation_map or {}
        for neighbor in neighbor_kami_ids:
            att = attenuation.get(neighbor, 0.2)
            effective_salience = salience * (1.0 - att)
            self._broadcasts[current_tick + 1][neighbor].append(
                f"[from {source_kami_id}, salience={effective_salience:.2f}]: {text}"
            )

    def get_pending_events(self, tick: int, kami_id: str) -> list[PropagatedEvent]:
        """Get events queued for delivery to a kami on this tick."""
        return [
            e for e in self._pending.get(tick, []) if e.target_kami_id == kami_id
        ]

    def get_broadcasts(self, tick: int, kami_id: str) -> list[str]:
        """Get broadcast digests for a kami on this tick."""
        return self._broadcasts.get(tick, {}).get(kami_id, [])

    def get_all_pending_kami_ids(self, tick: int) -> set[str]:
        """Get all kami IDs that have pending events for this tick."""
        kami_ids = set()
        for evt in self._pending.get(tick, []):
            kami_ids.add(evt.target_kami_id)
        for kami_id in self._broadcasts.get(tick, {}):
            kami_ids.add(kami_id)
        return kami_ids

    def cleanup_tick(self, tick: int):
        """Remove delivered events for a tick."""
        self._pending.pop(tick, None)
        self._broadcasts.pop(tick, None)
