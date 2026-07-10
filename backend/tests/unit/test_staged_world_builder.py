import pytest

from kami_sim.factstore.models import Entity, EpisodicMemoryRecord, Schedule, init_db
from kami_sim.world_builder.build_world import load_world_into_db
from kami_sim.world_builder import staged
from kami_sim.world_builder.contracts import validate_complete_world


def _background(index: int) -> str:
    return " ".join(
        [
            f"Agent {index} grew through a specific family conflict and learned practical work early."
        ]
        * 12
    )


def _spatial(count: int = 8) -> dict:
    kinds = ["residential", "institutional", "commercial", "public_indoor"]
    kami = [
        {
            "entity_id": f"kami_{index}",
            "name": f"Place {index}",
            "kind": kinds[index % len(kinds)],
            "district": "main",
            "description": "A concrete inspectable location with grounded layout, constraints, routines, traces, and visible affordances for daily life.",
            "history": "A specific local conflict happened here and still shapes how residents use this place today.",
            "sensory": {"sights": ["worn threshold"]},
            "current_situation": "Work is waiting at tick zero.",
            "ambient_objects": [f"fixture {item}" for item in range(6)],
            "capacity": 50,
        }
        for index in range(count)
    ]
    return {
        "kami_specs": kami,
        "spatial_graph": {
            "edges": [
                {
                    "source": f"kami_{index}",
                    "target": f"kami_{index + 1}",
                    "edge_type": "doorway" if index % 2 else "path",
                }
                for index in range(count - 1)
            ]
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_count", [1, 10, 100])
async def test_staged_pipeline_scales_with_bounded_batches(monkeypatch, agent_count):
    population_sizes = []
    relationship_sizes = []
    object_sizes = []
    backstory_sizes = []
    checkpoints = []

    async def fake_seed(*args, **kwargs):
        return {"name": "Scale World", "premise": "A complete bounded test world."}

    async def fake_spatial(*args, **kwargs):
        return _spatial()

    async def fake_population(prompt, name, world, batch_size, start, existing, slots):
        population_sizes.append(batch_size)
        return [
            {
                "entity_id": f"agent_{index}",
                "name": f"Agent {index}",
                "age": 20 + index % 50,
                "role": f"Role {index}",
                "home": f"kami_{index % 8}",
                "work": f"kami_{(index + 1) % 8}",
                "background": _background(index),
                "private_history": ["private one", "private two", "private three"],
                "traits": ["careful", "direct", "funny", "stubborn"],
                "fears": ["failure", "loss"],
                "desires": ["trust", "mastery"],
                "goals": {"current": "Do useful work"},
                "needs": {},
            }
            for index in range(start, start + batch_size)
        ]

    async def fake_relationships(prompt, world, pairs):
        relationship_sizes.append(len(pairs))
        return [
            {
                "names": pair["names"],
                "rel_type": "friends_with",
                "trust": 0.6,
                "tension": 0.2,
                "story": "They share concrete obligations and an unresolved disagreement from earlier work.",
            }
            for pair in pairs
        ]

    async def fake_objects(prompt, world, job):
        object_sizes.append(job["count"])
        kami = job["kami"]
        return [
            {
                "entity_id": f"obj_{kami[index % len(kami)]['entity_id']}_{index}",
                "name": f"Useful object {index}",
                "kind": "object",
                "kami_id": kami[index % len(kami)]["entity_id"],
                "description": "A concrete tool used in daily work.",
                "state": {"integrity": 0.8},
            }
            for index in range(job["count"])
        ]

    async def fake_backstories(prompt, world, agents):
        backstory_sizes.append(len(agents))
        return [
            {
                "name": agent["name"],
                "life_narrative": ("I remember concrete choices, failures, loyalties, and obligations. " * 8),
                "memories": [
                    {
                        "content": f"A specific remembered scene {index} changed how I trust people.",
                        "importance": 0.5,
                        "participants": [agent["name"]],
                    }
                    for index in range(10)
                ],
            }
            for agent in agents
        ]

    monkeypatch.setattr(staged, "_generate_seed", fake_seed)
    monkeypatch.setattr(staged, "_generate_spatial", fake_spatial)
    monkeypatch.setattr(staged, "_generate_population_batch", fake_population)
    monkeypatch.setattr(staged, "_generate_relationship_batch", fake_relationships)
    monkeypatch.setattr(staged, "_generate_object_batch", fake_objects)
    monkeypatch.setattr(staged, "_generate_backstory_batch", fake_backstories)

    async def checkpoint(value):
        checkpoints.append(value["completed_stages"].copy())

    world = await staged.build_staged_world(
        "A bounded scale test world with concrete daily life.",
        agent_count,
        checkpoint_callback=checkpoint,
    )

    assert len(world["agents"]) == agent_count
    assert len(world["relationships"]) >= max(0, agent_count - 1)
    assert len(world["objects"]) >= max(16, agent_count * 3)
    assert max(population_sizes) <= 5
    assert max(relationship_sizes, default=0) <= 15
    assert max(backstory_sizes) <= 3
    assert max(object_sizes) <= 76
    assert checkpoints[-1] == list(staged.STAGES)
    assert validate_complete_world(world, agent_count) == []
    if agent_count == 100:
        engine, factory = init_db("sqlite:///:memory:")
        try:
            with factory() as session:
                graph = load_world_into_db(session, world, simulation_id="scale")
                assert graph.is_connected()
                assert session.query(Entity).filter(Entity.kind == "agent").count() == 100
                assert session.query(Schedule).count() == 2800
                assert session.query(EpisodicMemoryRecord).count() == 1000
        finally:
            engine.dispose()


@pytest.mark.asyncio
async def test_pipeline_resumes_after_completed_checkpoint(monkeypatch):
    world = {
        "world_seed": {"name": "Resume", "premise": "A complete resume premise."},
        **_spatial(),
    }

    async def should_not_run(*args, **kwargs):
        raise AssertionError("completed stage ran again")

    monkeypatch.setattr(staged, "_generate_seed", should_not_run)
    monkeypatch.setattr(staged, "_generate_spatial", should_not_run)

    async def cancel_at_population(*args, **kwargs):
        raise staged.WorldBuildCancelled("stop")

    monkeypatch.setattr(staged, "_generate_population_batch", cancel_at_population)
    with pytest.raises(staged.WorldBuildCancelled):
        await staged.build_staged_world(
            "Resume test premise",
            2,
            checkpoint={
                "completed_stages": ["seed", "spatial"],
                "world": world,
                "slot_inventory": staged._build_slot_inventory(world["kami_specs"], 2),
            },
        )


def test_connect_graph_bridges_disconnected_components_and_preserves_types():
    spatial = _spatial(4)
    spatial["spatial_graph"]["edges"] = [
        {"source": "kami_0", "target": "kami_1", "edge_type": "doorway"},
        {"source": "kami_2", "target": "kami_3", "edge_type": "transit_route"},
    ]
    graph = staged._connect_graph(spatial["spatial_graph"], spatial["kami_specs"])

    assert {edge["edge_type"] for edge in graph["edges"]} >= {
        "doorway", "transit_route", "adjacent"
    }
    assert len(staged._components(
        [kami["entity_id"] for kami in spatial["kami_specs"]], graph["edges"]
    )) == 1
