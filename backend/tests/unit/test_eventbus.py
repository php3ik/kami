"""Unit tests for EventBus."""

from kami_sim.eventbus.bus import EventBus


def test_propagate_with_delay():
    bus = EventBus()
    bus.propagate_event(
        source_event_id="evt_1",
        source_kami_id="kami_a",
        target_kami_id="kami_b",
        event_type="noise",
        narrative_digest="A loud crash",
        salience=0.8,
        current_tick=5,
    )

    # Should NOT be available on tick 5
    assert len(bus.get_pending_events(5, "kami_b")) == 0
    # Should be available on tick 6
    events = bus.get_pending_events(6, "kami_b")
    assert len(events) == 1
    assert events[0].salience == 0.8


def test_broadcast_with_attenuation():
    bus = EventBus()
    bus.publish_broadcast(
        source_kami_id="kami_a",
        text="Music playing",
        salience=0.6,
        current_tick=10,
        neighbor_kami_ids=["kami_b", "kami_c"],
        attenuation_map={"kami_b": 0.3, "kami_c": 0.8},
    )

    b_broadcasts = bus.get_broadcasts(11, "kami_b")
    c_broadcasts = bus.get_broadcasts(11, "kami_c")
    assert len(b_broadcasts) == 1
    assert len(c_broadcasts) == 1
    # kami_c should have lower effective salience due to higher attenuation
    assert "0.42" in b_broadcasts[0]  # 0.6 * (1 - 0.3) = 0.42
    assert "0.12" in c_broadcasts[0]  # 0.6 * (1 - 0.8) = 0.12


def test_cleanup():
    bus = EventBus()
    bus.propagate_event("e1", "a", "b", "test", "test", 0.5, 1)
    bus.cleanup_tick(2)
    assert len(bus.get_pending_events(2, "b")) == 0


def test_get_all_pending_kami_ids():
    bus = EventBus()
    bus.propagate_event("e1", "a", "b", "test", "test", 0.5, 1)
    bus.propagate_event("e2", "a", "c", "test", "test", 0.5, 1)

    ids = bus.get_all_pending_kami_ids(2)
    assert ids == {"b", "c"}
