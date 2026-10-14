"""races remember a repetition count

The count is configuration. Existing races get 1, which is the only value the
heat rotation and the lap target already used. Results and lap rows stay.

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("repetitions", sa.Integer(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.drop_column("repetitions")
