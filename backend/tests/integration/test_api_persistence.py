import json

import httpx
import pytest

from kami_sim.api import server


@pytest.mark.asyncio
async def test_lifespan_imports_legacy_registry_into_database(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'kami.db').as_posix()}"
    registry_path = tmp_path / "simulations_registry.json"
    registry_path.write_text(
        json.dumps({
            "active_id": "legacy-sim",
            "simulations": [{
                "id": "legacy-sim",
                "name": "Legacy World",
                "prompt": "Imported world",
                "db_url": database_url,
                "db_path": str(tmp_path / "kami.db"),
                "graph_path": None,
                "graph_data": {"nodes": [], "edges": []},
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-02T00:00:00+00:00",
                "total_cost_usd": 1.5,
            }],
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(server.config, "database_url", database_url)
    monkeypatch.setattr(server, "REGISTRY_PATH", registry_path)
    for key, value in dict(server.sim_state).items():
        monkeypatch.setitem(server.sim_state, key, value)
    server.sim_state["simulation_repository"] = None
    server.sim_state["session_factory"] = None
    server.sim_state["scheduler"] = None

    async with server.lifespan(server.app):
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/api/simulations")
            assert response.status_code == 200
            payload = response.json()
            assert payload["active_id"] == "legacy-sim"
            assert payload["simulations"][0]["name"] == "Legacy World"
            assert server.sim_state["simulation_repository"].get("legacy-sim") is not None

    # The file is import-only; runtime writes are persisted in the database.
    assert json.loads(registry_path.read_text(encoding="utf-8"))["active_id"] == "legacy-sim"
