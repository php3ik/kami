"""Deterministic runtime helpers for replayable scheduler ticks."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from .config import config


@dataclass
class _TickContext:
    simulation_id: str
    tick: int
    seed: int
    counters: dict[str, int] = field(default_factory=dict)


_tick_context: ContextVar[_TickContext | None] = ContextVar(
    "kami_deterministic_tick", default=None
)


def stable_seed(*parts: object) -> int:
    """Return a provider-compatible positive seed for stable input parts."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def request_seed(component: str, tick: int | None, payload: object) -> int | None:
    if not config.deterministic_mode:
        return None
    return stable_seed(config.deterministic_seed, component, tick, payload)


@contextmanager
def tick_scope(simulation_id: str, tick: int) -> Iterator[None]:
    """Reset deterministic ID counters for one canonical tick attempt."""
    if not config.deterministic_mode:
        yield
        return
    context = _TickContext(
        simulation_id=simulation_id,
        tick=tick,
        seed=stable_seed(config.deterministic_seed, simulation_id, tick),
    )
    token = _tick_context.set(context)
    try:
        yield
    finally:
        _tick_context.reset(token)


def generate_id(prefix: str, length: int = 12) -> str:
    """Generate stable IDs inside a deterministic tick and UUIDs elsewhere."""
    context = _tick_context.get()
    if context is None:
        return f"{prefix}{uuid.uuid4().hex[:length]}"
    index = context.counters.get(prefix, 0)
    context.counters[prefix] = index + 1
    digest = hashlib.sha256(
        f"{context.seed}:{context.simulation_id}:{context.tick}:{prefix}:{index}".encode()
    ).hexdigest()
    return f"{prefix}{digest[:length]}"


def tick_metadata(simulation_id: str, tick: int) -> dict:
    enabled = bool(config.deterministic_mode)
    return {
        "enabled": enabled,
        "seed": config.deterministic_seed if enabled else None,
        "tick_seed": (
            stable_seed(config.deterministic_seed, simulation_id, tick)
            if enabled
            else None
        ),
    }
