"""races remember how many lanes they use

A two-lane track can run one lane or both. Empty means the track's own lane count, which is
what every race planned before this choice existed. Results already stored are not rewritten.

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("active_lanes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.drop_column("active_lanes")
