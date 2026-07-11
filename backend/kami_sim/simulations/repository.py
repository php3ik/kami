"""Database-backed replacement for simulations_registry.json."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..factstore.models import Simulation
from ..language import normalize_content_language


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_datetime(value: Any, default: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.replace(tzinfo=None)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(UTC)
            return parsed.replace(tzinfo=None)
        except ValueError:
            pass
    return default or _utcnow_naive()


class SimulationRepository:
    """Owns simulation metadata, active selection, and legacy import."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def to_record(simulation: Simulation) -> dict:
        return {
            "id": simulation.id,
            "name": simulation.name,
            "prompt": simulation.prompt,
            "content_language": normalize_content_language(simulation.content_language),
            "status": simulation.status,
            "current_tick": simulation.current_tick,
            "graph_data": simulation.graph_data or {},
            "db_url": simulation.db_url,
            "db_path": simulation.db_path,
            "graph_path": simulation.graph_path,
            "created_at": simulation.created_at.replace(tzinfo=UTC).isoformat(),
            "updated_at": simulation.updated_at.replace(tzinfo=UTC).isoformat(),
            "total_cost_usd": float(simulation.total_cost_usd or 0.0),
            "budget_limit_usd": simulation.budget_limit_usd,
        }

    def read_registry(self) -> dict:
        session = self.session_factory()
        try:
            simulations = (
                session.query(Simulation)
                .order_by(Simulation.updated_at.desc(), Simulation.id.asc())
                .all()
            )
            active = next((item.id for item in simulations if item.is_active), None)
            return {
                "active_id": active,
                "simulations": [self.to_record(item) for item in simulations],
            }
        finally:
            session.close()

    def get(self, simulation_id: str) -> dict | None:
        session = self.session_factory()
        try:
            simulation = session.get(Simulation, simulation_id)
            return self.to_record(simulation) if simulation else None
        finally:
            session.close()

    def get_active(self) -> dict | None:
        session = self.session_factory()
        try:
            simulation = (
                session.query(Simulation)
                .filter(Simulation.is_active.is_(True))
                .order_by(Simulation.updated_at.desc())
                .first()
            )
            return self.to_record(simulation) if simulation else None
        finally:
            session.close()

    def upsert(self, record: dict, active: bool = False) -> dict:
        session = self.session_factory()
        try:
            simulation = session.get(Simulation, record["id"])
            if simulation is None:
                simulation = Simulation(id=record["id"], name=record.get("name") or record["id"])
                session.add(simulation)

            self._apply_record(simulation, record)
            if active:
                session.query(Simulation).filter(
                    Simulation.id != simulation.id,
                    Simulation.is_active.is_(True),
                ).update({Simulation.is_active: False}, synchronize_session=False)
                simulation.is_active = True
            session.commit()
            return self.to_record(simulation)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_runtime(
        self,
        simulation_id: str,
        *,
        current_tick: int | None = None,
        status: str | None = None,
        cost_delta: float = 0.0,
    ) -> dict:
        session = self.session_factory()
        try:
            simulation = session.get(Simulation, simulation_id)
            if simulation is None:
                raise KeyError(f"Simulation not found: {simulation_id}")
            if current_tick is not None:
                simulation.current_tick = max(0, int(current_tick))
            if status is not None:
                simulation.status = status
            simulation.total_cost_usd = float(simulation.total_cost_usd or 0.0) + float(
                cost_delta
            )
            simulation.updated_at = _utcnow_naive()
            session.commit()
            return self.to_record(simulation)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_budget_limit(
        self, simulation_id: str, budget_limit_usd: float | None
    ) -> dict:
        session = self.session_factory()
        try:
            simulation = session.get(Simulation, simulation_id)
            if simulation is None:
                raise KeyError(f"Simulation not found: {simulation_id}")
            if budget_limit_usd is not None and budget_limit_usd < 0:
                raise ValueError("Budget limit cannot be negative")
            simulation.budget_limit_usd = budget_limit_usd
            simulation.updated_at = _utcnow_naive()
            session.commit()
            return self.to_record(simulation)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def replace_registry(self, registry: dict) -> None:
        session = self.session_factory()
        try:
            records = registry.get("simulations", [])
            incoming_ids = {record["id"] for record in records}
            if incoming_ids:
                session.query(Simulation).filter(
                    Simulation.id.notin_(incoming_ids)
                ).delete(synchronize_session=False)
            else:
                session.query(Simulation).delete(synchronize_session=False)

            active_id = registry.get("active_id")
            for record in records:
                simulation = session.get(Simulation, record["id"])
                if simulation is None:
                    simulation = Simulation(
                        id=record["id"],
                        name=record.get("name") or record["id"],
                    )
                    session.add(simulation)
                self._apply_record(simulation, record)
                simulation.is_active = simulation.id == active_id
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def import_legacy_registry(self, registry: dict) -> int:
        """Idempotently import records from the tracked legacy JSON file."""
        imported = 0
        session = self.session_factory()
        try:
            active_id = registry.get("active_id")
            has_active = session.query(Simulation).filter(
                Simulation.is_active.is_(True)
            ).first() is not None
            for record in registry.get("simulations", []):
                simulation = session.get(Simulation, record["id"])
                if simulation is None:
                    simulation = Simulation(
                        id=record["id"],
                        name=record.get("name") or record["id"],
                    )
                    session.add(simulation)
                    imported += 1
                    self._apply_record(simulation, record)
                elif simulation.status == "migrated":
                    self._apply_record(simulation, record)
                if not has_active and simulation.id == active_id:
                    session.query(Simulation).filter(
                        Simulation.id != simulation.id,
                        Simulation.is_active.is_(True),
                    ).update({Simulation.is_active: False}, synchronize_session=False)
                    simulation.is_active = True
                    has_active = True
            session.commit()
            return imported
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _apply_record(simulation: Simulation, record: dict) -> None:
        simulation.name = record.get("name") or simulation.id
        simulation.prompt = record.get("prompt") or ""
        simulation.content_language = normalize_content_language(
            record.get("content_language")
        )
        simulation.status = record.get("status") or "paused"
        simulation.current_tick = max(0, int(record.get("current_tick") or 0))
        simulation.graph_data = record.get("graph_data") or {}
        simulation.db_url = record.get("db_url")
        simulation.db_path = record.get("db_path")
        simulation.graph_path = record.get("graph_path")
        simulation.total_cost_usd = float(record.get("total_cost_usd") or 0.0)
        if "budget_limit_usd" in record:
            raw_limit = record.get("budget_limit_usd")
            simulation.budget_limit_usd = (
                None if raw_limit is None else max(0.0, float(raw_limit))
            )
        simulation.created_at = _parse_datetime(
            record.get("created_at"), simulation.created_at
        )
        simulation.updated_at = _parse_datetime(record.get("updated_at"))
