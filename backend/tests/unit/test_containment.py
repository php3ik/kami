"""Unit tests for epistemic containment."""

from kami_sim.agent_worker.containment import validate_agent_output, filter_perception


def test_valid_output():
    is_valid, violations = validate_agent_output(
        "I see Marcus near the bench. He looks tired.",
        known_names={"Marcus Cole", "Marcus"},
        perceived_names={"Marcus"},
    )
    assert is_valid


def test_invalid_output_unknown_name():
    # Validator now only catches multi-word names (First Last) to avoid
    # false positives from sentence-initial words and common nouns
    is_valid, violations = validate_agent_output(
        "I notice that Sarah Henderson is watching from the window.",
        known_names={"Marcus Cole"},
        perceived_names={"Marcus Cole"},
    )
    assert not is_valid
    assert "Sarah Henderson" in violations


def test_single_word_capitalized_not_flagged():
    # Single capitalized words (sentence starts, exclamations, nouns) should NOT be flagged
    is_valid, violations = validate_agent_output(
        "Man, I need some Coffee. Let me think about Neruda.",
        known_names={"Marcus Cole"},
        perceived_names=set(),
    )
    assert is_valid
    assert violations == []


def test_filter_perception_unknown_agent():
    kami_state = {
        "kami_id": "kami_1",
        "entities": [
            {
                "entity_id": "agent_a",
                "kind": "agent",
                "name": "Alice",
                "archetype": {"appearance": "tall, red hair"},
                "states": {},
            },
            {
                "entity_id": "obj_1",
                "kind": "object",
                "name": "Chair",
                "archetype": {},
                "states": {},
            },
        ],
    }

    # Agent who doesn't know Alice
    filtered = filter_perception(kami_state, "agent_b", social_graph=set())
    agent_entry = [e for e in filtered["entities"] if e["kind"] == "agent"][0]
    assert "Alice" not in agent_entry["name"]
    assert "unfamiliar" in agent_entry["name"]

    # Object names are always visible
    obj_entry = [e for e in filtered["entities"] if e["kind"] == "object"][0]
    assert obj_entry["name"] == "Chair"


def test_filter_perception_known_agent():
    kami_state = {
        "kami_id": "kami_1",
        "entities": [
            {
                "entity_id": "agent_a",
                "kind": "agent",
                "name": "Alice",
                "archetype": {},
                "states": {},
            },
        ],
    }

    filtered = filter_perception(kami_state, "agent_b", social_graph={"agent_a"})
    agent_entry = filtered["entities"][0]
    assert agent_entry["name"] == "Alice"


def test_filter_excludes_self():
    kami_state = {
        "kami_id": "kami_1",
        "entities": [
            {"entity_id": "agent_a", "kind": "agent", "name": "Alice", "archetype": {}, "states": {}},
        ],
    }

    filtered = filter_perception(kami_state, "agent_a", social_graph=set())
    assert len(filtered["entities"]) == 0
