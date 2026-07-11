"""Persistent cost accounting and admission control for LLM calls."""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Iterator

from sqlalchemy import func

from ..factstore.models import LLMCall, Simulation


# Approximate pricing per million tokens. Unknown models use DEFAULT_PRICING.
MODEL_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.4-mini-2026-03-17": {"input": 0.75, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
    "gpt-5.4": {"input": 2.50, "output": 15.00},
    "gpt-5.5": {"input": 5.00, "output": 30.00},
    "gpt-5.5-pro": {"input": 30.00, "output": 180.00},
}

DEFAULT_PRICING = {"input": 3.0, "output": 15.0}
_simulation_context: ContextVar[str | None] = ContextVar(
    "budget_simulation_id", default=None
)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _pricing_for_model(model: str) -> dict[str, float]:
    model_name = model.split(":", 1)[-1]
    return MODEL_PRICING.get(model, MODEL_PRICING.get(model_name, DEFAULT_PRICING))


def _calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    pricing = _pricing_for_model(model)
    return (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
        + cache_read_tokens * pricing["input"] * 0.1 / 1_000_000
        + cache_write_tokens * pricing["input"] * 1.25 / 1_000_000
    )


class BudgetExceededError(RuntimeError):
    """Raised before an LLM request that could exceed its simulation cap."""

    def __init__(self, simulation_id: str, limit_usd: float, required_usd: float):
        self.simulation_id = simulation_id
        self.limit_usd = limit_usd
        self.required_usd = required_usd
        super().__init__(
            f"Simulation {simulation_id} budget exhausted: "
            f"limit ${limit_usd:.4f}, call requires up to ${required_usd:.4f}"
        )


@dataclass
class LLMCallRecord:
    model: str
    component: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: float = 0.0
    tick: int | None = None
    simulation_id: str | None = None
    call_id: str = ""
    status: str = "completed"


@dataclass
class _Reservation:
    call_id: str
    simulation_id: str | None
    estimated_cost_usd: float
    started_at: datetime


@dataclass
class BudgetTracker:
    """Tracks spend, persists call records, and reserves capped capacity."""

    records: list[LLMCallRecord] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    default_limit_usd: float = 0.0
    _session_factory: Any = field(default=None, repr=False)
    _reservations: dict[str, _Reservation] = field(default_factory=dict, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def configure(self, session_factory: Any, default_limit_usd: float = 0.0) -> None:
        """Attach persistent storage after application database initialization."""
        with self._lock:
            self._session_factory = session_factory
            self.default_limit_usd = max(0.0, float(default_limit_usd))
            self._reservations.clear()

    @contextmanager
    def scope(self, simulation_id: str | None) -> Iterator[None]:
        token = _simulation_context.set(simulation_id)
        try:
            yield
        finally:
            _simulation_context.reset(token)

    def reserve_call(
        self,
        model: str,
        max_input_tokens: int,
        max_output_tokens: int,
        simulation_id: str | None = None,
    ) -> str:
        """Reserve a conservative upper-bound cost before contacting a provider."""
        scoped_id = simulation_id or _simulation_context.get()
        estimated_cost = _calculate_cost(
            model,
            max(0, int(max_input_tokens)),
            max(0, int(max_output_tokens)),
            cache_write_tokens=max(0, int(max_input_tokens)),
        )
        reservation = _Reservation(
            call_id=uuid.uuid4().hex,
            simulation_id=scoped_id,
            estimated_cost_usd=estimated_cost,
            started_at=_utcnow_naive(),
        )

        with self._lock:
            if scoped_id:
                spent, limit = self._budget_state(scoped_id)
                reserved = sum(
                    item.estimated_cost_usd
                    for item in self._reservations.values()
                    if item.simulation_id == scoped_id
                )
                if limit > 0 and spent + reserved + estimated_cost > limit:
                    raise BudgetExceededError(scoped_id, limit, estimated_cost)
            self._reservations[reservation.call_id] = reservation
        return reservation.call_id

    def release_reservation(self, reservation_id: str | None) -> None:
        if not reservation_id:
            return
        with self._lock:
            self._reservations.pop(reservation_id, None)

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
        reservation_id: str | None = None,
    ) -> LLMCallRecord:
        scoped_id = simulation_id or _simulation_context.get()
        cost = _calculate_cost(
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
        )
        reservation = self._reservations.get(reservation_id or "")
        call_id = reservation_id or uuid.uuid4().hex
        started_at = reservation.started_at if reservation else _utcnow_naive()
        record = LLMCallRecord(
            call_id=call_id,
            model=model,
            component=component,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost,
            timestamp=time.time(),
            tick=tick,
            simulation_id=scoped_id,
        )

        with self._lock:
            try:
                self._persist_record(record, started_at=started_at)
                self.records.append(record)
                self.total_cost_usd += cost
                self.total_input_tokens += input_tokens
                self.total_output_tokens += output_tokens
            finally:
                self._reservations.pop(call_id, None)
        return record

    def record_failure(
        self,
        model: str,
        component: str,
        error: Exception,
        tick: int | None = None,
        simulation_id: str | None = None,
        reservation_id: str | None = None,
    ) -> None:
        scoped_id = simulation_id or _simulation_context.get()
        reservation = self._reservations.get(reservation_id or "")
        call_id = reservation_id or uuid.uuid4().hex
        started_at = reservation.started_at if reservation else _utcnow_naive()
        provider, model_name = _split_model(model)
        with self._lock:
            try:
                if self._session_factory is not None:
                    session = self._session_factory()
                    try:
                        session.add(
                            LLMCall(
                                call_id=call_id,
                                simulation_id=scoped_id,
                                provider=provider,
                                model=model_name,
                                component=component,
                                tick=tick,
                                status="failed",
                                input_tokens=0,
                                output_tokens=0,
                                cache_read_tokens=0,
                                cache_write_tokens=0,
                                cost_usd=0.0,
                                error_type=type(error).__name__,
                                error_message=str(error)[:2000],
                                started_at=started_at,
                                completed_at=_utcnow_naive(),
                            )
                        )
                        session.commit()
                    except Exception:
                        session.rollback()
                        raise
                    finally:
                        session.close()
            finally:
                self._reservations.pop(call_id, None)

    def get_summary(self, simulation_id: str | None = None) -> dict:
        if self._session_factory is None:
            return self._memory_summary(simulation_id)

        session = self._session_factory()
        try:
            query = session.query(LLMCall)
            if simulation_id is not None:
                query = query.filter(LLMCall.simulation_id == simulation_id)
            completed = query.filter(LLMCall.status == "completed")
            aggregate = completed.with_entities(
                func.count(LLMCall.call_id),
                func.coalesce(func.sum(LLMCall.input_tokens), 0),
                func.coalesce(func.sum(LLMCall.output_tokens), 0),
                func.coalesce(func.sum(LLMCall.cost_usd), 0.0),
            ).one()
            failed_calls = query.filter(LLMCall.status == "failed").count()
            component_rows = completed.with_entities(
                LLMCall.component, func.sum(LLMCall.cost_usd)
            ).group_by(LLMCall.component).all()
            ledger_cost = float(aggregate[3] or 0.0)
            total_cost = ledger_cost
            limit = self.default_limit_usd
            if simulation_id is not None:
                simulation = session.get(Simulation, simulation_id)
                if simulation is not None:
                    total_cost = max(
                        ledger_cost, float(simulation.total_cost_usd or 0.0)
                    )
                    if simulation.budget_limit_usd is not None:
                        limit = float(simulation.budget_limit_usd)
            return {
                "total_cost_usd": round(total_cost, 6),
                "ledger_cost_usd": round(ledger_cost, 6),
                "unattributed_cost_usd": round(max(0.0, total_cost - ledger_cost), 6),
                "budget_limit_usd": round(limit, 6) if limit > 0 else None,
                "remaining_usd": round(max(0.0, limit - total_cost), 6)
                if limit > 0
                else None,
                "total_calls": int(aggregate[0] or 0),
                "failed_calls": failed_calls,
                "total_input_tokens": int(aggregate[1] or 0),
                "total_output_tokens": int(aggregate[2] or 0),
                "by_component": {
                    component: round(float(cost or 0.0), 6)
                    for component, cost in component_rows
                },
            }
        finally:
            session.close()

    def get_tick_cost(self, tick: int, simulation_id: str | None = None) -> float:
        if self._session_factory is None:
            with self._lock:
                return sum(
                    record.cost_usd
                    for record in self.records
                    if record.tick == tick
                    and (
                        simulation_id is None
                        or record.simulation_id == simulation_id
                    )
                )
        session = self._session_factory()
        try:
            query = session.query(func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).filter(
                LLMCall.status == "completed", LLMCall.tick == tick
            )
            if simulation_id is not None:
                query = query.filter(LLMCall.simulation_id == simulation_id)
            return float(query.scalar() or 0.0)
        finally:
            session.close()

    def list_calls(
        self, simulation_id: str, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        if self._session_factory is None:
            return []
        session = self._session_factory()
        try:
            calls = (
                session.query(LLMCall)
                .filter(LLMCall.simulation_id == simulation_id)
                .order_by(LLMCall.completed_at.desc(), LLMCall.call_id.desc())
                .offset(max(0, offset))
                .limit(min(max(1, limit), 500))
                .all()
            )
            return [
                {
                    "call_id": call.call_id,
                    "provider": call.provider,
                    "model": call.model,
                    "component": call.component,
                    "tick": call.tick,
                    "status": call.status,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "cache_read_tokens": call.cache_read_tokens,
                    "cache_write_tokens": call.cache_write_tokens,
                    "cost_usd": round(float(call.cost_usd or 0.0), 8),
                    "error_type": call.error_type,
                    "error_message": call.error_message,
                    "started_at": call.started_at.replace(tzinfo=UTC).isoformat(),
                    "completed_at": call.completed_at.replace(tzinfo=UTC).isoformat(),
                }
                for call in calls
            ]
        finally:
            session.close()

    def _budget_state(self, simulation_id: str) -> tuple[float, float]:
        if self._session_factory is None:
            spent = sum(
                record.cost_usd
                for record in self.records
                if record.simulation_id == simulation_id
            )
            return spent, self.default_limit_usd
        session = self._session_factory()
        try:
            simulation = session.get(Simulation, simulation_id)
            ledger_cost = float(
                session.query(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
                .filter(
                    LLMCall.simulation_id == simulation_id,
                    LLMCall.status == "completed",
                )
                .scalar()
                or 0.0
            )
            if simulation is None:
                return ledger_cost, self.default_limit_usd
            limit = (
                self.default_limit_usd
                if simulation.budget_limit_usd is None
                else float(simulation.budget_limit_usd)
            )
            return max(ledger_cost, float(simulation.total_cost_usd or 0.0)), limit
        finally:
            session.close()

    def _persist_record(self, record: LLMCallRecord, started_at: datetime) -> None:
        if self._session_factory is None:
            return
        provider, model_name = _split_model(record.model)
        session = self._session_factory()
        try:
            session.add(
                LLMCall(
                    call_id=record.call_id,
                    simulation_id=record.simulation_id,
                    provider=provider,
                    model=model_name,
                    component=record.component,
                    tick=record.tick,
                    status=record.status,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cache_read_tokens=record.cache_read_tokens,
                    cache_write_tokens=record.cache_write_tokens,
                    cost_usd=record.cost_usd,
                    started_at=started_at,
                    completed_at=_utcnow_naive(),
                )
            )
            if record.simulation_id:
                simulation = session.get(Simulation, record.simulation_id)
                if simulation is not None:
                    simulation.total_cost_usd = float(
                        simulation.total_cost_usd or 0.0
                    ) + record.cost_usd
                    simulation.updated_at = _utcnow_naive()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _memory_summary(self, simulation_id: str | None) -> dict:
        with self._lock:
            records = [
                record
                for record in self.records
                if simulation_id is None or record.simulation_id == simulation_id
            ]
            by_component: dict[str, float] = {}
            for record in records:
                by_component[record.component] = (
                    by_component.get(record.component, 0.0) + record.cost_usd
                )
            total = sum(record.cost_usd for record in records)
            return {
                "total_cost_usd": round(total, 6),
                "ledger_cost_usd": round(total, 6),
                "unattributed_cost_usd": 0.0,
                "budget_limit_usd": self.default_limit_usd or None,
                "remaining_usd": (
                    round(max(0.0, self.default_limit_usd - total), 6)
                    if self.default_limit_usd > 0
                    else None
                ),
                "total_calls": len(records),
                "failed_calls": 0,
                "total_input_tokens": sum(r.input_tokens for r in records),
                "total_output_tokens": sum(r.output_tokens for r in records),
                "by_component": {
                    key: round(value, 6) for key, value in by_component.items()
                },
            }


def _split_model(model: str) -> tuple[str, str]:
    if ":" in model:
        provider, model_name = model.split(":", 1)
        return provider, model_name
    return "unknown", model


budget = BudgetTracker()
