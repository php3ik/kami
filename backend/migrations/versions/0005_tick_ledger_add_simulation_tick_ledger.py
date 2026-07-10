"""add durable simulation tick ledger

Revision ID: 0005_tick_ledger
Revises: 0004_llm_ledger
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_tick_ledger"
down_revision: Union[str, Sequence[str], None] = "0004_llm_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "simulation_ticks" in inspector.get_table_names():
        required = {
            "id",
            "simulation_id",
            "tick",
            "status",
            "attempt_count",
            "result",
            "error_message",
            "started_at",
            "completed_at",
        }
        existing = {
            column["name"] for column in inspector.get_columns("simulation_ticks")
        }
        if not required <= existing:
            missing = ", ".join(sorted(required - existing))
            raise RuntimeError(
                f"Partially initialized simulation_ticks table; missing: {missing}"
            )
        return

    op.create_table(
        "simulation_ticks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "simulation_id",
            "tick",
            name="uq_simulation_ticks_sim_tick",
        ),
    )
    op.create_index(
        "ix_simulation_ticks_sim_status",
        "simulation_ticks",
        ["simulation_id", "status"],
    )
    op.create_index(
        "ix_simulation_ticks_sim_completed",
        "simulation_ticks",
        ["simulation_id", "completed_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "simulation_ticks" in sa.inspect(bind).get_table_names():
        op.drop_table("simulation_ticks")
