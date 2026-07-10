"""Dynamic LLM world builder.

This module intentionally contains no domain templates. It asks the LLM for a
complete world contract, validates it, asks for repairs when needed, then
normalizes IDs and graph connectivity before the world is loaded into FactStore.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..llm.client import llm_client
from ..spatial.graph import SpatialGraph

logger = logging.getLogger(__name__)

VALID_KINDS = {
    "residential",
    "commercial",
    "public_outdoor",
    "public_indoor",
    "industrial",
    "institutional",
    "transit",
    "utility",
    "workshop",
    "nature",
}

WORLD_SCHEMA_HINT = {
    "world_seed": {
        "name": "string",
        "premise": "string",
        "geography": "string",
        "history": "string",
        "daily_rhythm": "string",
        "social_tensions": ["string"],
        "tone": "string",
    },
    "kami_specs": [{
        "entity_id": "kami_snake_case_id",
        "name": "human readable name",
        "kind": "residential|commercial|public_outdoor|public_indoor|industrial|institutional|transit|utility|workshop|nature",
        "district": "string",
        "description": "4-7 grounded sentences describing layout, mood, use, constraints, and what feels lived-in",
        "history": "3-5 sentences about what happened here before tick 0 and why it matters now",
        "sensory": {
            "sights": ["specific visible detail"],
            "sounds": ["specific sound"],
            "smells": ["specific smell"],
            "textures": ["specific tactile detail"]
        },
        "current_situation": "what is true here at tick 0, including traces of recent events",
        "ambient_objects": ["8-15 concrete objects or fixtures visible here"],
        "capacity": 1,
    }],
    "spatial_graph": {
        "edges": [{
            "source": "kami_id",
            "target": "kami_id",
            "edge_type": "adjacent|contains|path|doorway|trail|transit_route",
            "visual_attenuation": 0.2,
            "audio_attenuation": 0.4,
        }]
    },
    "agents": [{
        "entity_id": "agent_snake_case_id",
        "name": "string",
        "age": 30,
        "role": "string",
        "home": "kami_id",
        "work": "kami_id",
        "appearance": "string",
        "background": "250-450 words: detailed biography with childhood/family, formative experiences, wounds, habits, contradictions, current emotional pressure, dreams, and concrete memories",
        "private_history": "5-8 specific facts that other people may not know",
        "memories": [{
            "content": "specific remembered scene, not abstract summary",
            "importance": 0.8,
            "participants": ["names"]
        }],
        "secrets": ["private worry, shame, promise, or hidden wish"],
        "traits": ["string"],
        "fears": ["string"],
        "desires": ["string"],
        "voice": "string",
        "goals": {"life": "string", "daily": "string", "current": "string"},
        "needs": {"fatigue": 0.2, "hunger": 0.2, "stress": 0.2, "social": 0.4, "task_pressure": 0.4},
    }],
    "relationships": [{
        "names": ["Agent Name A", "Agent Name B"],
        "rel_type": "string",
        "trust": 0.5,
        "tension": 0.2,
        "story": "specific relationship origin",
    }],
    "objects": [{
        "entity_id": "obj_snake_case_id",
        "name": "string",
        "kind": "object|document|vehicle|plant|animal",
        "kami_id": "kami_id",
        "description": "string",
        "condition": "specific physical condition",
        "uses": ["how agents can use or notice it"],
        "story": "why this object is here or what recent event it hints at",
        "state": {"attribute": "value"},
    }],
}

WORLD_TOOLS = [{
    "name": "submit_world",
    "description": "Submit a complete consistent simulation world as structured data.",
    "input_schema": {
        "type": "object",
        "properties": {
            "world_seed": {"type": "object"},
            "kami_specs": {"type": "array", "items": {"type": "object"}},
            "spatial_graph": {"type": "object"},
            "agents": {"type": "array", "items": {"type": "object"}},
            "relationships": {"type": "array", "items": {"type": "object"}},
            "objects": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["world_seed", "kami_specs", "spatial_graph", "agents", "relationships"],
    },
}]


async def build_dynamic_world(prompt: str, agent_count: int, name: str | None = None) -> dict:
    """Backward-compatible entry point for the canonical staged pipeline."""
    from .staged import build_staged_world

    return await build_staged_world(prompt, agent_count, name)


async def _generate_world(
    prompt: str,
    agent_count: int,
    target_kami_count: int,
    name: str | None,
) -> dict:
    seed_and_kami = await _generate_seed_and_kami(prompt, agent_count, target_kami_count, name)
    seed_and_kami = _normalize_world(
        {
            **seed_and_kami,
            "agents": [],
            "relationships": [],
            "objects": [],
        },
        prompt,
        0,
        name,
    )

    agents = await _generate_agents(prompt, agent_count, name, seed_and_kami)
    partial_world = _normalize_world(
        {
            **seed_and_kami,
            "agents": agents.get("agents") or [],
            "relationships": [],
            "objects": [],
        },
        prompt,
        agent_count,
        name,
    )

    social_and_objects = await _generate_social_and_objects(
        prompt, agent_count, target_kami_count, name, partial_world
    )
    return {
        "world_seed": partial_world.get("world_seed") or {},
        "kami_specs": partial_world.get("kami_specs") or [],
        "spatial_graph": partial_world.get("spatial_graph") or {},
        "agents": partial_world.get("agents") or [],
        "relationships": social_and_objects.get("relationships") or [],
        "objects": social_and_objects.get("objects") or [],
    }


async def _generate_seed_and_kami(
    prompt: str,
    agent_count: int,
    target_kami_count: int,
    name: str | None,
) -> dict:
    target_kami_count = max(6, min(target_kami_count, 14))
    response = await llm_client.call(
        messages=[{
            "role": "user",
            "content": f"""Return valid JSON only. No markdown.

Create the setting bible and spatial kami graph for a new simulation world.

World name: {name or 'derive a fitting name from the description'}
World description:
{prompt}

Population count later: exactly {agent_count} agents.
Kami/location target now: about {target_kami_count} concrete places.

Do not use a canned genre template. Infer the world from the exact description, including named groups, places, culture, tools, conflicts, daily routines, and implied objects.

Requirements:
- The world must be immediately playable at tick 0.
- Kami must be concrete spatial places, not abstract categories.
- Every Kami must feel inspectable: layout, sensory details, local history, current traces, and 6-10 ambient objects/fixtures.
- The graph must be connected.
- Return only keys: world_seed, kami_specs, spatial_graph.
- Use exact stable snake_case kami IDs.
- Keep each kami description to 2-4 dense sentences and each history to 1-3 dense sentences. Do not write essays in this stage.
- Use this JSON shape as the contract:
{json.dumps({k: WORLD_SCHEMA_HINT[k] for k in ("world_seed", "kami_specs", "spatial_graph")}, ensure_ascii=False, indent=2)}
""",
        }],
        system=(
            "You are a simulation world architect. You produce grounded, internally "
            "consistent worlds from arbitrary text descriptions. Never fall back to "
            "prewritten worlds; every name, location, object, and social tie must be "
            "derived from the user's description and plausible implications."
        ),
        tier="strong",
        component="WorldBuilder",
        tools=None,
        response_format={"type": "json_object"},
        max_tokens=20000,
        temperature=0.45,
    )
    try:
        return _world_from_response(response, label="seed_and_kami")
    except ValueError:
        logger.warning("Retrying seed_and_kami with compact contract after empty/invalid response.")

    compact_target = max(6, min(target_kami_count, 10))
    response = await llm_client.call(
        messages=[{
            "role": "user",
            "content": f"""Return one compact valid JSON object only. No markdown.

Create a playable simulation setting from this prompt:
{prompt}

World name: {name or 'derive from prompt'}
Create {compact_target} concrete kami locations for exactly {agent_count} future agents.

JSON keys exactly:
- world_seed: name, premise, geography, history, daily_rhythm, social_tensions, tone
- kami_specs: array of locations with entity_id, name, kind, district, description, history, sensory, current_situation, ambient_objects, capacity
- spatial_graph: edges between exact kami IDs

Keep descriptions rich but concise. The graph must be connected. Use no templates.
""",
        }],
        system=(
            "Return compact JSON for a dynamic simulation world. Spend no time "
            "explaining. Prioritize valid parseable JSON."
        ),
        tier="strong",
        component="WorldBuilder",
        tools=None,
        response_format={"type": "json_object"},
        max_tokens=20000,
        temperature=0.35,
    )
    return _world_from_response(response, label="seed_and_kami_retry")


async def _generate_agents(
    prompt: str,
    agent_count: int,
    name: str | None,
    world: dict,
) -> dict:
    kami_brief = [
        {
            "entity_id": kami["entity_id"],
            "name": kami["name"],
            "kind": kami["kind"],
            "district": kami.get("district"),
            "description": kami.get("description"),
            "current_situation": kami.get("current_situation"),
        }
        for kami in world.get("kami_specs", [])
    ]

    response = await llm_client.call(
        messages=[{
            "role": "user",
            "content": f"""Return valid JSON only. No markdown.

Create exactly {agent_count} distinct agents for this simulation world.

World name: {name or world.get('world_seed', {}).get('name') or 'generated world'}
World description:
{prompt}

Existing kami. Use these exact kami IDs for home/work:
{json.dumps(kami_brief, ensure_ascii=False, indent=2)}

Each agent must have a biography, not a dry role card:
- background: 180-320 words with childhood/family, formative experiences, wounds, habits, contradictions, current emotional pressure, dreams, and concrete memories.
- private_history: 5-8 specific private facts.
- memories: at least 4 remembered scenes with importance and participants.
- secrets, traits, fears, desires, voice, goals, needs.
- Make people socially and emotionally different from each other.
- If the prompt names nationalities, professions, conflicts, or strange beliefs, assign them deliberately and plausibly.

Return only:
{{"agents": {json.dumps(WORLD_SCHEMA_HINT["agents"], ensure_ascii=False, indent=2)}}}
""",
        }],
        system=(
            "You are a character architect for a live simulation. Generate people "
            "with inner lives, contradictions, local context, and usable motivations. "
            "Return JSON only."
        ),
        tier="strong",
        component="WorldBuilder",
        tools=None,
        response_format={"type": "json_object"},
        max_tokens=10000,
        temperature=0.7,
    )
    try:
        return _world_from_response(response, label="agents")
    except ValueError:
        logger.warning("Retrying agents with compact contract after empty/invalid response.")

    response = await llm_client.call(
        messages=[{
            "role": "user",
            "content": f"""Return compact valid JSON only.

Create exactly {agent_count} agents for:
{prompt}

Use only these kami IDs for home/work:
{json.dumps([kami["entity_id"] for kami in world.get("kami_specs", [])], ensure_ascii=False)}

Each agent needs entity_id, name, age, role, home, work, appearance, background, private_history, memories, secrets, traits, fears, desires, voice, goals, needs.
Backgrounds may be 120-220 words but must contain concrete biography, pressure, dreams, and contradictions.
Return {{"agents": [...]}}.
""",
        }],
        system="Return compact JSON only for simulation characters.",
        tier="strong",
        component="WorldBuilder",
        tools=None,
        response_format={"type": "json_object"},
        max_tokens=20000,
        temperature=0.45,
    )
    return _world_from_response(response, label="agents_retry")


async def _generate_social_and_objects(
    prompt: str,
    agent_count: int,
    target_kami_count: int,
    name: str | None,
    world: dict,
) -> dict:
    kami_brief = [
        {
            "entity_id": kami["entity_id"],
            "name": kami["name"],
            "kind": kami["kind"],
            "district": kami.get("district"),
            "ambient_objects": kami.get("ambient_objects", [])[:12],
        }
        for kami in world.get("kami_specs", [])
    ]
    agent_brief = [
        {
            "name": agent["name"],
            "role": agent.get("role"),
            "home": agent.get("home"),
            "work": agent.get("work"),
            "desires": agent.get("desires", []),
            "fears": agent.get("fears", []),
        }
        for agent in world.get("agents", [])
    ]
    minimum_objects = max(target_kami_count * 2, agent_count * 3)

    response = await llm_client.call(
        messages=[{
            "role": "user",
            "content": f"""Return valid JSON only. No markdown.

Create the relationship web and interactive object layer for this simulation.

World name: {name or world.get('world_seed', {}).get('name') or 'generated world'}
World description:
{prompt}

Kami:
{json.dumps(kami_brief, ensure_ascii=False, indent=2)}

Agents:
{json.dumps(agent_brief, ensure_ascii=False, indent=2)}

Requirements:
- Relationships must connect most agents with concrete origins, trust, tension, obligations, secrets, or friction.
- Objects must be placed with exact kami_id values.
- Create at least {minimum_objects} object entities.
- Objects are for simulation, not decoration: tools, documents, supplies, vehicles, instruments, personal items, locked things, broken things, evidence, ritual items, maintenance items, communication devices, containers, logs, etc.
- Each object needs description, condition, uses, story, and state.

Return only:
{{
  "relationships": {json.dumps(WORLD_SCHEMA_HINT["relationships"], ensure_ascii=False, indent=2)},
  "objects": {json.dumps(WORLD_SCHEMA_HINT["objects"], ensure_ascii=False, indent=2)}
}}
""",
        }],
        system=(
            "You design social tension and inspectable physical affordances for "
            "a multi-agent simulation. Return JSON only and use exact IDs."
        ),
        tier="strong",
        component="WorldBuilder",
        tools=None,
        response_format={"type": "json_object"},
        max_tokens=9000,
        temperature=0.55,
    )
    try:
        return _world_from_response(response, label="social_and_objects")
    except ValueError:
        logger.warning("Retrying social_and_objects with compact contract after empty/invalid response.")

    response = await llm_client.call(
        messages=[{
            "role": "user",
            "content": f"""Return compact valid JSON only.

Create relationships and at least {minimum_objects} objects for this world:
{prompt}

Kami IDs:
{json.dumps([kami["entity_id"] for kami in world.get("kami_specs", [])], ensure_ascii=False)}

Agent names:
{json.dumps([agent["name"] for agent in world.get("agents", [])], ensure_ascii=False)}

Return {{"relationships": [...], "objects": [...]}}. Use exact agent names and kami IDs.
""",
        }],
        system="Return compact JSON only for relationship and object layers.",
        tier="strong",
        component="WorldBuilder",
        tools=None,
        response_format={"type": "json_object"},
        max_tokens=20000,
        temperature=0.35,
    )
    return _world_from_response(response, label="social_and_objects_retry")


async def _repair_world(
    prompt: str,
    agent_count: int,
    target_kami_count: int,
    name: str | None,
    world: dict,
    errors: list[str],
) -> dict:
    response = await llm_client.call(
        messages=[{
            "role": "user",
            "content": f"""Repair this generated world so it satisfies all validation errors.

Original name: {name or ''}
Original description:
{prompt}

Validation errors:
{json.dumps(errors, ensure_ascii=False, indent=2)}

World to repair:
{json.dumps(world, ensure_ascii=False, indent=2)[:18000]}

Return the full corrected world by calling submit_world. Keep what is good, but fix invalid IDs, missing agents, disconnected graph, weak relationships, thin biographies, dry kami descriptions, and missing objects. Exactly {agent_count} agents; about {target_kami_count} kami.
""",
        }],
        system="You repair structured simulation worlds. Return only a valid submit_world tool call.",
        tier="strong",
        component="WorldBuilder",
        tools=WORLD_TOOLS,
        max_tokens=14000,
        temperature=0.35,
    )
    try:
        return _world_from_response(response)
    except ValueError:
        response = await llm_client.call(
            messages=[{
                "role": "user",
                "content": f"""Return valid JSON only. Repair this world.

Original description:
{prompt}

Validation errors:
{json.dumps(errors, ensure_ascii=False, indent=2)}

World to repair:
{json.dumps(world, ensure_ascii=False, indent=2)[:18000]}
""",
            }],
            system="You repair simulation world JSON. No markdown, no prose outside JSON.",
            tier="strong",
            component="WorldBuilder",
            tools=None,
            response_format={"type": "json_object"},
            max_tokens=14000,
            temperature=0.25,
        )
        return _world_from_response(response)


def _world_from_response(response: dict, label: str = "world") -> dict:
    for tool_call in response.get("tool_calls", []):
        if tool_call.get("name") == "submit_world":
            return tool_call.get("input") or {}
    content = response.get("content", "")
    parsed = _extract_json(content)
    if parsed is not None:
        return parsed
    logger.warning(
        "WorldBuilder %s returned no parseable JSON: stop=%s content_len=%s tool_calls=%s",
        label,
        response.get("stop_reason"),
        len(content or ""),
        [call.get("name") for call in response.get("tool_calls", [])],
    )
    raise ValueError(f"WorldBuilder {label} did not return structured world data")


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _normalize_world(
    world: dict,
    prompt: str,
    agent_count: int,
    name: str | None,
) -> dict:
    world = dict(world) if isinstance(world, dict) else {}
    raw_seed = world.get("world_seed") or {}
    seed = dict(raw_seed) if isinstance(raw_seed, dict) else {"premise": str(raw_seed)}
    seed.setdefault("name", name or seed.get("town_name") or _title_from_prompt(prompt))
    seed.setdefault("town_name", seed["name"])
    seed.setdefault("premise", prompt)
    world["world_seed"] = seed

    kami_specs = _normalize_kami_specs(world.get("kami_specs") or [])
    world["kami_specs"] = kami_specs
    kami_ids = {kami["entity_id"] for kami in kami_specs}

    world["spatial_graph"] = _normalize_graph(world.get("spatial_graph") or {}, kami_specs)
    world["agents"] = _normalize_agents(world.get("agents") or [], kami_specs, agent_count)
    world["relationships"] = _normalize_relationships(
        world.get("relationships") or [], world["agents"]
    )
    world["objects"] = _normalize_objects(world.get("objects") or [], kami_ids)
    world["backstories"] = {
        agent["name"]: {
            "life_narrative": agent.get("life_narrative") or agent.get("background", ""),
            "memories": agent.get("memories", []),
            "private_history": agent.get("private_history", []),
            "secrets": agent.get("secrets", []),
        }
        for agent in world["agents"]
    }
    world["budget"] = {"dynamic_world_builder": True}
    return world


def _normalize_kami_specs(raw_specs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    specs: list[dict] = []
    for index, raw in enumerate(raw_specs):
        if not isinstance(raw, dict):
            raw = {"name": str(raw), "description": str(raw)}
        raw = dict(raw or {})
        name = str(raw.get("name") or raw.get("NAME") or f"Location {index + 1}").strip()
        entity_id = _unique_id(
            _snake_id(str(raw.get("entity_id") or raw.get("id") or name), "kami"),
            seen,
        )
        kind = str(raw.get("kind") or "public_indoor").strip().lower()
        if kind not in VALID_KINDS:
            kind = "public_indoor"
        spec = {
            "entity_id": entity_id,
            "name": name,
            "kind": kind,
            "district": str(raw.get("district") or "main").strip(),
            "kami_kind": kind,
            "description": str(raw.get("description") or raw.get("desc") or name).strip(),
            "history": str(raw.get("history") or raw.get("past_events") or "").strip(),
            "sensory": raw.get("sensory") if isinstance(raw.get("sensory"), dict) else {},
            "current_situation": str(raw.get("current_situation") or raw.get("current_state") or "").strip(),
            "capacity": _safe_int(raw.get("capacity"), 8, 1, 100),
            "ambient_objects": _string_list(raw.get("ambient_objects"), min_items=0),
        }
        specs.append(spec)
    return specs


def _normalize_graph(graph_data: dict, kami_specs: list[dict]) -> dict:
    if isinstance(graph_data, list):
        graph_data = {"edges": graph_data}
    elif not isinstance(graph_data, dict):
        graph_data = {}
    kami_ids = {kami["entity_id"] for kami in kami_specs}
    edges = []
    for raw in graph_data.get("edges", []):
        if isinstance(raw, dict):
            source = raw.get("source")
            target = raw.get("target")
            edge_type = raw.get("edge_type") or raw.get("type") or "adjacent"
            visual_attenuation = raw.get("visual_attenuation")
            audio_attenuation = raw.get("audio_attenuation")
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            source = raw[0]
            target = raw[1]
            edge_type = raw[2] if len(raw) >= 3 else "adjacent"
            visual_attenuation = None
            audio_attenuation = None
        else:
            continue
        if source in kami_ids and target in kami_ids and source != target:
            edges.append({
                "source": source,
                "target": target,
                "edge_type": edge_type,
                "visual_attenuation": _safe_float(visual_attenuation, 0.25, 0.0, 1.0),
                "audio_attenuation": _safe_float(audio_attenuation, 0.35, 0.0, 1.0),
            })

    connected = set()
    if kami_specs:
        connected.add(kami_specs[0]["entity_id"])
    for edge in edges:
        connected.add(edge["source"])
        connected.add(edge["target"])
    for left, right in zip(kami_specs, kami_specs[1:]):
        if right["entity_id"] not in connected:
            edges.append({
                "source": left["entity_id"],
                "target": right["entity_id"],
                "edge_type": "adjacent",
                "visual_attenuation": 0.25,
                "audio_attenuation": 0.35,
            })
            connected.add(right["entity_id"])
    return {"nodes": [{"id": kami["entity_id"], "name": kami["name"], "kind": kami["kind"]} for kami in kami_specs], "edges": edges}


def _normalize_agents(raw_agents: list[dict], kami_specs: list[dict], agent_count: int) -> list[dict]:
    kami_ids = {kami["entity_id"] for kami in kami_specs}
    residential = [kami["entity_id"] for kami in kami_specs if kami["kind"] == "residential"] or list(kami_ids)
    worklike = [
        kami["entity_id"]
        for kami in kami_specs
        if kami["kind"] in {"commercial", "industrial", "institutional", "utility", "workshop", "public_indoor", "public_outdoor"}
    ] or list(kami_ids)
    seen: set[str] = set()
    agents = []
    for index, raw in enumerate(raw_agents[:agent_count]):
        if not isinstance(raw, dict):
            raw = {"name": str(raw)}
        raw = dict(raw or {})
        name = str(raw.get("name") or f"Agent {index + 1}").strip()
        entity_id = _unique_id(_snake_id(str(raw.get("entity_id") or name), "agent"), seen)
        home = raw.get("home") if raw.get("home") in kami_ids else residential[index % len(residential)]
        work = raw.get("work") if raw.get("work") in kami_ids else worklike[index % len(worklike)]
        goals = raw.get("goals") or {}
        if not isinstance(goals, dict):
            goals = {"current": str(goals)}
        needs = raw.get("needs") if isinstance(raw.get("needs"), dict) else {}
        memories = raw.get("memories") if isinstance(raw.get("memories"), list) else []
        agents.append({
            "entity_id": entity_id,
            "name": name,
            "age": _safe_int(raw.get("age"), 30, 1, 100),
            "role": str(raw.get("role") or "person in this world").strip(),
            "home": home,
            "work": work,
            "appearance": str(raw.get("appearance") or "Grounded, specific appearance tied to this world.").strip(),
            "background": str(raw.get("background") or "").strip(),
            "life_narrative": str(raw.get("life_narrative") or "").strip(),
            "private_history": _string_list(raw.get("private_history"), min_items=0),
            "memories": _normalize_memories(memories),
            "secrets": _string_list(raw.get("secrets"), min_items=0),
            "traits": _string_list(raw.get("traits"), min_items=4),
            "fears": _string_list(raw.get("fears"), min_items=2),
            "desires": _string_list(raw.get("desires"), min_items=2),
            "voice": str(raw.get("voice") or "Speaks in a grounded voice shaped by their role and context.").strip(),
            "goals": {
                "life": str(goals.get("life") or "Build a meaningful life in this world."),
                "daily": str(goals.get("daily") or "Handle today's concrete obligations."),
                "current": str(goals.get("current") or "Find the next useful action."),
            },
            "needs": {
                "fatigue": _safe_float(needs.get("fatigue"), 0.2, 0.0, 1.0),
                "hunger": _safe_float(needs.get("hunger"), 0.25, 0.0, 1.0),
                "stress": _safe_float(needs.get("stress"), 0.25, 0.0, 1.0),
                "social": _safe_float(needs.get("social"), 0.4, 0.0, 1.0),
                "task_pressure": _safe_float(needs.get("task_pressure"), 0.45, 0.0, 1.0),
            },
        })
    return agents


def _normalize_relationships(raw_rels: list[dict], agents: list[dict]) -> list[dict]:
    names = {agent["name"] for agent in agents}
    rels = []
    for raw in raw_rels:
        if not isinstance(raw, dict):
            continue
        raw_names = raw.get("names") or raw.get("agents") or raw.get("participants") or []
        if not raw_names:
            raw_names = [raw.get("agent_a") or raw.get("source"), raw.get("agent_b") or raw.get("target")]
        pair = [name for name in raw_names if name in names]
        if len(pair) < 2:
            continue
        rels.append({
            "names": pair[:2],
            "rel_type": str(raw.get("rel_type") or raw.get("type") or "knows"),
            "trust": _safe_float(raw.get("trust", raw.get("strength")), 0.5, 0.0, 1.0),
            "tension": _safe_float(raw.get("tension"), 0.2, 0.0, 1.0),
            "story": str(raw.get("story") or raw.get("origin") or "They have a concrete reason to know each other."),
        })
    return rels


def _normalize_memories(memories: list[Any]) -> list[dict]:
    normalized = []
    for memory in memories:
        if isinstance(memory, dict):
            content = str(memory.get("content") or memory.get("memory") or "").strip()
            if not content:
                continue
            normalized.append({
                "content": content,
                "importance": _safe_float(memory.get("importance"), 0.5, 0.0, 1.0),
                "participants": _string_list(memory.get("participants"), min_items=0),
            })
        elif str(memory).strip():
            normalized.append({
                "content": str(memory).strip(),
                "importance": 0.5,
                "participants": [],
            })
    return normalized


def _normalize_objects(raw_objects: list[dict], kami_ids: set[str]) -> list[dict]:
    seen: set[str] = set()
    objects = []
    kami_pool = sorted(kami_ids)
    for index, raw in enumerate(raw_objects):
        if not isinstance(raw, dict):
            continue
        raw = dict(raw or {})
        kami_id = (
            raw.get("kami_id")
            or raw.get("location_id")
            or raw.get("location")
            or raw.get("place_id")
            or raw.get("place")
            or raw.get("kami")
        )
        if kami_id not in kami_ids:
            normalized = _snake_id(str(kami_id or ""), "kami")
            if normalized in kami_ids:
                kami_id = normalized
        if kami_id not in kami_ids:
            if not kami_pool:
                continue
            kami_id = kami_pool[index % len(kami_pool)]
        name = str(raw.get("name") or f"Object {index + 1}")
        kind = str(raw.get("kind") or "object")
        if kind not in {"object", "document", "vehicle", "plant", "animal"}:
            kind = "object"
        objects.append({
            "entity_id": _unique_id(_snake_id(str(raw.get("entity_id") or name), "obj"), seen),
            "name": name,
            "kind": kind,
            "kami_id": kami_id,
            "description": str(raw.get("description") or name),
            "condition": str(raw.get("condition") or ""),
            "uses": _string_list(raw.get("uses"), min_items=0),
            "story": str(raw.get("story") or ""),
            "state": raw.get("state") if isinstance(raw.get("state"), dict) else {},
        })
    return objects


def _validate_world(world: dict, agent_count: int) -> list[str]:
    errors = []
    kami_specs = world.get("kami_specs") or []
    agents = world.get("agents") or []
    kami_ids = {kami.get("entity_id") for kami in kami_specs}
    agent_names = {agent.get("name") for agent in agents}
    if len(kami_specs) < max(4, min(8, agent_count)):
        errors.append(f"too few kami: {len(kami_specs)}")
    for kami in kami_specs:
        if len(kami.get("description", "")) < 220:
            errors.append(f"kami {kami.get('name')} needs richer description")
        if len(kami.get("history", "")) < 100:
            errors.append(f"kami {kami.get('name')} needs local history")
        if len(kami.get("ambient_objects") or []) < 6:
            errors.append(f"kami {kami.get('name')} needs more ambient objects")
        sensory = kami.get("sensory") or {}
        if not isinstance(sensory, dict) or not any(sensory.values()):
            errors.append(f"kami {kami.get('name')} needs sensory details")
    if len(agents) != agent_count:
        errors.append(f"expected exactly {agent_count} agents, got {len(agents)}")
    for agent in agents:
        if agent.get("home") not in kami_ids:
            errors.append(f"agent {agent.get('name')} has invalid home {agent.get('home')}")
        if agent.get("work") and agent.get("work") not in kami_ids:
            errors.append(f"agent {agent.get('name')} has invalid work {agent.get('work')}")
        background = agent.get("background", "")
        if not background or len(background) < 500 or len(background.split()) < 80:
            errors.append(f"agent {agent.get('name')} needs a more specific background")
        if len(agent.get("memories") or []) < 3:
            errors.append(f"agent {agent.get('name')} needs at least 3 concrete memories")
        if len(agent.get("private_history") or []) < 3:
            errors.append(f"agent {agent.get('name')} needs private history facts")
    if len(world.get("relationships") or []) < max(1, agent_count - 2):
        errors.append("too few relationships")
    for rel in world.get("relationships") or []:
        for name in rel.get("names") or []:
            if name not in agent_names:
                errors.append(f"relationship references missing agent {name}")
    graph = SpatialGraph()
    for kami in kami_specs:
        graph.add_kami(kami["entity_id"])
    for edge in (world.get("spatial_graph") or {}).get("edges", []):
        graph.add_edge(edge["source"], edge["target"])
    if kami_specs and not graph.is_connected():
        errors.append("spatial graph is not connected")
    object_count = len(world.get("objects") or [])
    minimum_objects = max(len(kami_specs) * 2, agent_count * 3)
    if object_count < minimum_objects:
        errors.append(f"too few objects: expected at least {minimum_objects}, got {object_count}")
    return errors


def _split_validation_errors(errors: list[str]) -> tuple[list[str], list[str]]:
    critical_prefixes = (
        "too few kami",
        "expected exactly",
        "relationship references",
        "spatial graph is not connected",
    )
    critical_markers = (" has invalid home ", " has invalid work ")
    critical = []
    quality = []
    for error in errors:
        if error.startswith(critical_prefixes) or any(marker in error for marker in critical_markers):
            critical.append(error)
        else:
            quality.append(error)
    return critical, quality


def _snake_id(value: str, prefix: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9а-яіїєґ]+", "_", text, flags=re.IGNORECASE)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = "item"
    if not text.startswith(prefix + "_"):
        text = f"{prefix}_{text}"
    return text


def _unique_id(base: str, seen: set[str]) -> str:
    candidate = base
    index = 2
    while candidate in seen:
        candidate = f"{base}_{index}"
        index += 1
    seen.add(candidate)
    return candidate


def _string_list(value: Any, min_items: int) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    return items


def _safe_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _safe_float(value: Any, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _title_from_prompt(prompt: str) -> str:
    words = re.findall(r"[\wА-Яа-яІіЇїЄєҐґ']+", prompt)
    return " ".join(words[:4]).title() or "Generated World"
