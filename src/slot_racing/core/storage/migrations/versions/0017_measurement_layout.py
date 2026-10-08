"""time measurements remember the layout they were driven on

Existing rows stay empty. They are not assigned to whatever plan is current now.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("time_measurements", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("track_layout_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_time_measurements_track_layout_id_track_layouts"),
            "track_layouts",
            ["track_layout_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("time_measurements", recreate="always") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_time_measurements_track_layout_id_track_layouts"), type_="foreignkey"
        )
        batch_op.drop_column("track_layout_id")
