"""Cascade 2 — Spatial decomposition (spec §2.10).

Recursive decomposition: town -> districts -> buildings -> floors -> rooms.
Each call generates kami specs + relative topology.
"""

from __future__ import annotations

import logging

from ...llm.client import llm_client
from ...spatial.graph import SpatialGraph

logger = logging.getLogger(__name__)


async def decompose_town(world_seed: dict) -> tuple[list[dict], SpatialGraph]:
    """Decompose town into kami based on world seed.

    Returns (kami_specs, spatial_graph).
    """
    districts = world_seed.get("districts", "")
    landmarks = world_seed.get("landmarks", "")
    town_name = world_seed.get("town_name", "Town")

    response = await llm_client.call(
        messages=[{
            "role": "user",
            "content": f"""Decompose this town into specific locations (kami/rooms/spaces).

Town: {town_name}
Districts: {districts}
Landmarks: {landmarks}
Geography: {world_seed.get('geography', '')}

Generate 30-60 specific locations organized by district. For each location provide:
- ID (snake_case, e.g., kami_main_st_bakery)
- NAME (human readable)
- KIND (one of: residential, commercial, public_outdoor, public_indoor, industrial, institutional, transit)
- DISTRICT (which district it belongs to)
- DESCRIPTION (1-2 sentences, atmospheric)
- CAPACITY (max people)
- CONNECTIONS (list of adjacent kami IDs with edge type: adjacent, contains, transit_route)

Format each as:
---
ID: kami_xxx
NAME: The Xxx
KIND: commercial
DISTRICT: downtown
DESCRIPTION: A cozy...
CAPACITY: 15
CONNECTS_TO: kami_yyy (adjacent), kami_zzz (contains)
---

Important:
- Every residential building should have individual apartments/rooms
- Include streets, parks, and transit connections
- Ensure the graph is fully connected
- Include at least 20-30 residential units for ~100 people""",
        }],
        system=f"You are decomposing the town of {town_name} into a spatial graph of locations. Be thorough and ensure connectivity.",
        tier="strong",
        component="WorldBuilder",
        max_tokens=4000,
        temperature=0.7,
    )

    text = response.get("content", "")
    kami_specs, graph = _parse_decomposition(text)

    return kami_specs, graph


def _parse_decomposition(text: str) -> tuple[list[dict], SpatialGraph]:
    """Parse LLM output into kami specs and spatial graph."""
    specs = []
    graph = SpatialGraph()

    # Split by --- separator
    blocks = text.split("---")

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        spec = {}
        connections = []

        for line in block.split("\n"):
            line = line.strip()
            if line.startswith("ID:"):
                spec["entity_id"] = line[3:].strip()
            elif line.startswith("NAME:"):
                spec["name"] = line[5:].strip()
            elif line.startswith("KIND:"):
                spec["kind"] = line[5:].strip()
            elif line.startswith("DISTRICT:"):
                spec["district"] = line[9:].strip()
            elif line.startswith("DESCRIPTION:"):
                spec["description"] = line[12:].strip()
            elif line.startswith("CAPACITY:"):
                try:
                    spec["capacity"] = int(line[9:].strip())
                except ValueError:
                    spec["capacity"] = 10
            elif line.startswith("CONNECTS_TO:"):
                conn_str = line[12:].strip()
                for conn in conn_str.split(","):
                    conn = conn.strip()
                    if "(" in conn:
                        parts = conn.split("(")
                        target = parts[0].strip()
                        edge_type = parts[1].rstrip(")").strip()
                    else:
                        target = conn
                        edge_type = "adjacent"
                    if target:
                        connections.append((target, edge_type))

        if spec.get("entity_id"):
            spec["connections"] = connections
            specs.append(spec)
            graph.add_kami(
                spec["entity_id"],
                name=spec.get("name", ""),
                kind=spec.get("kind", "location"),
                district=spec.get("district", ""),
            )

    # Add edges
    for spec in specs:
        for target, edge_type in spec.get("connections", []):
            if target in [s["entity_id"] for s in specs]:
                att_visual = 0.9 if edge_type == "contains" else 0.1
                att_audio = 0.7 if edge_type == "contains" else 0.2
                graph.add_edge(
                    spec["entity_id"], target,
                    edge_type=edge_type,
                    visual_attenuation=att_visual,
                    audio_attenuation=att_audio,
                )

    return specs, graph
