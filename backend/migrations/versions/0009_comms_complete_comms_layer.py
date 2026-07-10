"""complete CommsLayer delivery state

Revision ID: 0009_comms
Revises: 0008_kami_memory
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_comms"
down_revision: Union[str, Sequence[str], None] = "0008_kami_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    message_columns = {column["name"] for column in inspector.get_columns("messages")}
    with op.batch_alter_table("messages") as batch_op:
        if "kind" not in message_columns:
            batch_op.add_column(
                sa.Column("kind", sa.String(), nullable=False, server_default="message")
            )
        if "metadata" not in message_columns:
            batch_op.add_column(
                sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}")
            )

    if "message_deliveries" not in set(inspector.get_table_names()):
        op.create_table(
            "message_deliveries",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("simulation_id", sa.String(), nullable=False),
            sa.Column("message_id", sa.String(), nullable=False),
            sa.Column("recipient_id", sa.String(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("available_at_tick", sa.Integer(), nullable=False),
            sa.Column("created_at_tick", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["message_id"], ["messages.message_id"]),
            sa.ForeignKeyConstraint(["recipient_id"], ["entities.entity_id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "message_id", "recipient_id", name="uq_message_delivery_recipient"
            ),
        )
        op.create_index(
            "ix_message_deliveries_sim_recipient_status",
            "message_deliveries",
            ["simulation_id", "recipient_id", "status", "available_at_tick"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "message_deliveries" in set(inspector.get_table_names()):
        op.drop_table("message_deliveries")
    message_columns = {column["name"] for column in inspector.get_columns("messages")}
    with op.batch_alter_table("messages") as batch_op:
        if "metadata" in message_columns:
            batch_op.drop_column("metadata")
        if "kind" in message_columns:
            batch_op.drop_column("kind")
