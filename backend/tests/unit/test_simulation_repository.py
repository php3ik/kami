from kami_sim.factstore.models import init_db
from kami_sim.simulations import SimulationRepository


def _repository():
    engine, factory = init_db("sqlite:///:memory:")
    return engine, SimulationRepository(factory)


def _record(simulation_id: str, name: str, cost: float = 0.0) -> dict:
    return {
        "id": simulation_id,
        "name": name,
        "prompt": f"Prompt for {name}",
        "graph_data": {"nodes": [{"id": f"kami_{simulation_id}"}], "edges": []},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00",
        "total_cost_usd": cost,
    }


def test_imports_legacy_registry_and_preserves_active_world():
    engine, repository = _repository()
    try:
        imported = repository.import_legacy_registry({
            "active_id": "sim-b",
            "simulations": [_record("sim-a", "A"), _record("sim-b", "B")],
        })

        registry = repository.read_registry()
        assert imported == 2
        assert registry["active_id"] == "sim-b"
        assert {item["id"] for item in registry["simulations"]} == {"sim-a", "sim-b"}
    finally:
        engine.dispose()


def test_legacy_import_is_idempotent_and_does_not_overwrite_database_state():
    engine, repository = _repository()
    legacy = {"active_id": "sim-a", "simulations": [_record("sim-a", "Legacy", 1.0)]}
    try:
        assert repository.import_legacy_registry(legacy) == 1
        repository.upsert(_record("sim-a", "Updated", 7.5), active=True)

        assert repository.import_legacy_registry(legacy) == 0
        current = repository.get("sim-a")
        assert current["name"] == "Updated"
        assert current["total_cost_usd"] == 7.5
    finally:
        engine.dispose()


def test_replace_registry_switches_active_world_and_removes_deleted_records():
    engine, repository = _repository()
    try:
        repository.import_legacy_registry({
            "active_id": "sim-a",
            "simulations": [_record("sim-a", "A"), _record("sim-b", "B")],
        })
        repository.replace_registry({
            "active_id": "sim-b",
            "simulations": [_record("sim-b", "B updated")],
        })

        registry = repository.read_registry()
        assert registry["active_id"] == "sim-b"
        assert [item["id"] for item in registry["simulations"]] == ["sim-b"]
        assert registry["simulations"][0]["name"] == "B updated"
    finally:
        engine.dispose()


def test_update_runtime_persists_tick_status_and_cost_delta():
    engine, repository = _repository()
    try:
        repository.upsert(_record("sim-a", "A", 1.25), active=True)

        updated = repository.update_runtime(
            "sim-a",
            current_tick=42,
            status="running",
            cost_delta=0.75,
        )

        assert updated["current_tick"] == 42
        assert updated["status"] == "running"
        assert updated["total_cost_usd"] == 2.0
    finally:
        engine.dispose()


def test_legacy_import_enriches_migration_placeholder():
    engine, repository = _repository()
    try:
        placeholder = _record("sim-a", "sim-a")
        placeholder["status"] = "migrated"
        repository.upsert(placeholder)

        repository.import_legacy_registry({
            "active_id": "sim-a",
            "simulations": [_record("sim-a", "Real Name", 3.0)],
        })

        imported = repository.get("sim-a")
        assert imported["name"] == "Real Name"
        assert imported["status"] == "paused"
        assert repository.read_registry()["active_id"] == "sim-a"
    finally:
        engine.dispose()
