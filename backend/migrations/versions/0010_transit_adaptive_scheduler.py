"""add durable transit journeys

Revision ID: 0010_transit
Revises: 0009_comms
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_transit"
down_revision: Union[str, Sequence[str], None] = "0009_comms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "transit_journeys" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "transit_journeys",
        sa.Column("journey_id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("from_kami_id", sa.String(), nullable=False),
        sa.Column("to_kami_id", sa.String(), nullable=False),
        sa.Column("requested_at_tick", sa.Integer(), nullable=False),
        sa.Column("depart_at_tick", sa.Integer(), nullable=False),
        sa.Column("arrive_at_tick", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.entity_id"]),
        sa.ForeignKeyConstraint(["from_kami_id"], ["entities.entity_id"]),
        sa.ForeignKeyConstraint(["to_kami_id"], ["entities.entity_id"]),
        sa.PrimaryKeyConstraint("journey_id"),
    )
    op.create_index(
        "ix_transit_journeys_sim_status_tick",
        "transit_journeys",
        ["simulation_id", "status", "depart_at_tick", "arrive_at_tick"],
    )
    op.create_index(
        "ix_transit_journeys_sim_entity_status",
        "transit_journeys",
        ["simulation_id", "entity_id", "status"],
    )


def downgrade() -> None:
    if "transit_journeys" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("transit_journeys")
