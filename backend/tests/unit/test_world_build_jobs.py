import asyncio

import pytest

from kami_sim.factstore.models import Simulation, init_db
from kami_sim.simulations.runtime import SimulationRunManager
from kami_sim.world_builder.jobs import WorldBuildJobRepository


def test_world_build_job_persists_progress_checkpoint_and_interruption():
    engine, factory = init_db("sqlite:///:memory:")
    with factory() as session:
        session.add(Simulation(id="sim-a", name="A"))
        session.commit()
    repository = WorldBuildJobRepository(factory)
    try:
        created = repository.create(
            "build-a", "sim-a", {"prompt": "A world", "agent_count": 10}
        )
        assert created["status"] == "queued"

        repository.update(
            "build-a",
            status="running",
            stage="population",
            completed_units=2,
            checkpoint={"completed_stages": ["seed", "spatial"]},
        )
        persisted = repository.get("build-a", include_checkpoint=True)
        assert persisted["stage"] == "population"
        assert persisted["checkpoint"]["completed_stages"] == ["seed", "spatial"]
        assert repository.list(statuses={"running"})[0]["job_id"] == "build-a"

        assert repository.mark_interrupted() == 1
        interrupted = repository.get("build-a")
        assert interrupted["status"] == "failed"
        assert "resume" in interrupted["message"].lower()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_run_manager_can_cancel_background_operation():
    manager = SimulationRunManager()
    started = asyncio.Event()

    async def operation():
        started.set()
        await asyncio.Event().wait()

    manager.start_operation("build world", "sim-a", operation)
    await started.wait()
    assert manager.is_busy is True

    assert await manager.cancel_operation("sim-a") is True
    assert manager.is_busy is False
