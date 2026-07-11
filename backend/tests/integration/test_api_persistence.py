import asyncio
import json

import httpx
import pytest

from kami_sim.api import server
from kami_sim.factstore import tools as fs
from kami_sim.llm.budget import BudgetExceededError, budget


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
        session = server.sim_state["session_factory"]()
        try:
            fs.create_entity(
                session,
                "object",
                "Visible",
                0,
                entity_id="sim_legacy-sim__visible",
                simulation_id="legacy-sim",
            )
            fs.create_entity(
                session,
                "object",
                "Hidden",
                0,
                entity_id="sim_other__hidden",
                simulation_id="other",
            )
            session.commit()
        finally:
            session.close()

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
            assert (
                await client.get("/api/entity/sim_legacy-sim__visible")
            ).status_code == 200
            assert (
                await client.get("/api/entity/sim_other__hidden")
            ).status_code == 404

            with budget.scope("legacy-sim"):
                budget.record_call(
                    "anthropic:claude-haiku-4-5-20251001",
                    "AgentWorker",
                    1000,
                    200,
                    tick=2,
                )
            budget_response = await client.get(
                "/api/simulations/legacy-sim/budget"
            )
            assert budget_response.status_code == 200
            assert budget_response.json()["summary"]["total_calls"] == 1
            assert len(budget_response.json()["calls"]) == 1

            limit_response = await client.put(
                "/api/simulations/legacy-sim/budget",
                json={"budget_limit_usd": 2.0},
            )
            assert limit_response.status_code == 200
            assert limit_response.json()["budget"]["budget_limit_usd"] == 2.0

            async def reject_world(*args, **kwargs):
                raise BudgetExceededError("new-sim", 0.01, 0.02)

            monkeypatch.setattr(server, "build_world", reject_world)
            create_response = await client.post(
                "/api/sim/create",
                json={
                    "prompt": "Світ, що перевищує бюджет",
                    "agent_count": 2,
                    "content_language": "uk",
                },
            )
            assert create_response.status_code == 202
            job_id = create_response.json()["job"]["job_id"]
            for _ in range(50):
                job_response = await client.get(f"/api/world-builds/{job_id}")
                if job_response.json()["job"]["status"] == "failed":
                    break
                await asyncio.sleep(0.01)
            assert job_response.json()["job"]["status"] == "failed"
            assert "budget exhausted" in job_response.json()["job"]["error"]
            failed_records = [
                item
                for item in server.sim_state["simulation_repository"]
                .read_registry()["simulations"]
                if item["id"] != "legacy-sim"
            ]
            assert len(failed_records) == 1
            assert failed_records[0]["status"] == "failed"
            assert failed_records[0]["content_language"] == "uk"

    # The file is import-only; runtime writes are persisted in the database.
    assert json.loads(registry_path.read_text(encoding="utf-8"))["active_id"] == "legacy-sim"
