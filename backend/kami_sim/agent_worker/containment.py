"""Epistemic containment validators — spec §2.4.

Three layers of containment to prevent agents knowing things they shouldn't.
"""

from __future__ import annotations

import re
from typing import Any


def validate_agent_output(
    output_text: str,
    known_names: set[str],
    perceived_names: set[str],
) -> tuple[bool, list[str]]:
    """Structural validation: check that agent output only references known entities.

    Returns (is_valid, list_of_violations).
    """
    allowed_names = known_names | perceived_names
    violations = []

    # Only flag multi-word proper names that look like person names (First Last)
    # Single capitalized words produce too many false positives from
    # sentence-initial words, exclamations, common nouns, etc.
    # E.g. "Man", "Let", "Coffee", "Neruda", "Ugh", "Gotta" were all false positives.
    multi_word_names = re.findall(
        r'\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})+)\b', output_text
    )
    for name in multi_word_names:
        if name not in allowed_names:
            # Check if it's a substring of an allowed name or vice versa
            if not any(name in a or a in name for a in allowed_names):
                violations.append(name)

    return len(violations) == 0, violations


def filter_perception(
    kami_state: dict,
    agent_id: str,
    social_graph: set[str],
) -> dict:
    """Filter kami state to only what an agent can perceive.

    Agents can sense entities physically present but may not know names
    of people not in their social graph.
    """
    filtered = {
        "kami_id": kami_state["kami_id"],
        "entities": [],
    }

    for entity in kami_state.get("entities", []):
        eid = entity["entity_id"]
        if eid == agent_id:
            continue  # Don't include self in perception

        filtered_entity = {
            "entity_id": eid,
            "kind": entity["kind"],
            "states": entity.get("states", {}),
        }

        # Name resolution: only use real name if agent knows them
        if entity["kind"] == "agent":
            if eid in social_graph:
                filtered_entity["name"] = entity["name"]
            else:
                # Describe by appearance from archetype
                arch = entity.get("archetype", {})
                appearance = arch.get("appearance", "a person")
                filtered_entity["name"] = f"an unfamiliar person ({appearance})"
        else:
            filtered_entity["name"] = entity["name"]

        filtered["entities"].append(filtered_entity)

    return filtered
