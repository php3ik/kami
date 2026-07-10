"""add persistent memory hierarchy

Revision ID: 0006_memory
Revises: 0005_tick_ledger
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_memory"
down_revision: Union[str, Sequence[str], None] = "0005_tick_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    memory_tables = {
        "episodic_memories",
        "memory_summaries",
        "semantic_insights",
        "agent_memory_profiles",
    }
    present = memory_tables & existing
    if present:
        if present != memory_tables:
            missing = ", ".join(sorted(memory_tables - present))
            raise RuntimeError(f"Partially initialized memory schema; missing: {missing}")
        return

    op.create_table(
        "episodic_memories",
        sa.Column("memory_id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("participants", sa.JSON(), nullable=False),
        sa.Column("location", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("source_event_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["entities.entity_id"]),
        sa.PrimaryKeyConstraint("memory_id"),
        sa.UniqueConstraint(
            "simulation_id",
            "agent_id",
            "source_event_id",
            name="uq_episodic_memory_source",
        ),
    )
    op.create_index(
        "ix_episodic_memories_sim_agent_tick",
        "episodic_memories",
        ["simulation_id", "agent_id", "tick"],
    )

    op.create_table(
        "memory_summaries",
        sa.Column("summary_id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["entities.entity_id"]),
        sa.PrimaryKeyConstraint("summary_id"),
        sa.UniqueConstraint(
            "simulation_id", "agent_id", "tick", name="uq_memory_summary_day"
        ),
    )
    op.create_index(
        "ix_memory_summaries_sim_agent_tick",
        "memory_summaries",
        ["simulation_id", "agent_id", "tick"],
    )

    op.create_table(
        "semantic_insights",
        sa.Column("insight_id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False),
        sa.Column("created_tick", sa.Integer(), nullable=False),
        sa.Column("last_reinforced_tick", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["entities.entity_id"]),
        sa.PrimaryKeyConstraint("insight_id"),
    )
    op.create_index(
        "ix_semantic_insights_sim_agent_status",
        "semantic_insights",
        ["simulation_id", "agent_id", "status"],
    )

    op.create_table(
        "agent_memory_profiles",
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("life_narrative", sa.Text(), nullable=False),
        sa.Column("last_consolidation_tick", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["entities.entity_id"]),
        sa.PrimaryKeyConstraint("agent_id"),
    )
    op.create_index(
        "ix_memory_profiles_simulation",
        "agent_memory_profiles",
        ["simulation_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in (
        "agent_memory_profiles",
        "semantic_insights",
        "memory_summaries",
        "episodic_memories",
    ):
        if table in existing:
            op.drop_table(table)
