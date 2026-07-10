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
