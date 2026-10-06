"""personal part stock and stable plan order

Owned quantities reference a part definition. They are not a second library.
``sort_order`` keeps the order instances were added, so the same copies stay
marked as excess after a reload.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "track_part_stock",
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity >= 0", name=op.f("ck_track_part_stock_quantity")),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["track_part_definitions.id"],
            name=op.f("fk_track_part_stock_part_id_track_part_definitions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("part_id", name=op.f("pk_track_part_stock")),
    )
    with op.batch_alter_table("track_plan_instances") as batch_op:
        batch_op.add_column(
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("track_plan_instances") as batch_op:
        batch_op.drop_column("sort_order")
    op.drop_table("track_part_stock")
