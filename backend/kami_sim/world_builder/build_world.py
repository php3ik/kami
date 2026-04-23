"""WorldBuilder CLI — spec §3.2 Phase 5.

Usage: python -m kami_sim.world_builder.build_world --prompt "..." --output world.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

from ..factstore import tools as fs
from ..spatial.graph import SpatialGraph

from ..llm.budget import budget
from .cascades.seed import generate_world_seed
from .cascades.decompose import decompose_town
from .cascades.populate import generate_population
from .cascades.social import generate_social_fabric
from .cascades.backstory import generate_backstory
from .validators import (
    validate_after_decomposition,
    validate_after_population,
    validate_social_fabric,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def build_world(prompt: str, agent_count: int = 100) -> dict:
    """Run all 5 cascades to generate a complete world."""

    # Cascade 1: World seed
    logger.info("Cascade 1: Generating world seed...")
    world_seed = await generate_world_seed(prompt)
    logger.info(f"World seed: {world_seed.get('town_name', 'unnamed')}")

    # Cascade 2: Spatial decomposition
    logger.info("Cascade 2: Spatial decomposition...")
    kami_specs, spatial_graph = await decompose_town(world_seed)
    logger.info(f"Generated {len(kami_specs)} kami")

    # Validate
    errors = validate_after_decomposition(kami_specs, spatial_graph, world_seed)
    for e in errors:
        logger.warning(f"Validation: {e}")

    # Cascade 2.5: Slot inventory
    residential_count = len([k for k in kami_specs if k.get("kind") == "residential"])
    work_count = len([k for k in kami_specs if k.get("kind") in ("commercial", "industrial", "institutional")])
    logger.info(f"Slots: {residential_count} residential, {work_count} work")

    # Cascade 3: Population
    logger.info(f"Cascade 3: Generating {agent_count} agents...")
    agents = await generate_population(world_seed, kami_specs, target_count=agent_count)
    logger.info(f"Generated {len(agents)} agents")

    errors = validate_after_population(agents, kami_specs, world_seed)
    for e in errors:
        logger.warning(f"Validation: {e}")

    # Cascade 4: Social fabric
    logger.info("Cascade 4: Generating social fabric...")
    relationships = await generate_social_fabric(agents, world_seed)
    logger.info(f"Generated {len(relationships)} relationships")

    errors = validate_social_fabric(relationships, agents)
    for e in errors:
        logger.warning(f"Validation: {e}")

    # Cascade 5: Backstory injection
    logger.info("Cascade 5: Generating backstories...")
    backstories = {}
    for i, agent in enumerate(agents):
        agent_rels = [r for r in relationships if agent["name"] in r.get("names", [])]
        backstory = await generate_backstory(agent, agent_rels, world_seed)
        backstories[agent["name"]] = backstory
        if (i + 1) % 10 == 0:
            logger.info(f"Backstory progress: {i + 1}/{len(agents)}")

    # Assemble output
    output = {
        "world_seed": world_seed,
        "kami_specs": kami_specs,
        "spatial_graph": spatial_graph.to_dict(),
        "agents": agents,
        "relationships": relationships,
        "backstories": backstories,
        "budget": budget.get_summary(),
    }

    return output


def main():
    parser = argparse.ArgumentParser(description="Build a world for Kami Simulation")
    parser.add_argument("--prompt", type=str, required=True, help="World premise")
    parser.add_argument("--output", type=str, default="world.json", help="Output file")
    parser.add_argument("--agents", type=int, default=100, help="Number of agents")
    args = parser.parse_args()

    result = asyncio.run(build_world(args.prompt, agent_count=args.agents))

    Path(args.output).write_text(json.dumps(result, indent=2, default=str))
    logger.info(f"World written to {args.output}")
    logger.info(f"Budget: {result['budget']}")


if __name__ == "__main__":
    main()


def load_world_into_db(session, data: dict) -> SpatialGraph:
    sg = SpatialGraph()
    
    # 1. Kami
    for k in data["kami_specs"]:
        fs.create_entity(session, kind="kami", canonical_name=k["name"], tick=0, archetype=k, entity_id=k.get("entity_id", f"kami_{uuid.uuid4().hex[:8]}"))
        sg.add_kami(k.get("entity_id", f"kami_{uuid.uuid4().hex[:8]}"), name=k["name"], kind=k.get("kind", "unknown"))
        
    for edge in data["spatial_graph"]["edges"]:
        sg.add_edge(edge["source"], edge["target"], edge_type="adjacent",
                    visual_attenuation=edge.get("visual_attenuation", 0.5),
                    audio_attenuation=edge.get("audio_attenuation", 0.5))
                    
    # 2. Agents
    for idx, a in enumerate(data["agents"]):
        agent_id = f"agent_{idx}"
        a["entity_id"] = agent_id
        
        fs.create_entity(session, kind="agent", canonical_name=a["name"], tick=0, archetype=a, entity_id=agent_id)
        # Attempt to bind agent to their native home
        kami_id = a.get("home") if a.get("home") in [k.get("entity_id") for k in data["kami_specs"]] else data["kami_specs"][0].get("entity_id")
        fs.place_entity(session, agent_id, kami_id, tick=0)

    # 3. Relationships
    name_to_id = {a["name"]: a["entity_id"] for a in data["agents"]}
    for r in data["relationships"]:
        names = r.get("names", [])
        if len(names) >= 2:
            a1 = name_to_id.get(names[0])
            a2 = name_to_id.get(names[1])
            if a1 and a2:
                fs.update_relation(session, a1, a2, "knows", tick=0, weight={"context": r.get("story", "")})
                fs.update_relation(session, a2, a1, "knows", tick=0, weight={"context": r.get("story", "")})

    # Initial physical states
    for a in data["agents"]:
        fs.change_state(session, a["entity_id"], "fatigue", 0.0, tick=0)
        fs.change_state(session, a["entity_id"], "hunger", 0.0, tick=0)
        
    session.commit()
    return sg
