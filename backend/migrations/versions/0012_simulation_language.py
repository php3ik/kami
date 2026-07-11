"""add simulation content language

Revision ID: 0012_sim_language
Revises: 0011_world_build_jobs
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_sim_language"
down_revision: Union[str, Sequence[str], None] = "0011_world_build_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("simulations")}
    if "content_language" not in columns:
        op.add_column(
            "simulations",
            sa.Column(
                "content_language",
                sa.String(length=2),
                nullable=False,
                server_default="en",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("simulations")}
    if "content_language" in columns:
        op.drop_column("simulations", "content_language")
