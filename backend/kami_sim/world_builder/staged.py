"""Five-stage, bounded and resumable WorldBuilder pipeline."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import config
from ..llm.client import llm_client
from ..language import (
    language_instruction,
    normalize_content_language,
    structured_text_matches_language,
)
from ..spatial.graph import SpatialGraph
from .contracts import validate_complete_world
from .dynamic_world import (
    WORLD_SCHEMA_HINT,
    _normalize_agents,
    _normalize_kami_specs,
    _normalize_memories,
    _normalize_objects,
    _normalize_relationships,
    _normalize_world,
    _world_from_response,
)


ProgressCallback = Callable[[dict], Awaitable[None] | None]
CheckpointCallback = Callable[[dict], Awaitable[None] | None]
CancelCheck = Callable[[], bool | Awaitable[bool]]

STAGES = ("seed", "spatial", "population", "social_objects", "backstories")
PERSONA_DIVERSITY = (
    "unusual practical hobby",
    "non-obvious class background",
    "distinct speech cadence",
    "physical limitation or scar",
    "private ritual",
    "conflicted institutional loyalty",
    "rare technical competence",
    "unexpected caregiving role",
    "specific comic habit",
    "moral compromise they regret",
)


class WorldBuildCancelled(RuntimeError):
    pass


async def build_staged_world(
    prompt: str,
    agent_count: int,
    name: str | None = None,
    content_language: str = "en",
    *,
    checkpoint: dict | None = None,
    progress_callback: ProgressCallback | None = None,
    checkpoint_callback: CheckpointCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict:
    content_language = normalize_content_language(content_language)
    state = dict(checkpoint or {})
    completed = list(state.get("completed_stages") or [])
    world = dict(state.get("world") or {})

    async def finish_stage(stage: str, message: str) -> None:
        if stage not in completed:
            completed.append(stage)
        state.update(completed_stages=completed, world=world)
        await _invoke(checkpoint_callback, dict(state))
        await _progress(progress_callback, stage, len(completed), message)

    if "seed" not in completed:
        await _check_cancel(cancel_check)
        await _progress(progress_callback, "seed", 0, "Generating the world bible")
        world["world_seed"] = await _generate_seed(
            prompt, agent_count, name, content_language
        )
        await finish_stage("seed", "World bible complete")

    if "spatial" not in completed:
        await _check_cancel(cancel_check)
        await _progress(progress_callback, "spatial", 1, "Decomposing the spatial world")
        spatial = await _generate_spatial(prompt, agent_count, name, world["world_seed"])
        world["kami_specs"] = _normalize_kami_specs(spatial.get("kami_specs") or [])
        world["spatial_graph"] = _connect_graph(
            spatial.get("spatial_graph") or {}, world["kami_specs"]
        )
        state["slot_inventory"] = _build_slot_inventory(world["kami_specs"], agent_count)
        _validate_spatial(world, agent_count)
        await finish_stage("spatial", "Spatial graph and slot inventory complete")

    if "population" not in completed:
        await _check_cancel(cancel_check)
        await _progress(progress_callback, "population", 2, "Generating population batches")
        raw_agents: list[dict] = []
        for batch_index, start in enumerate(range(0, agent_count, 5)):
            await _check_cancel(cancel_check)
            batch_size = min(5, agent_count - start)
            batch = await _generate_population_batch(
                prompt,
                name,
                world,
                batch_size,
                start,
                raw_agents,
                state["slot_inventory"],
            )
            raw_agents.extend(batch)
            normalized = _normalize_agents(raw_agents, world["kami_specs"], len(raw_agents))
            _validate_population_progress(normalized, len(raw_agents))
            world["agents"] = normalized
            state.update(world=world, population_offset=len(raw_agents))
            await _invoke(checkpoint_callback, dict(state))
            await _progress(
                progress_callback,
                "population",
                2,
                f"Generated {len(raw_agents)}/{agent_count} agents",
                batch=batch_index + 1,
                batches=math.ceil(agent_count / 5),
            )
        await finish_stage("population", "Population complete")

    if "social_objects" not in completed:
        await _check_cancel(cancel_check)
        await _progress(progress_callback, "social_objects", 3, "Building social and object layers")
        candidates = _social_candidates(world["agents"])
        relationship_batches = [candidates[i:i + 15] for i in range(0, len(candidates), 15)]
        object_jobs = _object_jobs(world["kami_specs"], agent_count)
        relationship_results, object_results = await asyncio.gather(
            _bounded_map(
                relationship_batches,
                lambda batch: _generate_relationship_batch(prompt, world, batch),
                cancel_check,
            ),
            _bounded_map(
                object_jobs,
                lambda job: _generate_object_batch(prompt, world, job),
                cancel_check,
            ),
        )
        raw_relationships = [item for batch in relationship_results for item in batch]
        raw_objects = [item for batch in object_results for item in batch]
        world["relationships"] = _normalize_relationships(
            raw_relationships, world["agents"]
        )
        kami_ids = {kami["entity_id"] for kami in world["kami_specs"]}
        world["objects"] = _normalize_objects(raw_objects, kami_ids)
        _validate_social_objects(world, agent_count)
        await finish_stage("social_objects", "Relationships and objects complete")

    if "backstories" not in completed:
        await _check_cancel(cancel_check)
        await _progress(progress_callback, "backstories", 4, "Injecting lived backstories")
        agent_batches = [world["agents"][i:i + 3] for i in range(0, agent_count, 3)]
        results = await _bounded_map(
            agent_batches,
            lambda batch: _generate_backstory_batch(prompt, world, batch),
            cancel_check,
        )
        by_name = {
            item["name"]: item
            for batch in results
            for item in batch
            if isinstance(item, dict) and item.get("name")
        }
        for agent in world["agents"]:
            backstory = by_name.get(agent["name"])
            if backstory is None:
                raise ValueError(f"missing backstory for {agent['name']}")
            agent["life_narrative"] = str(backstory.get("life_narrative") or "").strip()
            agent["memories"] = _normalize_memories(backstory.get("memories") or [])
        world = _normalize_world(world, prompt, agent_count, name)
        world.setdefault("world_seed", {})["content_language"] = content_language
        errors = validate_complete_world(world, agent_count)
        if errors:
            raise ValueError("WorldBuilder validation failed: " + "; ".join(errors[:30]))
        await finish_stage("backstories", "World generation complete")

    world["slot_inventory"] = state.get("slot_inventory") or {}
    world.setdefault("world_seed", {})["content_language"] = content_language
    world["budget"] = {"pipeline": "staged_v2", "stages": list(STAGES)}
    return world


async def _generate_seed(
    prompt: str, agent_count: int, name: str | None, content_language: str = "en"
) -> dict:
    language_contract = language_instruction(content_language)
    result = await _json_call(
        f"{language_contract}\n\n"
        f"Create a grounded world bible from this exact simulation premise:\n{prompt}\n\n"
        f"World name: {name or 'derive one'}\nPopulation: {agent_count}.\n"
        "Return JSON with key world_seed containing name, premise, geography, history, "
        "economy, demographics, daily_rhythm, social_tensions, cultural_tone, and named factions.",
        "You are a world-bible architect. Return valid JSON only; use no canned setting.",
        tier="strong",
        max_tokens=3500,
        temperature=0.45,
        content_language=content_language,
    )
    seed = result.get("world_seed") if isinstance(result.get("world_seed"), dict) else result
    if not isinstance(seed, dict) or len(str(seed.get("premise") or "")) < 20:
        raise ValueError("WorldBuilder seed stage returned an incomplete world bible")
    seed.setdefault("name", name or seed.get("town_name") or "Generated World")
    seed.setdefault("premise", prompt)
    seed["content_language"] = normalize_content_language(content_language)
    return seed


async def _generate_spatial(
    prompt: str, agent_count: int, name: str | None, world_seed: dict
) -> dict:
    target = max(6, min(28, math.ceil(agent_count / 4) + 6))
    language_contract = language_instruction(world_seed.get("content_language"))
    return await _json_call(
        f"{language_contract}\n\nBuild the complete spatial layer for this world.\nPremise: {prompt}\n"
        f"World bible: {json.dumps(world_seed, ensure_ascii=False)}\n"
        f"Create {target} concrete Kami locations for {agent_count} people. Return JSON keys "
        "kami_specs and spatial_graph. Every Kami needs entity_id, name, kind, district, "
        "description, history, sensory details, current_situation, 6-12 ambient_objects, "
        "and capacity. Preserve meaningful doorway/contains/path/trail/transit_route edge types. "
        f"The graph must be connected. Contract hint: {json.dumps({'kami_specs': WORLD_SCHEMA_HINT['kami_specs'], 'spatial_graph': WORLD_SCHEMA_HINT['spatial_graph']}, ensure_ascii=False)}",
        "You decompose world bibles into connected, inspectable spatial graphs. Return JSON only.",
        tier="strong",
        max_tokens=14000,
        temperature=0.45,
        content_language=world_seed.get("content_language"),
    )


async def _generate_population_batch(
    prompt: str,
    name: str | None,
    world: dict,
    batch_size: int,
    start: int,
    existing: list[dict],
    slot_inventory: dict,
) -> list[dict]:
    diversity = [PERSONA_DIVERSITY[(start + i) % len(PERSONA_DIVERSITY)] for i in range(batch_size)]
    summaries = [
        {"name": item.get("name"), "role": item.get("role"), "traits": item.get("traits")}
        for item in existing
    ]
    language_contract = language_instruction(
        (world.get("world_seed") or {}).get("content_language")
    )
    result = await _json_call(
        f"{language_contract}\n\nGenerate exactly {batch_size} new distinct agents, indexes {start + 1}-{start + batch_size}.\n"
        f"Premise: {prompt}\nWorld: {json.dumps(world.get('world_seed'), ensure_ascii=False)}\n"
        f"Kami IDs and slots: {json.dumps(slot_inventory, ensure_ascii=False)}\n"
        f"Existing personas to avoid duplicating: {json.dumps(summaries, ensure_ascii=False)}\n"
        f"Diversity prompts, one per agent: {json.dumps(diversity, ensure_ascii=False)}\n"
        "Return {\"agents\": [...]}. Each needs a unique name and entity_id, age, role, exact "
        "home/work Kami IDs, appearance, 120-220 word background, 3+ private_history facts, "
        "4+ traits, 2+ fears, 2+ desires, distinctive voice, goals, and numeric needs. "
        "Do not generate final backstory memories yet.",
        "You generate grounded, diverse people in bounded batches. Return JSON only.",
        tier="strong",
        max_tokens=7500,
        temperature=0.75,
        content_language=(world.get("world_seed") or {}).get("content_language"),
    )
    agents = result.get("agents") or []
    if len(agents) != batch_size:
        raise ValueError(f"population batch expected {batch_size} agents, got {len(agents)}")
    return agents


async def _generate_relationship_batch(prompt: str, world: dict, pairs: list[dict]) -> list[dict]:
    language_contract = language_instruction(
        (world.get("world_seed") or {}).get("content_language")
    )
    result = await _json_call(
        f"{language_contract}\n\nEnrich these exact social candidate pairs for the world `{prompt}`:\n"
        f"{json.dumps(pairs, ensure_ascii=False)}\n"
        "Return {\"relationships\": [...]}, exactly one per pair, preserving exact names. "
        "Each needs rel_type, trust, tension, and a concrete 2-4 sentence origin with obligation, "
        "friction, affection, secret, or shared history.",
        "You build a plausible social fabric from spatial and occupational candidates. JSON only.",
        tier="cheap",
        max_tokens=5000,
        temperature=0.55,
        content_language=(world.get("world_seed") or {}).get("content_language"),
    )
    relationships = result.get("relationships") or []
    if len(relationships) != len(pairs):
        raise ValueError(
            f"relationship batch expected {len(pairs)} rows, got {len(relationships)}"
        )
    return relationships


async def _generate_object_batch(prompt: str, world: dict, job: dict) -> list[dict]:
    language_contract = language_instruction(
        (world.get("world_seed") or {}).get("content_language")
    )
    result = await _json_call(
        f"{language_contract}\n\nGenerate exactly {job['count']} interactive physical objects for these Kami in `{prompt}`:\n"
        f"{json.dumps(job['kami'], ensure_ascii=False)}\n"
        "Return {\"objects\": [...]}. Use exact kami_id values. Include tools, documents, supplies, "
        "personal items, communication devices, damaged items, evidence and affordances implied by "
        "the world. Every object needs entity_id, name, kind, kami_id, description, condition, "
        "uses, story, and state.",
        "You create grounded simulation affordances, not decorative filler. Return JSON only.",
        tier="cheap",
        max_tokens=5000,
        temperature=0.45,
        content_language=(world.get("world_seed") or {}).get("content_language"),
    )
    objects = result.get("objects") or []
    if len(objects) != job["count"]:
        raise ValueError(f"object batch expected {job['count']} rows, got {len(objects)}")
    return objects


async def _generate_backstory_batch(prompt: str, world: dict, agents: list[dict]) -> list[dict]:
    names = {agent["name"] for agent in agents}
    relationships = [
        rel for rel in world.get("relationships", [])
        if names.intersection(rel.get("names") or [])
    ]
    language_contract = language_instruction(
        (world.get("world_seed") or {}).get("content_language")
    )
    result = await _json_call(
        f"{language_contract}\n\nGenerate final backstories for these agents in `{prompt}`:\n"
        f"Agents: {json.dumps(agents, ensure_ascii=False)}\n"
        f"Relationships: {json.dumps(relationships, ensure_ascii=False)}\n"
        "Return {\"backstories\": [...]}, exactly one per agent with exact name, a 250-500 word "
        "first-person life_narrative and 10-14 concrete episodic memories. Each memory needs "
        "content, importance, and participants using exact agent names. Cover childhood, work, "
        "relationships, recent pressure, failures, tenderness, secrets, and current goals.",
        "You create internally consistent lived histories for simulation agents. Return JSON only.",
        tier="cheap",
        max_tokens=7500,
        temperature=0.55,
        content_language=(world.get("world_seed") or {}).get("content_language"),
    )
    backstories = result.get("backstories") or []
    if len(backstories) != len(agents):
        raise ValueError(f"backstory batch expected {len(agents)} rows, got {len(backstories)}")
    return backstories


async def _json_call(
    content: str,
    system: str,
    *,
    tier: str,
    max_tokens: int,
    temperature: float,
    content_language: str = "en",
) -> dict:
    last_error: Exception | None = None
    for attempt in range(2):
        prompt = content
        if attempt:
            prompt += (
                "\n\nThe prior response was invalid or used the wrong content language. "
                "Return one compact valid JSON object only and obey the content language contract."
            )
        response = await llm_client.call(
            messages=[{"role": "user", "content": prompt}],
            system=system,
            tier=tier,
            component="WorldBuilder",
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            temperature=temperature if attempt == 0 else min(0.25, temperature),
            timeout_seconds=config.world_builder_timeout_seconds,
        )
        try:
            result = _world_from_response(response)
            if not structured_text_matches_language(result, content_language):
                raise ValueError(
                    "WorldBuilder response violated the content language contract"
                )
            return result
        except ValueError as exc:
            last_error = exc
    raise last_error or ValueError("WorldBuilder returned no structured data")


def _build_slot_inventory(kami_specs: list[dict], agent_count: int) -> dict:
    residential = []
    work = []
    public = []
    for kami in kami_specs:
        row = {
            "kami_id": kami["entity_id"],
            "name": kami["name"],
            "kind": kami["kind"],
            "capacity": int(kami.get("capacity") or 1),
        }
        if kami["kind"] == "residential":
            residential.append(row)
        if kami["kind"] in {
            "commercial", "industrial", "institutional", "utility", "workshop",
            "public_indoor", "public_outdoor",
        }:
            work.append(row)
        if kami["kind"] in {"public_indoor", "public_outdoor", "commercial", "nature"}:
            public.append(row)
    if not residential:
        residential = [
            {"kami_id": item["entity_id"], "name": item["name"], "kind": item["kind"], "capacity": max(2, agent_count)}
            for item in kami_specs[:1]
        ]
    if not work:
        work = residential
    return {
        "population": agent_count,
        "residential_slots": residential,
        "work_slots": work,
        "public_slots": public,
    }


def _connect_graph(raw_graph: dict, kami_specs: list[dict]) -> dict:
    ids = [kami["entity_id"] for kami in kami_specs]
    valid = set(ids)
    edges = []
    graph = SpatialGraph()
    for kami_id in ids:
        graph.add_kami(kami_id)
    for raw in (raw_graph.get("edges") if isinstance(raw_graph, dict) else []) or []:
        if not isinstance(raw, dict):
            continue
        source, target = raw.get("source"), raw.get("target")
        if source not in valid or target not in valid or source == target:
            continue
        edge_type = str(raw.get("edge_type") or raw.get("type") or "adjacent")
        if edge_type not in {"adjacent", "contains", "path", "doorway", "trail", "transit_route"}:
            edge_type = "adjacent"
        edge = {
            "source": source,
            "target": target,
            "edge_type": edge_type,
            "visual_attenuation": _bounded_float(raw.get("visual_attenuation"), 0.25),
            "audio_attenuation": _bounded_float(raw.get("audio_attenuation"), 0.35),
        }
        edges.append(edge)
        graph.add_edge(source, target, edge_type=edge_type)
    components = _components(ids, edges)
    for left, right in zip(components, components[1:]):
        source, target = sorted(left)[0], sorted(right)[0]
        edge = {
            "source": source,
            "target": target,
            "edge_type": "adjacent",
            "visual_attenuation": 0.5,
            "audio_attenuation": 0.5,
            "generated_bridge": True,
        }
        edges.append(edge)
    return {
        "nodes": [
            {"id": kami["entity_id"], "name": kami["name"], "kind": kami["kind"]}
            for kami in kami_specs
        ],
        "edges": edges,
    }


def _components(ids: list[str], edges: list[dict]) -> list[set[str]]:
    remaining = set(ids)
    adjacency = {item: set() for item in ids}
    for edge in edges:
        adjacency[edge["source"]].add(edge["target"])
        adjacency[edge["target"]].add(edge["source"])
    components = []
    while remaining:
        start = sorted(remaining)[0]
        component = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency[node] - component)
        remaining -= component
        components.append(component)
    return components


def _social_candidates(agents: list[dict]) -> list[dict]:
    candidates: dict[tuple[str, str], dict] = {}
    for index, agent in enumerate(agents):
        for other in agents[index + 1:]:
            shared = []
            if agent.get("home") == other.get("home"):
                shared.append("home")
            if agent.get("work") == other.get("work"):
                shared.append("work")
            if shared:
                key = tuple(sorted((agent["name"], other["name"])))
                candidates[key] = {"names": list(key), "shared_context": shared}
    for left, right in zip(agents, agents[1:]):
        key = tuple(sorted((left["name"], right["name"])))
        candidates.setdefault(key, {"names": list(key), "shared_context": ["social bridge"]})
    target = max(0, min(len(candidates), max(len(agents) - 1, len(agents) * 2)))
    return [candidates[key] for key in sorted(candidates)[:target]]


def _object_jobs(kami_specs: list[dict], agent_count: int) -> list[dict]:
    total = max(len(kami_specs) * 2, agent_count * 3)
    base, extra = divmod(total, max(1, len(kami_specs)))
    jobs = []
    for index in range(0, len(kami_specs), 2):
        kami = kami_specs[index:index + 2]
        count = sum(base + (1 if index + offset < extra else 0) for offset in range(len(kami)))
        jobs.append({"kami": kami, "count": count})
    return jobs


async def _bounded_map(items: list, callback, cancel_check: CancelCheck | None) -> list:
    semaphore = asyncio.Semaphore(max(1, config.world_builder_batch_concurrency))

    async def run(item):
        async with semaphore:
            await _check_cancel(cancel_check)
            return await callback(item)

    return await asyncio.gather(*(run(item) for item in items))


def _validate_spatial(world: dict, agent_count: int) -> None:
    kami = world.get("kami_specs") or []
    if len(kami) < max(4, min(8, agent_count)):
        raise ValueError(f"spatial stage produced too few Kami: {len(kami)}")
    graph = SpatialGraph()
    for item in kami:
        graph.add_kami(item["entity_id"])
        if len(item.get("ambient_objects") or []) < 6:
            raise ValueError(f"Kami {item['name']} has fewer than 6 ambient objects")
    for edge in world["spatial_graph"]["edges"]:
        graph.add_edge(edge["source"], edge["target"], edge_type=edge["edge_type"])
    if not graph.is_connected():
        raise ValueError("spatial stage graph is not connected")


def _validate_population_progress(agents: list[dict], expected: int) -> None:
    if len(agents) != expected:
        raise ValueError(f"population expected {expected} agents, got {len(agents)}")
    names = [agent["name"] for agent in agents]
    if len(set(names)) != len(names):
        raise ValueError("population contains duplicate agent names")
    for agent in agents:
        if len(agent.get("background", "").split()) < 80:
            raise ValueError(f"agent {agent['name']} has a thin background")
        if len(agent.get("traits") or []) < 4:
            raise ValueError(f"agent {agent['name']} needs at least 4 traits")


def _validate_social_objects(world: dict, agent_count: int) -> None:
    if len(world.get("relationships") or []) < max(0, agent_count - 1):
        raise ValueError("social stage did not connect the population")
    minimum = max(len(world["kami_specs"]) * 2, agent_count * 3)
    if len(world.get("objects") or []) < minimum:
        raise ValueError(f"object stage expected at least {minimum} objects")


async def _progress(callback, stage: str, completed: int, message: str, **extra) -> None:
    await _invoke(callback, {
        "stage": stage,
        "completed_units": completed,
        "total_units": len(STAGES),
        "message": message,
        **extra,
    })


async def _check_cancel(cancel_check: CancelCheck | None) -> None:
    if cancel_check is None:
        return
    value = cancel_check()
    if inspect.isawaitable(value):
        value = await value
    if value:
        raise WorldBuildCancelled("World build cancelled")


async def _invoke(callback, payload: dict) -> None:
    if callback is None:
        return
    result = callback(payload)
    if inspect.isawaitable(result):
        await result


def _bounded_float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default
