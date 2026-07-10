"""add simulation scope to factstore

Revision ID: 0003_scope
Revises: 0002_simulations
"""

import json
from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_scope"
down_revision: Union[str, Sequence[str], None] = "0002_simulations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCOPED_TABLES = {
    "entities": ("entity_id", "entity_id"),
    "agent_beliefs": ("belief_id", "agent_id"),
    "agent_intents": ("intent_id", "agent_id"),
    "agent_needs": ("id", "agent_id"),
    "channels": ("channel_id", "channel_id"),
    "conversation_threads": ("thread_id", "kami_id"),
    "events": ("event_id", "kami_id"),
    "locations": ("id", "entity_id"),
    "messages": ("message_id", "sender_id"),
    "ownership": ("id", "entity_id"),
    "physical_state": ("id", "entity_id"),
    "read_receipts": ("id", "agent_id"),
    "relations": ("id", "from_entity"),
    "schedules": ("schedule_id", "kami_id"),
}

SCOPED_INDEXES = {
    "agent_beliefs": [
        ("ix_beliefs_sim_agent", ["simulation_id", "agent_id"]),
    ],
    "agent_intents": [
        (
            "ix_agent_intents_sim_agent_tick",
            ["simulation_id", "agent_id", "tick"],
        ),
        (
            "ix_agent_intents_sim_kami_tick",
            ["simulation_id", "kami_id", "tick"],
        ),
    ],
    "agent_needs": [
        (
            "ix_agent_needs_sim_current",
            ["simulation_id", "agent_id", "need", "valid_until_tick"],
        ),
    ],
    "channels": [
        ("ix_channels_simulation", ["simulation_id"]),
    ],
    "conversation_threads": [
        (
            "ix_threads_sim_kami_status",
            ["simulation_id", "kami_id", "status"],
        ),
    ],
    "entities": [
        ("ix_entities_sim_kind", ["simulation_id", "kind"]),
    ],
    "events": [
        ("ix_events_sim_kami_tick", ["simulation_id", "kami_id", "tick"]),
        ("ix_events_sim_tick", ["simulation_id", "tick"]),
    ],
    "locations": [
        (
            "ix_locations_sim_entity_current",
            ["simulation_id", "entity_id", "valid_until_tick"],
        ),
        (
            "ix_locations_sim_kami_current",
            ["simulation_id", "kami_id", "valid_until_tick"],
        ),
    ],
    "messages": [
        (
            "ix_messages_sim_channel_tick",
            ["simulation_id", "channel_id", "sent_at_tick"],
        ),
    ],
    "ownership": [
        (
            "ix_ownership_sim_entity_current",
            ["simulation_id", "entity_id", "valid_until_tick"],
        ),
    ],
    "physical_state": [
        (
            "ix_physstate_sim_entity_attr_current",
            ["simulation_id", "entity_id", "attribute", "valid_until_tick"],
        ),
    ],
    "read_receipts": [
        ("ix_receipts_sim_agent", ["simulation_id", "agent_id"]),
    ],
    "relations": [
        (
            "ix_relations_sim_from_type_current",
            ["simulation_id", "from_entity", "rel_type", "valid_until_tick"],
        ),
    ],
    "schedules": [
        ("ix_schedules_sim_tick", ["simulation_id", "fires_at_tick"]),
    ],
}


def _scope_from_id(value: str | None) -> str:
    if value and value.startswith("sim_") and "__" in value:
        return value[4:].split("__", 1)[0] or "default"
    return "default"


def _scope_from_participants(
    value,
    entity_scopes: dict[str, str],
) -> str | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, list):
        return None
    scopes = {
        entity_scopes.get(item, _scope_from_id(item))
        for item in value
        if isinstance(item, str)
    }
    scopes.discard("default")
    if len(scopes) > 1:
        raise RuntimeError(
            f"Legacy participant list crosses simulation scopes: {sorted(scopes)}"
        )
    return next(iter(scopes), None)


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    already_scoped = {
        table_name
        for table_name in SCOPED_TABLES
        if "simulation_id" in {
            column["name"] for column in inspector.get_columns(table_name)
        }
    }
    if already_scoped == set(SCOPED_TABLES):
        return
    if already_scoped:
        missing = sorted(set(SCOPED_TABLES) - already_scoped)
        raise RuntimeError(
            "Cannot adopt partially scoped FactStore schema; missing simulation_id in: "
            + ", ".join(missing)
        )

    for table_name in SCOPED_TABLES:
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "simulation_id",
                    sa.String(),
                    nullable=False,
                    server_default="default",
                )
            )

    entity_rows = connection.execute(
        sa.text("SELECT entity_id FROM entities")
    ).mappings()
    entity_scopes = {
        row["entity_id"]: _scope_from_id(row["entity_id"])
        for row in entity_rows
    }
    if entity_scopes:
        connection.execute(
            sa.text(
                "UPDATE entities SET simulation_id = :simulation_id "
                "WHERE entity_id = :row_id"
            ),
            [
                {"simulation_id": scope, "row_id": entity_id}
                for entity_id, scope in entity_scopes.items()
            ],
        )

    scopes = set(entity_scopes.values())
    for table_name, (primary_key, source_column) in SCOPED_TABLES.items():
        if table_name == "entities":
            continue
        rows = connection.execute(
            sa.text(
                f"SELECT {primary_key} AS row_id, {source_column} AS source_id "
                f"FROM {table_name}"
            )
        ).mappings()
        updates = []
        for row in rows:
            scope = entity_scopes.get(
                row["source_id"], _scope_from_id(row["source_id"])
            )
            scopes.add(scope)
            updates.append({"simulation_id": scope, "row_id": row["row_id"]})
        if updates:
            connection.execute(
                sa.text(
                    f"UPDATE {table_name} SET simulation_id = :simulation_id "
                    f"WHERE {primary_key} = :row_id"
                ),
                updates,
            )

    for table_name, primary_key in (
        ("channels", "channel_id"),
        ("conversation_threads", "thread_id"),
        ("events", "event_id"),
    ):
        rows = connection.execute(
            sa.text(
                f"SELECT {primary_key} AS row_id, simulation_id, participants "
                f"FROM {table_name}"
            )
        ).mappings()
        updates = []
        for row in rows:
            participant_scope = _scope_from_participants(
                row["participants"], entity_scopes
            )
            if participant_scope is None:
                continue
            if row["simulation_id"] not in {"default", participant_scope}:
                raise RuntimeError(
                    f"Legacy {table_name} row {row['row_id']} crosses simulation scopes"
                )
            scopes.add(participant_scope)
            updates.append({
                "simulation_id": participant_scope,
                "row_id": row["row_id"],
            })
        if updates:
            connection.execute(
                sa.text(
                    f"UPDATE {table_name} SET simulation_id = :simulation_id "
                    f"WHERE {primary_key} = :row_id"
                ),
                updates,
            )

    existing_simulations = {
        row["id"]
        for row in connection.execute(sa.text("SELECT id FROM simulations")).mappings()
    }
    missing_scopes = sorted(scopes - existing_simulations)
    if missing_scopes:
        now = datetime.now(UTC).replace(tzinfo=None)
        simulation_table = sa.table(
            "simulations",
            sa.column("id", sa.String()),
            sa.column("name", sa.String()),
            sa.column("prompt", sa.Text()),
            sa.column("status", sa.String()),
            sa.column("current_tick", sa.Integer()),
            sa.column("is_active", sa.Boolean()),
            sa.column("graph_data", sa.JSON()),
            sa.column("total_cost_usd", sa.Float()),
            sa.column("created_at", sa.DateTime()),
            sa.column("updated_at", sa.DateTime()),
        )
        op.bulk_insert(
            simulation_table,
            [
                {
                    "id": scope,
                    "name": scope,
                    "prompt": "",
                    "status": "migrated",
                    "current_tick": 0,
                    "is_active": False,
                    "graph_data": {},
                    "total_cost_usd": 0.0,
                    "created_at": now,
                    "updated_at": now,
                }
                for scope in missing_scopes
            ],
        )

    for table_name, indexes in SCOPED_INDEXES.items():
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            for index_name, columns in indexes:
                batch_op.create_index(index_name, columns, unique=False)


def downgrade() -> None:
    for table_name in reversed(list(SCOPED_TABLES)):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            for index_name, _ in reversed(SCOPED_INDEXES.get(table_name, [])):
                batch_op.drop_index(index_name)
            batch_op.drop_column("simulation_id")
