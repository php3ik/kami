"""Unit tests for budget tracking."""

import pytest

from kami_sim.factstore.models import LLMCall, init_db
from kami_sim.llm.budget import BudgetExceededError, BudgetTracker
from kami_sim.simulations import SimulationRepository


def test_cost_calculation():
    tracker = BudgetTracker()
    record = tracker.record_call(
        model="claude-haiku-4-5-20251001",
        component="KamiWorker",
        input_tokens=5000,
        output_tokens=1000,
        tick=1,
    )

    # Haiku: input $0.80/M, output $4.00/M
    expected = 5000 * 0.80 / 1_000_000 + 1000 * 4.00 / 1_000_000
    assert abs(record.cost_usd - expected) < 0.0001


def test_summary():
    tracker = BudgetTracker()
    tracker.record_call("claude-haiku-4-5-20251001", "KamiWorker", 1000, 500, tick=1)
    tracker.record_call("claude-haiku-4-5-20251001", "AgentWorker", 2000, 300, tick=1)

    summary = tracker.get_summary()
    assert summary["total_calls"] == 2
    assert "KamiWorker" in summary["by_component"]
    assert "AgentWorker" in summary["by_component"]


def test_tick_cost():
    tracker = BudgetTracker()
    tracker.record_call("claude-haiku-4-5-20251001", "KamiWorker", 1000, 500, tick=1)
    tracker.record_call("claude-haiku-4-5-20251001", "KamiWorker", 1000, 500, tick=2)

    assert tracker.get_tick_cost(1) > 0
    assert tracker.get_tick_cost(3) == 0


def test_provider_prefixed_model_uses_known_pricing():
    tracker = BudgetTracker()

    plain = tracker.record_call(
        "claude-haiku-4-5-20251001", "AgentWorker", 5000, 1000
    )
    prefixed = tracker.record_call(
        "anthropic:claude-haiku-4-5-20251001", "AgentWorker", 5000, 1000
    )

    assert prefixed.cost_usd == plain.cost_usd


def test_simulation_scope_separates_equal_tick_numbers():
    tracker = BudgetTracker()

    with tracker.scope("sim-a"):
        tracker.record_call("claude-haiku-4-5-20251001", "AgentWorker", 1000, 500, tick=1)
    with tracker.scope("sim-b"):
        tracker.record_call("claude-haiku-4-5-20251001", "AgentWorker", 2000, 500, tick=1)

    assert tracker.get_summary("sim-a")["total_calls"] == 1
    assert tracker.get_summary("sim-b")["total_calls"] == 1
    assert tracker.get_tick_cost(1, "sim-a") != tracker.get_tick_cost(1, "sim-b")


def test_persistent_ledger_survives_tracker_restart_and_updates_simulation_total():
    engine, factory = init_db("sqlite:///:memory:")
    repository = SimulationRepository(factory)
    repository.upsert({"id": "sim-a", "name": "A", "total_cost_usd": 0.0})
    try:
        tracker = BudgetTracker()
        tracker.configure(factory)
        with tracker.scope("sim-a"):
            tracker.record_call(
                "anthropic:claude-haiku-4-5-20251001",
                "AgentWorker",
                5000,
                1000,
                tick=3,
            )

        restarted = BudgetTracker()
        restarted.configure(factory)
        summary = restarted.get_summary("sim-a")

        assert summary["total_calls"] == 1
        assert summary["total_cost_usd"] > 0
        assert restarted.get_tick_cost(3, "sim-a") == summary["ledger_cost_usd"]
        assert repository.get("sim-a")["total_cost_usd"] == summary["total_cost_usd"]
    finally:
        engine.dispose()


def test_budget_reservations_prevent_parallel_calls_from_exceeding_limit():
    tracker = BudgetTracker(default_limit_usd=0.01)

    first = tracker.reserve_call(
        "anthropic:claude-haiku-4-5-20251001",
        max_input_tokens=1000,
        max_output_tokens=1000,
        simulation_id="sim-a",
    )
    with pytest.raises(BudgetExceededError):
        tracker.reserve_call(
            "anthropic:claude-haiku-4-5-20251001",
            max_input_tokens=1000,
            max_output_tokens=1000,
            simulation_id="sim-a",
        )

    tracker.release_reservation(first)
    second = tracker.reserve_call(
        "anthropic:claude-haiku-4-5-20251001",
        max_input_tokens=1000,
        max_output_tokens=1000,
        simulation_id="sim-a",
    )
    assert second


def test_failed_call_is_persisted_without_cost_and_releases_reservation():
    engine, factory = init_db("sqlite:///:memory:")
    tracker = BudgetTracker(default_limit_usd=0.01)
    tracker.configure(factory, default_limit_usd=0.01)
    try:
        reservation = tracker.reserve_call(
            "anthropic:claude-haiku-4-5-20251001",
            1000,
            1000,
            simulation_id="sim-a",
        )
        tracker.record_failure(
            "anthropic:claude-haiku-4-5-20251001",
            "KamiWorker",
            RuntimeError("provider unavailable"),
            simulation_id="sim-a",
            reservation_id=reservation,
        )

        summary = tracker.get_summary("sim-a")
        with factory() as session:
            failed = session.query(LLMCall).one()
        assert summary["failed_calls"] == 1
        assert summary["total_cost_usd"] == 0
        assert failed.error_type == "RuntimeError"
        assert tracker.reserve_call(
            "anthropic:claude-haiku-4-5-20251001",
            1000,
            1000,
            simulation_id="sim-a",
        )
    finally:
        engine.dispose()
