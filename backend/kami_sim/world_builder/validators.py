"""Validators between WorldBuilder cascades (spec §2.10)."""

from __future__ import annotations

import logging

from ..spatial.graph import SpatialGraph

logger = logging.getLogger(__name__)


def validate_after_decomposition(
    kami_specs: list[dict],
    graph: SpatialGraph,
    world_seed: dict,
) -> list[str]:
    """Validate after Cascade 2."""
    errors = []

    # Graph connectivity
    if not graph.is_connected():
        errors.append("Spatial graph is not connected — some kami are unreachable")

    # Landmarks materialized
    landmarks_text = world_seed.get("landmarks", "").lower()
    kami_names = {k.get("name", "").lower() for k in kami_specs}
    # Just warn, don't fail
    if landmarks_text and not any(name in landmarks_text for name in kami_names):
        logger.warning("Some landmarks may not have materialized as kami")

    # No orphan nodes
    for node in graph.all_kami_ids():
        if len(graph.get_neighbors(node)) == 0:
            errors.append(f"Orphan kami with no connections: {node}")

    return errors


def validate_after_population(
    agents: list[dict],
    kami_specs: list[dict],
    world_seed: dict,
) -> list[str]:
    """Validate after Cascade 3."""
    errors = []
    kami_ids = {k["entity_id"] for k in kami_specs}

    for agent in agents:
        # Valid home
        home = agent.get("home", "")
        if home and home not in kami_ids:
            errors.append(f"Agent {agent['name']} has invalid home: {home}")

        # Valid work
        work = agent.get("work", "")
        if work and work != "none" and work not in kami_ids:
            errors.append(f"Agent {agent['name']} has invalid workplace: {work}")

    return errors


def validate_social_fabric(
    relationships: list[dict],
    agents: list[dict],
) -> list[str]:
    """Validate after Cascade 4."""
    errors = []
    agent_names = {a["name"] for a in agents}

    for rel in relationships:
        for name in rel.get("names", []):
            if name not in agent_names:
                errors.append(f"Phantom reference in relationship: {name}")

    # Check for isolated agents (no relationships)
    connected = set()
    for rel in relationships:
        for name in rel.get("names", []):
            connected.add(name)

    isolated = agent_names - connected
    if len(isolated) > len(agents) * 0.2:
        errors.append(f"{len(isolated)} agents have no relationships (>20%)")

    return errors
