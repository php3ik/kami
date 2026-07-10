import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from kami_sim.factstore.models import init_db


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _alembic(database_path: Path, *args: str) -> subprocess.CompletedProcess:
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path.as_posix()}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_migrations_build_fresh_schema_without_drift(tmp_path):
    database_path = tmp_path / "fresh.db"

    _alembic(database_path, "upgrade", "head")
    result = _alembic(database_path, "check")

    assert "No new upgrade operations detected" in result.stdout
    with sqlite3.connect(database_path) as database:
        tables = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"entities", "events", "simulations", "alembic_version"} <= tables


def test_migrations_adopt_complete_create_all_schema(tmp_path):
    database_path = tmp_path / "legacy.db"
    engine, _ = init_db(f"sqlite:///{database_path.as_posix()}")
    engine.dispose()

    _alembic(database_path, "upgrade", "head")
    result = _alembic(database_path, "check")

    assert "No new upgrade operations detected" in result.stdout


def test_scope_migration_backfills_legacy_rows(tmp_path):
    database_path = tmp_path / "legacy_scopes.db"
    _alembic(database_path, "upgrade", "0002_simulations")
    with sqlite3.connect(database_path) as database:
        database.executemany(
            "INSERT INTO entities "
            "(entity_id, kind, canonical_name, archetype, created_at_tick) "
            "VALUES (?, ?, ?, '{}', 0)",
            [
                ("sim_alpha__kami_room", "kami", "Alpha Room"),
                ("sim_alpha__agent_ari", "agent", "Ari"),
                ("sim_beta__kami_room", "kami", "Beta Room"),
                ("sim_beta__agent_ben", "agent", "Ben"),
                ("legacy_agent", "agent", "Legacy"),
            ],
        )
        database.execute(
            "INSERT INTO locations "
            "(entity_id, kami_id, since_tick, valid_until_tick) "
            "VALUES ('sim_alpha__agent_ari', 'sim_alpha__kami_room', 0, NULL)"
        )
        database.execute(
            "INSERT INTO events "
            "(event_id, tick, kami_id, event_type, participants, payload, salience, narrative, causes) "
            "VALUES ('evt_alpha', 0, 'sim_alpha__kami_room', 'idle', '[]', '{}', 0.1, '', '[]')"
        )
        database.execute(
            "INSERT INTO events "
            "(event_id, tick, kami_id, event_type, participants, payload, salience, narrative, causes) "
            "VALUES ('evt_beta_remote', 0, NULL, 'message', '[\"sim_beta__agent_ben\"]', '{}', 0.1, '', '[]')"
        )
        database.execute(
            "INSERT INTO channels "
            "(channel_id, kind, participants, subscribers, medium_properties, created_at_tick, metadata) "
            "VALUES ('chan_beta', 'sms', '[\"sim_beta__agent_ben\"]', '[]', '{}', 0, '{}')"
        )

    _alembic(database_path, "upgrade", "head")
    with sqlite3.connect(database_path) as database:
        entity_scopes = dict(
            database.execute("SELECT entity_id, simulation_id FROM entities")
        )
        location_scope = database.execute(
            "SELECT simulation_id FROM locations"
        ).fetchone()[0]
        event_scope = database.execute(
            "SELECT simulation_id FROM events WHERE event_id = 'evt_alpha'"
        ).fetchone()[0]
        remote_event_scope = database.execute(
            "SELECT simulation_id FROM events WHERE event_id = 'evt_beta_remote'"
        ).fetchone()[0]
        channel_scope = database.execute(
            "SELECT simulation_id FROM channels WHERE channel_id = 'chan_beta'"
        ).fetchone()[0]
        simulations = {
            row[0]: row[1]
            for row in database.execute("SELECT id, status FROM simulations")
        }

    assert entity_scopes["sim_alpha__agent_ari"] == "alpha"
    assert entity_scopes["sim_beta__kami_room"] == "beta"
    assert entity_scopes["legacy_agent"] == "default"
    assert location_scope == "alpha"
    assert event_scope == "alpha"
    assert remote_event_scope == "beta"
    assert channel_scope == "beta"
    assert simulations == {
        "alpha": "migrated",
        "beta": "migrated",
        "default": "migrated",
    }
