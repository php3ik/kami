import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from kami_sim.api import server
from kami_sim.factstore import tools as fs
from kami_sim.factstore.models import (
    AgentMemoryProfile,
    EpisodicMemoryRecord,
    Event,
    KamiImprint,
    KamiMemoryProfile,
    KamiMemorySummary,
    MemorySummary,
    SemanticInsight,
    SimulationTick,
    init_db,
)
from kami_sim.comms.channels import create_channel, send_message
from kami_sim.simulations import RunConflictError


def test_create_request_enforces_population_and_prompt_limits():
    with pytest.raises(ValidationError):
        server.CreateSimRequest(prompt="ok", agent_count=0)
    with pytest.raises(ValidationError):
        server.CreateSimRequest(prompt="valid prompt", agent_count=101)


def test_runtime_guard_rejects_overlapping_commands(monkeypatch):
    class BusyManager:
        def ensure_idle(self, action):
            raise RunConflictError(f"Cannot {action} while run simulation is running")

    monkeypatch.setattr(server, "run_manager", BusyManager())

    with pytest.raises(HTTPException) as exc_info:
        server._ensure_runtime_idle("step the simulation")

    assert exc_info.value.status_code == 409


def test_legacy_migration_ignores_record_without_database_path():
    engine, factory = init_db("sqlite:///:memory:")
    record = {
        "id": "incomplete",
        "name": "Incomplete import",
        "db_url": "sqlite:///missing.db",
        "db_path": None,
        "graph_data": {},
    }
    try:
        with factory() as session:
            assert server._migrate_legacy_file_record(record, session) == record
    finally:
        engine.dispose()


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


def test_next_tick_prefers_committed_tick_ledger_over_legacy_events():
    engine, factory = init_db("sqlite:///:memory:")
    try:
        with factory() as session:
            session.add(
                Event(
                    event_id="evt_old",
                    simulation_id="sim-a",
                    tick=2,
                    event_type="idle",
                    participants=[],
                    payload={},
                    salience=0.1,
                    narrative="",
                    causes=[],
                )
            )
            session.add(
                SimulationTick(
                    simulation_id="sim-a",
                    tick=5,
                    status="committed",
                    result={"tick": 5},
                )
            )
            session.commit()

            assert server._next_tick_from_db(session, "sim-a") == 6
    finally:
        engine.dispose()


def test_memory_api_payloads_are_scoped_and_structured():
    engine, factory = init_db("sqlite:///:memory:")
    try:
        with factory() as session:
            kami = fs.create_entity(
                session,
                "kami",
                "Workshop",
                0,
                entity_id="sim_a__kami_workshop",
                simulation_id="a",
            )
            agent = fs.create_entity(
                session,
                "agent",
                "Ari",
                0,
                entity_id="sim_a__agent_ari",
                simulation_id="a",
            )
            session.add_all(
                [
                    EpisodicMemoryRecord(
                        memory_id="mem_a",
                        simulation_id="a",
                        agent_id=agent.entity_id,
                        tick=2,
                        content="Ari repaired the radio.",
                        importance=0.8,
                        participants=[],
                        location=kami.entity_id,
                        event_type="repair",
                    ),
                    MemorySummary(
                        summary_id="sum_a",
                        simulation_id="a",
                        agent_id=agent.entity_id,
                        tick=3,
                        summary="Ari completed a careful repair.",
                        candidates=[],
                    ),
                    SemanticInsight(
                        insight_id="ins_a",
                        simulation_id="a",
                        agent_id=agent.entity_id,
                        content="Careful testing prevents failures.",
                        strength=1.2,
                        created_tick=3,
                        last_reinforced_tick=3,
                        category="self",
                        status="active",
                        provenance=[],
                    ),
                    AgentMemoryProfile(
                        agent_id=agent.entity_id,
                        simulation_id="a",
                        life_narrative="I restore damaged equipment.",
                        last_consolidation_tick=3,
                    ),
                    KamiMemorySummary(
                        summary_id="ksum_a",
                        simulation_id="a",
                        kami_id=kami.entity_id,
                        tick=3,
                        summary="The workshop saw a successful radio repair.",
                        event_count=1,
                        peak_salience=0.8,
                    ),
                    KamiMemoryProfile(
                        kami_id=kami.entity_id,
                        simulation_id="a",
                        long_term_memory="The repaired radio now works clearly.",
                        last_consolidation_tick=3,
                    ),
                    KamiImprint(
                        imprint_id="kimp_a",
                        simulation_id="a",
                        kami_id=kami.entity_id,
                        tick=2,
                        fact="The north workbench is scorched.",
                        importance=0.95,
                        category="accident",
                    ),
                ]
            )
            session.commit()

            agent_payload = server._agent_memory_payload(session, agent)
            kami_payload = server._kami_memory_payload(session, kami)

        assert agent_payload["episodic"][0]["memory_id"] == "mem_a"
        assert agent_payload["insights"][0]["status"] == "active"
        assert agent_payload["life_narrative"].startswith("I restore")
        assert kami_payload["summaries"][0]["summary_id"] == "ksum_a"
        assert kami_payload["imprints"][0]["imprint_id"] == "kimp_a"
        assert "repaired radio" in kami_payload["long_term_memory"]
    finally:
        engine.dispose()


def test_communication_api_payload_is_scoped_and_time_bounded():
    engine, factory = init_db("sqlite:///:memory:")
    try:
        with factory() as session:
            ari = fs.create_entity(
                session, "agent", "Ari", 0, entity_id="agent_ari"
            )
            ben = fs.create_entity(
                session, "agent", "Ben", 0, entity_id="agent_ben"
            )
            channel = create_channel(
                session, "sms", [ari.entity_id, ben.entity_id], 0
            )
            send_message(
                session, channel.channel_id, ari.entity_id, "First", 1
            )
            send_message(
                session, channel.channel_id, ari.entity_id, "Future", 5
            )
            session.commit()

            payload = server._communication_payload(session, ben, until_tick=2)

        assert payload["unread_count"] == 1
        assert [message["content"] for message in payload["channels"][0]["messages"]] == ["First"]
        assert payload["channels"][0]["messages"][0]["available_at_tick"] == 2
    finally:
        engine.dispose()
