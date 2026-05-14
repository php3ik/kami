"""WorldBuilder CLI — spec §3.2 Phase 5.

Usage: python -m kami_sim.world_builder.build_world --prompt "..." --output world.json
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from pathlib import Path

from ..factstore import tools as fs
from ..spatial.graph import SpatialGraph

from .dynamic_world import build_dynamic_world

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def build_world(prompt: str, agent_count: int = 100, name: str | None = None) -> dict:
    """Generate a complete world dynamically from the user's text.

    No domain-specific fallback templates are used. If the model cannot produce
    a valid structured world after repair, the caller receives an error instead
    of a misleading canned world.
    """
    logger.info("Generating dynamic world from prompt...")
    return await build_dynamic_world(prompt, agent_count=agent_count, name=name)


def main():
    parser = argparse.ArgumentParser(description="Build a world for Kami Simulation")
    parser.add_argument("--prompt", type=str, required=True, help="World premise")
    parser.add_argument("--output", type=str, default="world.json", help="Output file")
    parser.add_argument("--agents", type=int, default=100, help="Number of agents")
    parser.add_argument("--name", type=str, default=None, help="Optional world name")
    args = parser.parse_args()

    import asyncio
    result = asyncio.run(build_world(args.prompt, agent_count=args.agents, name=args.name))

    Path(args.output).write_text(json.dumps(result, indent=2, default=str))
    logger.info(f"World written to {args.output}")
    logger.info(f"Budget: {result['budget']}")


if __name__ == "__main__":
    main()


def load_world_into_db(session, data: dict, simulation_id: str | None = None) -> SpatialGraph:
    sg = SpatialGraph()
    prefix = f"sim_{simulation_id}__" if simulation_id else ""

    def scoped_id(raw: str) -> str:
        if not prefix or raw.startswith(prefix):
            return raw
        return f"{prefix}{raw}"
    
    # 1. Kami
    for k in data["kami_specs"]:
        original_id = k.get("entity_id") or f"kami_{uuid.uuid4().hex[:8]}"
        kami_id = scoped_id(original_id)
        k["entity_id"] = kami_id
        k["simulation_id"] = simulation_id
        ambient_objects = k.get("ambient_objects") or []
        if ambient_objects:
            k["description"] = (
                f"{k.get('description', '')}\n\n"
                f"Local history: {k.get('history', '')}\n"
                f"Current situation: {k.get('current_situation', '')}\n"
                f"Visible objects and fixtures: {', '.join(str(item) for item in ambient_objects)}"
            ).strip()
        fs.create_entity(session, kind="kami", canonical_name=k["name"], tick=0, archetype=k, entity_id=kami_id)
        sg.add_kami(kami_id, name=k["name"], kind=k.get("kind", "unknown"))
        
    for edge in data["spatial_graph"]["edges"]:
        sg.add_edge(scoped_id(edge["source"]), scoped_id(edge["target"]), edge_type="adjacent",
                    visual_attenuation=edge.get("visual_attenuation", 0.5),
                    audio_attenuation=edge.get("audio_attenuation", 0.5))
                    
    # 2. Agents
    kami_ids = [k.get("entity_id") for k in data["kami_specs"]]
    for idx, a in enumerate(data["agents"]):
        original_agent_id = a.get("entity_id") or f"agent_{idx}"
        agent_id = scoped_id(original_agent_id)
        a["entity_id"] = agent_id
        a["simulation_id"] = simulation_id
        if a.get("home"):
            a["home"] = scoped_id(a["home"])
        if a.get("work"):
            a["work"] = scoped_id(a["work"])
        
        fs.create_entity(session, kind="agent", canonical_name=a["name"], tick=0, archetype=a, entity_id=agent_id)
        # Attempt to bind agent to their native home
        kami_id = a.get("home") if a.get("home") in kami_ids else data["kami_specs"][0].get("entity_id")
        fs.place_entity(session, agent_id, kami_id, tick=0)

    # 3. Relationships
    name_to_id = {a["name"]: a["entity_id"] for a in data["agents"]}
    for r in data["relationships"]:
        names = r.get("names", [])
        if len(names) >= 2:
            a1 = name_to_id.get(names[0])
            a2 = name_to_id.get(names[1])
            if a1 and a2:
                rel_type = r.get("rel_type", "knows")
                weight = {
                    "context": r.get("story", ""),
                    "trust": r.get("trust"),
                    "tension": r.get("tension"),
                }
                fs.update_relation(session, a1, a2, "knows", tick=0, weight=weight)
                fs.update_relation(session, a2, a1, "knows", tick=0, weight=weight)
                fs.update_relation(session, a1, a2, rel_type, tick=0, weight=weight)
                fs.update_relation(session, a2, a1, rel_type, tick=0, weight=weight)

    # 4. Objects and local props
    valid_kamis = {k.get("entity_id") for k in data["kami_specs"]}
    for idx, obj in enumerate(data.get("objects", [])):
        object_id = obj.get("entity_id") or f"obj_{uuid.uuid4().hex[:8]}"
        object_id = scoped_id(object_id)
        kami_id = scoped_id(obj.get("kami_id")) if obj.get("kami_id") else None
        if kami_id not in valid_kamis:
            continue
        kind = obj.get("kind", "object")
        if kind not in {"object", "document", "vehicle", "plant", "animal"}:
            kind = "object"
        archetype = {
            **obj,
            "entity_id": object_id,
            "simulation_id": simulation_id,
            "description": obj.get("description", ""),
        }
        fs.create_entity(
            session,
            kind=kind,
            canonical_name=obj.get("name") or f"Object {idx + 1}",
            tick=0,
            archetype=archetype,
            entity_id=object_id,
        )
        fs.place_entity(session, object_id, kami_id, tick=0)
        state = obj.get("state") if isinstance(obj.get("state"), dict) else {}
        for attribute, value in state.items():
            fs.change_state(session, object_id, str(attribute), value, tick=0)

    # Initial physical states
    for a in data["agents"]:
        needs = a.get("needs", {})
        fs.change_state(session, a["entity_id"], "fatigue", needs.get("fatigue", 0.0), tick=0)
        fs.change_state(session, a["entity_id"], "hunger", needs.get("hunger", 0.0), tick=0)
        for need, value in needs.items():
            fs.set_agent_need(session, a["entity_id"], need, value, tick=0)
        
    session.commit()
    return sg
