"""Unit tests for budget tracking."""

from kami_sim.llm.budget import BudgetTracker


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
