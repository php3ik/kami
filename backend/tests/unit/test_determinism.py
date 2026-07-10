from kami_sim.config import config
from kami_sim.determinism import generate_id, request_seed, stable_seed, tick_scope


def test_stable_seed_is_repeatable_and_input_sensitive():
    assert stable_seed("sim-a", 4, "AgentWorker") == stable_seed(
        "sim-a", 4, "AgentWorker"
    )
    assert stable_seed("sim-a", 4, "AgentWorker") != stable_seed(
        "sim-a", 5, "AgentWorker"
    )


def test_tick_scope_replays_canonical_ids(monkeypatch):
    monkeypatch.setattr(config, "deterministic_mode", True)
    monkeypatch.setattr(config, "deterministic_seed", 73)

    with tick_scope("sim-a", 9):
        first_attempt = [generate_id("evt_"), generate_id("evt_")]
    with tick_scope("sim-a", 9):
        replayed_attempt = [generate_id("evt_"), generate_id("evt_")]
    with tick_scope("sim-a", 10):
        next_tick = generate_id("evt_")

    assert first_attempt == replayed_attempt
    assert first_attempt[0] != first_attempt[1]
    assert next_tick != first_attempt[0]


def test_request_seed_is_disabled_by_default_and_stable_when_enabled(monkeypatch):
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    monkeypatch.setattr(config, "deterministic_mode", False)
    assert request_seed("AgentWorker", 2, payload) is None

    monkeypatch.setattr(config, "deterministic_mode", True)
    monkeypatch.setattr(config, "deterministic_seed", 11)
    assert request_seed("AgentWorker", 2, payload) == request_seed(
        "AgentWorker", 2, payload
    )
