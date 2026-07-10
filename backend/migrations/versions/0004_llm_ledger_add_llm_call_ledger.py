"""add persistent llm call ledger

Revision ID: 0004_llm_ledger
Revises: 0003_scope
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_llm_ledger"
down_revision: Union[str, Sequence[str], None] = "0003_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    simulation_columns = {
        column["name"] for column in inspector.get_columns("simulations")
    }
    if "budget_limit_usd" not in simulation_columns:
        op.add_column(
            "simulations",
            sa.Column("budget_limit_usd", sa.Float(), nullable=True),
        )

    if "llm_calls" not in inspector.get_table_names():
        op.create_table(
            "llm_calls",
            sa.Column("call_id", sa.String(), nullable=False),
            sa.Column("simulation_id", sa.String(), nullable=True),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("component", sa.String(), nullable=False),
            sa.Column("tick", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False),
            sa.Column("output_tokens", sa.Integer(), nullable=False),
            sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
            sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
            sa.Column("cost_usd", sa.Float(), nullable=False),
            sa.Column("error_type", sa.String(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("call_id"),
        )
        op.create_index(
            "ix_llm_calls_sim_completed",
            "llm_calls",
            ["simulation_id", "completed_at"],
        )
        op.create_index(
            "ix_llm_calls_sim_tick",
            "llm_calls",
            ["simulation_id", "tick"],
        )
        op.create_index(
            "ix_llm_calls_sim_component",
            "llm_calls",
            ["simulation_id", "component"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "llm_calls" in inspector.get_table_names():
        op.drop_table("llm_calls")
    simulation_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("simulations")
    }
    if "budget_limit_usd" in simulation_columns:
        op.drop_column("simulations", "budget_limit_usd")
