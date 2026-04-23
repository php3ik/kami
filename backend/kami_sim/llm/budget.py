"""Cost tracking for LLM calls — spec §2.2, §4.3.

Every LLM call passes through here. The dollar counter is non-negotiable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock


# Approximate pricing per million tokens (as of 2025)
MODEL_PRICING = {
    # Haiku-class
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    # Sonnet-class
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    # Opus-class
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}


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
    ) -> LLMCallRecord:
        pricing = MODEL_PRICING.get(model, {"input": 3.0, "output": 15.0})
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
        )

        with self._lock:
            self.records.append(record)
            self.total_cost_usd += cost
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

        return record

    def get_summary(self) -> dict:
        with self._lock:
            by_component: dict[str, float] = {}
            for r in self.records:
                by_component[r.component] = by_component.get(r.component, 0) + r.cost_usd
            return {
                "total_cost_usd": round(self.total_cost_usd, 4),
                "total_calls": len(self.records),
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "by_component": {k: round(v, 4) for k, v in by_component.items()},
            }

    def get_tick_cost(self, tick: int) -> float:
        with self._lock:
            return sum(r.cost_usd for r in self.records if r.tick == tick)


# Global singleton
budget = BudgetTracker()
