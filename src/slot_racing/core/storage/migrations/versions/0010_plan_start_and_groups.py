"""start straight and instance groups

A start straight is one straight instance on a plan. A group is several instances that
stay selected and move together. Neither field changes the shared part definition.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("track_plan_instances", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_start_straight",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("group_id", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("track_plan_instances", recreate="always") as batch_op:
        batch_op.drop_column("group_id")
        batch_op.drop_column("is_start_straight")
