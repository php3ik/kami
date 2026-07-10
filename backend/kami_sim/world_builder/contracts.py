"""Typed contracts and blocking validation for generated worlds."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class MemoryContract(ContractModel):
    content: str = Field(min_length=12)
    importance: float = Field(ge=0, le=1)
    participants: list[str] = Field(default_factory=list)


class KamiContract(ContractModel):
    entity_id: str = Field(min_length=3)
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    description: str = Field(min_length=80)
    history: str = Field(min_length=40)
    sensory: dict[str, Any]
    ambient_objects: list[str] = Field(min_length=6)
    capacity: int = Field(ge=1)


class EdgeContract(ContractModel):
    source: str
    target: str
    edge_type: Literal[
        "adjacent", "contains", "path", "doorway", "trail", "transit_route"
    ] = "adjacent"
    visual_attenuation: float = Field(ge=0, le=1)
    audio_attenuation: float = Field(ge=0, le=1)


class AgentContract(ContractModel):
    entity_id: str
    name: str = Field(min_length=1)
    age: int = Field(ge=1, le=100)
    role: str = Field(min_length=2)
    home: str
    work: str
    background: str = Field(min_length=300)
    life_narrative: str = Field(min_length=200)
    private_history: list[str] = Field(min_length=3)
    memories: list[MemoryContract] = Field(min_length=10)
    traits: list[str] = Field(min_length=4)
    fears: list[str] = Field(min_length=2)
    desires: list[str] = Field(min_length=2)


class RelationshipContract(ContractModel):
    names: list[str] = Field(min_length=2, max_length=2)
    rel_type: str = Field(min_length=1)
    trust: float = Field(ge=0, le=1)
    tension: float = Field(ge=0, le=1)
    story: str = Field(min_length=20)


class ObjectContract(ContractModel):
    entity_id: str
    name: str
    kind: Literal["object", "document", "vehicle", "plant", "animal"]
    kami_id: str
    description: str = Field(min_length=10)
    state: dict[str, Any] = Field(default_factory=dict)


class SpatialGraphContract(ContractModel):
    edges: list[EdgeContract]


class WorldContract(ContractModel):
    world_seed: dict[str, Any]
    kami_specs: list[KamiContract] = Field(min_length=4)
    spatial_graph: SpatialGraphContract
    agents: list[AgentContract]
    relationships: list[RelationshipContract]
    objects: list[ObjectContract]


def validate_complete_world(world: dict, agent_count: int) -> list[str]:
    errors: list[str] = []
    try:
        WorldContract.model_validate(world)
    except ValidationError as exc:
        errors.extend(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors()
        )

    agents = world.get("agents") or []
    kami_specs = world.get("kami_specs") or []
    relationships = world.get("relationships") or []
    objects = world.get("objects") or []
    names = [str(agent.get("name") or "") for agent in agents]
    ids = [str(agent.get("entity_id") or "") for agent in agents]
    name_set = set(names)
    kami_ids = {str(kami.get("entity_id") or "") for kami in kami_specs}
    if len(agents) != agent_count:
        errors.append(f"expected exactly {agent_count} agents, got {len(agents)}")
    if len(set(names)) != len(names):
        errors.append("agent names must be unique")
    if len(set(ids)) != len(ids):
        errors.append("agent IDs must be unique")
    for agent in agents:
        if agent.get("home") not in kami_ids:
            errors.append(f"agent {agent.get('name')} has invalid home")
        if agent.get("work") not in kami_ids:
            errors.append(f"agent {agent.get('name')} has invalid work")
        for memory in agent.get("memories") or []:
            for participant in memory.get("participants") or []:
                if participant not in name_set:
                    errors.append(
                        f"agent {agent.get('name')} memory references missing participant {participant}"
                    )
    if len(relationships) < max(0, agent_count - 1):
        errors.append(
            f"expected at least {max(0, agent_count - 1)} relationships, got {len(relationships)}"
        )
    for relationship in relationships:
        pair = relationship.get("names") or []
        if len(pair) != 2 or pair[0] == pair[1] or any(name not in name_set for name in pair):
            errors.append(f"relationship has invalid participants: {pair}")
    minimum_objects = max(len(kami_specs) * 2, agent_count * 3)
    if len(objects) < minimum_objects:
        errors.append(f"expected at least {minimum_objects} objects, got {len(objects)}")
    for item in objects:
        if item.get("kami_id") not in kami_ids:
            errors.append(f"object {item.get('name')} has invalid Kami")
    edges = (world.get("spatial_graph") or {}).get("edges") or []
    adjacency = {kami_id: set() for kami_id in kami_ids}
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if source not in kami_ids or target not in kami_ids or source == target:
            errors.append(f"spatial edge has invalid endpoints: {source}, {target}")
            continue
        adjacency[source].add(target)
        adjacency[target].add(source)
    if kami_ids:
        visited = set()
        stack = [sorted(kami_ids)[0]]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            stack.extend(adjacency[node] - visited)
        if visited != kami_ids:
            errors.append("spatial graph is not connected")
    return list(dict.fromkeys(errors))
