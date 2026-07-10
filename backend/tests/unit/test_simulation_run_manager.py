import asyncio

import pytest

from kami_sim.simulations import RunConflictError, SimulationRunManager


@pytest.mark.asyncio
async def test_command_claims_and_releases_runtime():
    manager = SimulationRunManager()

    async with manager.command("step simulation", "sim-a", running=True):
        assert manager.snapshot() == {
            "busy": True,
            "running": True,
            "paused": False,
            "operation": "step simulation",
            "simulation_id": "sim-a",
        }
        with pytest.raises(RunConflictError):
            manager.ensure_idle("switch simulations")

    assert manager.is_busy is False
    assert manager.is_running is False
    assert manager.is_paused is True


@pytest.mark.asyncio
async def test_background_run_honors_pause_and_releases_runtime():
    manager = SimulationRunManager()
    calls = 0
    errors = []
    completions = []

    async def tick():
        nonlocal calls
        calls += 1
        manager.pause()
        return [{"tick": calls}]

    async def on_error(exc):
        errors.append(exc)

    async def on_complete():
        completions.append(True)

    task = manager.start_background(
        "run simulation", "sim-a", 10, tick, on_error, on_complete
    )
    await task

    assert calls == 1
    assert errors == []
    assert completions == [True]
    assert manager.is_busy is False


@pytest.mark.asyncio
async def test_shutdown_cancels_active_background_task():
    manager = SimulationRunManager()
    started = asyncio.Event()

    async def tick():
        started.set()
        await asyncio.Event().wait()
        return []

    async def on_error(exc):
        raise AssertionError(f"Unexpected error callback: {exc}")

    task = manager.start_background(
        "run simulation", "sim-a", 10, tick, on_error
    )
    await started.wait()
    await manager.shutdown()

    assert task.cancelled()
    assert manager.is_busy is False
