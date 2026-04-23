"""Cascade 4 — Social fabric generation (spec §2.10).

Algorithmic proximity candidates + LLM-generated relationships.
"""

from __future__ import annotations

import logging

from ...llm.client import llm_client

logger = logging.getLogger(__name__)


async def generate_social_fabric(
    agents: list[dict],
    world_seed: dict,
    batch_size: int = 15,
) -> list[dict]:
    """Generate relationships between agents.

    Returns list of relationship specs.
    """
    # Phase 1: Algorithmic candidate generation
    candidates = _generate_candidates(agents)

    # Phase 2: LLM enrichment in batches
    relationships = []
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        batch_rels = await _enrich_batch(batch, agents, world_seed)
        relationships.extend(batch_rels)
        logger.info(f"Social fabric batch: {len(batch_rels)} relationships generated")

    return relationships


def _generate_candidates(agents: list[dict]) -> list[tuple[int, int, str]]:
    """Generate candidate relationship pairs from spatial/role proximity."""
    candidates = []

    for i, a in enumerate(agents):
        for j, b in enumerate(agents):
            if i >= j:
                continue

            reason = None
            # Neighbors (same home building)
            if a.get("home") and a.get("home") == b.get("home"):
                reason = "neighbors"
            # Coworkers
            elif a.get("work") and a.get("work") == b.get("work"):
                reason = "coworkers"
            # Age-group peers (within 5 years)
            elif isinstance(a.get("age"), int) and isinstance(b.get("age"), int):
                if abs(a["age"] - b["age"]) <= 5:
                    reason = "age_peers"

            if reason:
                candidates.append((i, j, reason))

    return candidates


async def _enrich_batch(
    candidates: list[tuple[int, int, str]],
    agents: list[dict],
    world_seed: dict,
) -> list[dict]:
    """Use LLM to generate relationship details for a batch of candidate pairs."""
    pairs_text = []
    for i, j, reason in candidates:
        a = agents[i]
        b = agents[j]
        pairs_text.append(
            f"- {a['name']} ({a.get('role', '?')}) and {b['name']} ({b.get('role', '?')}) — {reason}"
        )

    if not pairs_text:
        return []

    try:
        response = await llm_client.call(
            messages=[{
                "role": "user",
                "content": f"""For each pair of people who likely know each other, generate a brief relationship.

Town: {world_seed.get('town_name', 'Town')}

Pairs:
{chr(10).join(pairs_text)}

For each pair, output:
PAIR: [Name A] / [Name B]
TYPE: [knows, trusts, friends_with, married_to, employs, fears, etc.]
STRENGTH: [0.0-1.0]
ORIGIN: [1 sentence about how they know each other]

Skip pairs that wouldn't realistically know each other.""",
            }],
            system="Generate realistic relationships between town residents.",
            tier="cheap",
            component="WorldBuilder",
            max_tokens=2000,
        )

        return _parse_relationships(response.get("content", ""), agents)
    except Exception as e:
        logger.error(f"Social fabric batch failed: {e}")
        return []


def _parse_relationships(text: str, agents: list[dict]) -> list[dict]:
    """Parse relationship output."""
    relationships = []
    current = {}

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("PAIR:"):
            if current.get("names"):
                relationships.append(current)
            names = line[5:].strip().split("/")
            current = {"names": [n.strip() for n in names]}
        elif line.startswith("TYPE:"):
            current["rel_type"] = line[5:].strip()
        elif line.startswith("STRENGTH:"):
            try:
                current["strength"] = float(line[9:].strip())
            except ValueError:
                current["strength"] = 0.5
        elif line.startswith("ORIGIN:"):
            current["origin"] = line[7:].strip()

    if current.get("names"):
        relationships.append(current)

    return relationships
