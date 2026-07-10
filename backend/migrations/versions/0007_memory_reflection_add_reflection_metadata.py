"""add memory reflection metadata

Revision ID: 0007_reflection
Revises: 0006_memory
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_reflection"
down_revision: Union[str, Sequence[str], None] = "0006_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def upgrade() -> None:
    insight_columns = _column_names("semantic_insights")
    if "provenance" not in insight_columns:
        op.add_column(
            "semantic_insights",
            sa.Column("provenance", sa.JSON(), nullable=False, server_default="[]"),
        )

    profile_columns = _column_names("agent_memory_profiles")
    if "last_narrative_tick" not in profile_columns:
        op.add_column(
            "agent_memory_profiles",
            sa.Column(
                "last_narrative_tick",
                sa.Integer(),
                nullable=False,
                server_default="-1",
            ),
        )


def downgrade() -> None:
    profile_columns = _column_names("agent_memory_profiles")
    if "last_narrative_tick" in profile_columns:
        op.drop_column("agent_memory_profiles", "last_narrative_tick")
    insight_columns = _column_names("semantic_insights")
    if "provenance" in insight_columns:
        op.drop_column("semantic_insights", "provenance")
