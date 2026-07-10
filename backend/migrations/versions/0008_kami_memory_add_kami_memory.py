"""add durable Kami memory

Revision ID: 0008_kami_memory
Revises: 0007_reflection
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_kami_memory"
down_revision: Union[str, Sequence[str], None] = "0007_reflection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    tables = {
        "kami_memory_summaries",
        "kami_memory_profiles",
        "kami_imprints",
    }
    present = tables & existing
    if present:
        if present != tables:
            missing = ", ".join(sorted(tables - present))
            raise RuntimeError(f"Partially initialized Kami memory schema; missing: {missing}")
        return

    op.create_table(
        "kami_memory_summaries",
        sa.Column("summary_id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("kami_id", sa.String(), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("peak_salience", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["kami_id"], ["entities.entity_id"]),
        sa.PrimaryKeyConstraint("summary_id"),
        sa.UniqueConstraint(
            "simulation_id", "kami_id", "tick", name="uq_kami_memory_summary_day"
        ),
    )
    op.create_index(
        "ix_kami_memory_summaries_sim_kami_tick",
        "kami_memory_summaries",
        ["simulation_id", "kami_id", "tick"],
    )

    op.create_table(
        "kami_memory_profiles",
        sa.Column("kami_id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("long_term_memory", sa.Text(), nullable=False),
        sa.Column("last_consolidation_tick", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["kami_id"], ["entities.entity_id"]),
        sa.PrimaryKeyConstraint("kami_id"),
    )
    op.create_index(
        "ix_kami_memory_profiles_simulation",
        "kami_memory_profiles",
        ["simulation_id"],
    )

    op.create_table(
        "kami_imprints",
        sa.Column("imprint_id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("kami_id", sa.String(), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("fact", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("source_event_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["kami_id"], ["entities.entity_id"]),
        sa.PrimaryKeyConstraint("imprint_id"),
        sa.UniqueConstraint(
            "simulation_id",
            "kami_id",
            "source_event_id",
            name="uq_kami_imprint_source",
        ),
    )
    op.create_index(
        "ix_kami_imprints_sim_kami_tick",
        "kami_imprints",
        ["simulation_id", "kami_id", "tick"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in (
        "kami_imprints",
        "kami_memory_profiles",
        "kami_memory_summaries",
    ):
        if table in existing:
            op.drop_table(table)
