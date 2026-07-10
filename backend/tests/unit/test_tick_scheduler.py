import pytest

from kami_sim.scheduler.tick_scheduler import TickScheduler
from kami_sim.spatial.graph import SpatialGraph


class DummySession:
    def close(self):
        pass


@pytest.mark.asyncio
async def test_run_returns_only_current_batch_and_keeps_full_log(monkeypatch):
    scheduler = TickScheduler(
        session_factory=lambda: DummySession(),
        spatial_graph=SpatialGraph(),
    )

    async def fake_run_tick(session, tick, progress_callback=None):
        return {
            "tick": tick,
            "active_kami_count": 0,
            "active_agent_count": 0,
            "events": [],
            "narratives": {},
        }

    monkeypatch.setattr(scheduler, "_run_tick", fake_run_tick)

    first = await scheduler.run(num_ticks=2)
    second = await scheduler.run(num_ticks=1)

    assert [t["tick"] for t in first] == [0, 1]
    assert [t["tick"] for t in second] == [2]
    assert [t["tick"] for t in scheduler.tick_log] == [0, 1, 2]


@pytest.mark.asyncio
async def test_fatal_tick_does_not_advance_clock(monkeypatch):
    scheduler = TickScheduler(
        session_factory=lambda: DummySession(),
        spatial_graph=SpatialGraph(),
    )

    async def fail_tick(session, tick, progress_callback=None):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(scheduler, "_run_tick", fail_tick)

    results = await scheduler.run(num_ticks=2)

    assert len(results) == 1
    assert results[0]["tick"] == 0
    assert results[0]["error"] == "commit failed"
    assert scheduler.current_tick == 0
