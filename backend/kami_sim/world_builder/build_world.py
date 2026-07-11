"""WorldBuilder CLI — spec §3.2 Phase 5.

Usage: python -m kami_sim.world_builder.build_world --prompt "..." --output world.json
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
from pathlib import Path

from ..config import config
from ..determinism import generate_id
from ..factstore import tools as fs
from ..language import normalize_content_language
from ..memory.episodic_store import seed_archetype_memories
from ..spatial.graph import SpatialGraph

from .staged import build_staged_world

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def build_world(
    prompt: str,
    agent_count: int = 100,
    name: str | None = None,
    content_language: str = "en",
    *,
    checkpoint: dict | None = None,
    progress_callback=None,
    checkpoint_callback=None,
    cancel_check=None,
) -> dict:
    """Generate a complete world dynamically from the user's text.

    No domain-specific fallback templates are used. If the model cannot produce
    a valid structured world after repair, the caller receives an error instead
    of a misleading canned world.
    """
    logger.info("Generating dynamic world from prompt...")
    return await build_staged_world(
        prompt,
        agent_count=agent_count,
        name=name,
        content_language=content_language,
        checkpoint=checkpoint,
        progress_callback=progress_callback,
        checkpoint_callback=checkpoint_callback,
        cancel_check=cancel_check,
    )


def main():
    parser = argparse.ArgumentParser(description="Build a world for Kami Simulation")
    parser.add_argument("--prompt", type=str, required=True, help="World premise")
    parser.add_argument("--output", type=str, default="world.json", help="Output file")
    parser.add_argument("--agents", type=int, default=100, help="Number of agents")
    parser.add_argument("--name", type=str, default=None, help="Optional world name")
    parser.add_argument(
        "--language",
        choices=("en", "uk"),
        default="en",
        help="Language for all diegetic world content",
    )
    parser.add_argument(
        "--import-db",
        action="store_true",
        help="Import the completed world into the configured database",
    )
    parser.add_argument(
        "--simulation-id",
        type=str,
        default=None,
        help="Simulation scope used with --import-db",
    )
    args = parser.parse_args()

    import asyncio
    result = asyncio.run(
        build_world(
            args.prompt,
            agent_count=args.agents,
            name=args.name,
            content_language=normalize_content_language(args.language),
        )
    )

    Path(args.output).write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info(f"World written to {args.output}")
    logger.info(f"Budget: {result['budget']}")
    if args.import_db:
        from ..factstore.models import init_db

        engine, session_factory = init_db(config.database_url)
        session = session_factory()
        try:
            load_world_into_db(
                session,
                result,
                simulation_id=args.simulation_id or "cli",
            )
            logger.info("World imported into %s", config.database_url)
        finally:
            session.close()
            engine.dispose()


def load_world_into_db(
    session,
    data: dict,
    simulation_id: str | None = None,
    *,
    commit: bool = True,
) -> SpatialGraph:
    """Load one complete world atomically and leave no partial rows on failure."""
    try:
        return _load_world_into_db(
            session, data, simulation_id=simulation_id, commit=commit
        )
    except Exception:
        session.rollback()
        raise


def _load_world_into_db(
    session,
    data: dict,
    simulation_id: str | None = None,
    *,
    commit: bool = True,
) -> SpatialGraph:
    data = copy.deepcopy(data)
    sg = SpatialGraph()
    prefix = f"sim_{simulation_id}__" if simulation_id else ""

    def scoped_id(raw: str) -> str:
        if not prefix or raw.startswith(prefix):
            return raw
        return f"{prefix}{raw}"
    
    # 1. Kami
    for k in data["kami_specs"]:
        original_id = k.get("entity_id") or generate_id("kami_", 8)
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
        fs.create_entity(
            session,
            kind="kami",
            canonical_name=k["name"],
            tick=0,
            archetype=k,
            entity_id=kami_id,
            simulation_id=simulation_id,
        )
        sg.add_kami(kami_id, name=k["name"], kind=k.get("kind", "unknown"))
        
    for edge in data["spatial_graph"]["edges"]:
        sg.add_edge(scoped_id(edge["source"]), scoped_id(edge["target"]), edge_type=edge.get("edge_type", "adjacent"),
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

    name_to_id = {a["name"].casefold(): a["entity_id"] for a in data["agents"]}
    if len(name_to_id) != len(data["agents"]):
        raise ValueError("Agent names must be unique before world import")

    for a in data["agents"]:
        agent_id = a["entity_id"]
        agent_entity = fs.create_entity(
            session,
            kind="agent",
            canonical_name=a["name"],
            tick=0,
            archetype=a,
            entity_id=agent_id,
            simulation_id=simulation_id,
        )
        memories = []
        for raw_memory in list(a.get("memories") or []):
            memory = dict(raw_memory) if isinstance(raw_memory, dict) else {"content": str(raw_memory)}
            participants = []
            for participant in memory.get("participants") or []:
                raw = str(participant)
                resolved = name_to_id.get(raw.casefold())
                if resolved is None and raw.startswith("agent_"):
                    resolved = scoped_id(raw)
                if resolved:
                    participants.append(resolved)
            memory["participants"] = list(dict.fromkeys(participants))
            memories.append(memory)
        seed_archetype_memories(
            session,
            agent_entity,
            memories,
            life_narrative=str(a.get("life_narrative") or ""),
        )
        # Attempt to bind agent to their native home
        kami_id = a.get("home") if a.get("home") in kami_ids else data["kami_specs"][0].get("entity_id")
        fs.place_entity(session, agent_id, kami_id, tick=0)
        if a.get("home") in kami_ids:
            fs.update_relation(session, agent_id, a["home"], "lives_in", tick=0)
        if a.get("work") in kami_ids:
            fs.update_relation(session, agent_id, a["work"], "works_at", tick=0)
        _create_initial_schedules(session, a)

    # 3. Relationships
    for r in data["relationships"]:
        names = r.get("names", [])
        if len(names) >= 2:
            a1 = name_to_id.get(str(names[0]).casefold())
            a2 = name_to_id.get(str(names[1]).casefold())
            if a1 and a2:
                rel_type = _canonical_rel_type(str(r.get("rel_type") or "knows"))
                weight = {
                    "context": r.get("story", ""),
                    "trust": r.get("trust"),
                    "tension": r.get("tension"),
                    "generated_label": r.get("rel_type", "knows"),
                }
                fs.update_relation(session, a1, a2, "knows", tick=0, weight=weight)
                fs.update_relation(session, a2, a1, "knows", tick=0, weight=weight)
                if rel_type != "knows":
                    fs.update_relation(session, a1, a2, rel_type, tick=0, weight=weight)
                    fs.update_relation(session, a2, a1, rel_type, tick=0, weight=weight)

    # 4. Objects and local props
    valid_kamis = {k.get("entity_id") for k in data["kami_specs"]}
    for idx, obj in enumerate(data.get("objects", [])):
        object_id = obj.get("entity_id") or generate_id("obj_", 8)
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
            simulation_id=simulation_id,
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
        fs.change_state(session, a["entity_id"], "activity", "sleeping", tick=0)
        for need, value in needs.items():
            fs.set_agent_need(session, a["entity_id"], need, value, tick=0)
        
    if commit:
        session.commit()
    return sg


def _canonical_rel_type(value: str) -> str:
    lowered = value.casefold()
    if any(item in lowered for item in ("married", "spouse", "partner")):
        return "married_to"
    if "parent" in lowered or "mentor" in lowered:
        return "parent_of" if "parent" in lowered else "trusts"
    if "sibling" in lowered:
        return "sibling_of"
    if any(item in lowered for item in ("friend", "ally", "compan")):
        return "friends_with"
    if "owe" in lowered or "debt" in lowered or "obligation" in lowered:
        return "owes"
    if "fear" in lowered:
        return "fears"
    if "trust" in lowered or "confid" in lowered:
        return "trusts"
    return "knows"


def _create_initial_schedules(session, agent: dict) -> None:
    home = agent.get("home")
    work = agent.get("work")
    if not home or not work:
        return
    ticks_per_day = max(1, round((24 * 60) / config.tick_in_sim_minutes))
    morning = max(1, round((8 * 60) / config.tick_in_sim_minutes))
    midday = max(1, round((12 * 60) / config.tick_in_sim_minutes))
    evening = max(1, round((18 * 60) / config.tick_in_sim_minutes))
    night = max(1, round((23 * 60) / config.tick_in_sim_minutes))
    for day in range(7):
        offset = day * ticks_per_day
        fs.create_schedule(
            session,
            offset + morning,
            home,
            {
                "event_type": "routine_departure",
                "agent_id": agent["entity_id"],
                "target_kami_id": work,
                "instruction": "Wake the agent, set activity to awake, and start a plausible route toward work.",
            },
        )
        fs.create_schedule(
            session,
            offset + midday,
            work,
            {
                "event_type": "midday_routine",
                "agent_id": agent["entity_id"],
                "instruction": "Resolve a grounded midday need, obligation, or social beat.",
            },
        )
        fs.create_schedule(
            session,
            offset + evening,
            work,
            {
                "event_type": "routine_return",
                "agent_id": agent["entity_id"],
                "target_kami_id": home,
                "instruction": "The work period ends; start a plausible route home.",
            },
        )
        fs.create_schedule(
            session,
            offset + night,
            home,
            {
                "event_type": "night_routine",
                "agent_id": agent["entity_id"],
                "instruction": "If the agent is home, close the day and set activity to sleeping.",
            },
        )


if __name__ == "__main__":
    main()
