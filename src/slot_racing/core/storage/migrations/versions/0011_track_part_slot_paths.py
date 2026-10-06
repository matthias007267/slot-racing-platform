"""slot paths on a track part

Diverging parts store their grooves next to the outline. An empty value means
the ordinary lanes are derived from the measures, so older rows stay valid.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.add_column(sa.Column("slot_paths", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.drop_column("slot_paths")
