"""Persistence helpers for resumable WorldBuilder jobs."""

from __future__ import annotations

from datetime import UTC, datetime

from ..factstore.models import WorldBuildJob


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class WorldBuildJobRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def to_record(job: WorldBuildJob, *, include_checkpoint: bool = False) -> dict:
        record = {
            "job_id": job.job_id,
            "simulation_id": job.simulation_id,
            "status": job.status,
            "stage": job.stage,
            "completed_units": job.completed_units,
            "total_units": job.total_units,
            "message": job.message,
            "error": job.error_message,
            "cancel_requested": bool(job.cancel_requested),
            "request": dict(job.request or {}),
            "created_at": job.created_at.replace(tzinfo=UTC).isoformat(),
            "updated_at": job.updated_at.replace(tzinfo=UTC).isoformat(),
            "completed_at": (
                job.completed_at.replace(tzinfo=UTC).isoformat()
                if job.completed_at
                else None
            ),
        }
        if include_checkpoint:
            record["checkpoint"] = dict(job.checkpoint or {})
        return record

    def create(self, job_id: str, simulation_id: str, request: dict) -> dict:
        with self.session_factory() as session:
            job = WorldBuildJob(
                job_id=job_id,
                simulation_id=simulation_id,
                request=dict(request),
                checkpoint={},
            )
            session.add(job)
            session.commit()
            return self.to_record(job)

    def get(self, job_id: str, *, include_checkpoint: bool = False) -> dict | None:
        with self.session_factory() as session:
            job = session.get(WorldBuildJob, job_id)
            return self.to_record(job, include_checkpoint=include_checkpoint) if job else None

    def list(
        self,
        *,
        simulation_id: str | None = None,
        statuses: set[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        with self.session_factory() as session:
            query = session.query(WorldBuildJob)
            if simulation_id is not None:
                query = query.filter(WorldBuildJob.simulation_id == simulation_id)
            if statuses:
                query = query.filter(WorldBuildJob.status.in_(sorted(statuses)))
            jobs = query.order_by(
                WorldBuildJob.updated_at.desc(), WorldBuildJob.job_id.asc()
            ).limit(max(1, min(100, int(limit)))).all()
            return [self.to_record(job) for job in jobs]

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        completed_units: int | None = None,
        total_units: int | None = None,
        message: str | None = None,
        checkpoint: dict | None = None,
        error: str | None = None,
        cancel_requested: bool | None = None,
    ) -> dict:
        with self.session_factory() as session:
            job = session.get(WorldBuildJob, job_id)
            if job is None:
                raise KeyError(f"World build job not found: {job_id}")
            if status is not None:
                job.status = status
                if status in {"completed", "failed", "cancelled"}:
                    job.completed_at = _utcnow_naive()
                elif status in {"queued", "running"}:
                    job.completed_at = None
            if stage is not None:
                job.stage = stage
            if completed_units is not None:
                job.completed_units = max(0, int(completed_units))
            if total_units is not None:
                job.total_units = max(1, int(total_units))
            if message is not None:
                job.message = str(message)[:1000]
            if checkpoint is not None:
                job.checkpoint = dict(checkpoint)
            if error is not None or status in {"queued", "running", "completed"}:
                job.error_message = str(error)[:4000] if error else None
            if cancel_requested is not None:
                job.cancel_requested = bool(cancel_requested)
            job.updated_at = _utcnow_naive()
            session.commit()
            return self.to_record(job)

    def request_cancel(self, job_id: str) -> dict:
        return self.update(
            job_id,
            cancel_requested=True,
            message="Cancellation requested",
        )

    def mark_interrupted(self) -> int:
        with self.session_factory() as session:
            jobs = session.query(WorldBuildJob).filter(
                WorldBuildJob.status.in_(["queued", "running"])
            ).all()
            for job in jobs:
                job.status = "failed"
                job.message = "Build interrupted; resume is available"
                job.error_message = "Process stopped before the build completed"
                job.completed_at = _utcnow_naive()
                job.updated_at = _utcnow_naive()
            session.commit()
            return len(jobs)
