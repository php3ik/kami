"""add durable world build jobs

Revision ID: 0011_world_build_jobs
Revises: 0010_transit
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_world_build_jobs"
down_revision: Union[str, Sequence[str], None] = "0010_transit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "world_build_jobs" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "world_build_jobs",
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("completed_units", sa.Integer(), nullable=False),
        sa.Column("total_units", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["simulation_id"], ["simulations.id"]),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("simulation_id"),
    )
    op.create_index(
        "ix_world_build_jobs_status_updated",
        "world_build_jobs",
        ["status", "updated_at"],
    )
    op.create_index(
        "ix_world_build_jobs_simulation",
        "world_build_jobs",
        ["simulation_id"],
    )


def downgrade() -> None:
    if "world_build_jobs" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("world_build_jobs")
