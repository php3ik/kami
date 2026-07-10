"""Cost tracking for LLM calls — spec §2.2, §4.3.

Every LLM call passes through here. The dollar counter is non-negotiable.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Iterator


# Approximate pricing per million tokens (as of 2025)
MODEL_PRICING = {
    # Haiku-class
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    # Sonnet-class
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    # Opus-class
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}

DEFAULT_PRICING = {"input": 3.0, "output": 15.0}
_simulation_context: ContextVar[str | None] = ContextVar(
    "budget_simulation_id", default=None
)


def _pricing_for_model(model: str) -> dict[str, float]:
    """Resolve both plain and provider-prefixed model names."""
    model_name = model.split(":", 1)[-1]
    return MODEL_PRICING.get(model, MODEL_PRICING.get(model_name, DEFAULT_PRICING))


@dataclass
class LLMCallRecord:
    model: str
    component: str  # KamiWorker, AgentWorker, Consolidator, WorldBuilder
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: float = 0.0
    tick: int | None = None
    simulation_id: str | None = None


@dataclass
class BudgetTracker:
    """Tracks all LLM spending across the simulation."""

    records: list[LLMCallRecord] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    _lock: Lock = field(default_factory=Lock)

    def record_call(
        self,
        model: str,
        component: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        tick: int | None = None,
        simulation_id: str | None = None,
    ) -> LLMCallRecord:
        pricing = _pricing_for_model(model)
        scoped_simulation_id = simulation_id or _simulation_context.get()
        cost = (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
            # Cache reads are typically 90% cheaper
            + cache_read_tokens * pricing["input"] * 0.1 / 1_000_000
            # Cache writes have a small premium
            + cache_write_tokens * pricing["input"] * 1.25 / 1_000_000
        )

        record = LLMCallRecord(
            model=model,
            component=component,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost,
            timestamp=time.time(),
            tick=tick,
            simulation_id=scoped_simulation_id,
        )

        with self._lock:
            self.records.append(record)
            self.total_cost_usd += cost
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

        return record

    @contextmanager
    def scope(self, simulation_id: str | None) -> Iterator[None]:
        """Attach a simulation id to calls made in this async context."""
        token = _simulation_context.set(simulation_id)
        try:
            yield
        finally:
            _simulation_context.reset(token)

    def get_summary(self, simulation_id: str | None = None) -> dict:
        with self._lock:
            records = [
                record
                for record in self.records
                if simulation_id is None or record.simulation_id == simulation_id
            ]
            by_component: dict[str, float] = {}
            for r in records:
                by_component[r.component] = by_component.get(r.component, 0) + r.cost_usd
            return {
                "total_cost_usd": round(sum(r.cost_usd for r in records), 4),
                "total_calls": len(records),
                "total_input_tokens": sum(r.input_tokens for r in records),
                "total_output_tokens": sum(r.output_tokens for r in records),
                "by_component": {k: round(v, 4) for k, v in by_component.items()},
            }

    def get_tick_cost(self, tick: int, simulation_id: str | None = None) -> float:
        with self._lock:
            return sum(
                record.cost_usd
                for record in self.records
                if record.tick == tick
                and (simulation_id is None or record.simulation_id == simulation_id)
            )


# Global singleton
budget = BudgetTracker()
