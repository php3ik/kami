import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from kami_sim.api import server


def test_create_request_enforces_population_and_prompt_limits():
    with pytest.raises(ValidationError):
        server.CreateSimRequest(prompt="ok", agent_count=0)
    with pytest.raises(ValidationError):
        server.CreateSimRequest(prompt="valid prompt", agent_count=101)


def test_runtime_guard_rejects_overlapping_commands(monkeypatch):
    monkeypatch.setitem(server.sim_state, "running", True)

    with pytest.raises(HTTPException) as exc_info:
        server._ensure_runtime_idle("step the simulation")

    assert exc_info.value.status_code == 409


def test_openapi_exposes_bounded_tick_parameters():
    schema = server.app.openapi()
    step_parameters = schema["paths"]["/api/sim/step"]["post"]["parameters"]
    run_parameters = schema["paths"]["/api/sim/run"]["post"]["parameters"]

    assert step_parameters[0]["schema"]["minimum"] == 1
    assert step_parameters[0]["schema"]["maximum"] == 100
    assert run_parameters[0]["schema"]["minimum"] == 1
    assert run_parameters[0]["schema"]["maximum"] == 10_000


def test_default_cors_configuration_is_not_wildcard():
    assert server.config.cors_origins
    assert "*" not in server.config.cors_origins
