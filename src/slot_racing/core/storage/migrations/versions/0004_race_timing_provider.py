"""race timing provider

Adds ``races.timing_provider``, the stable id of the timing provider that times a race. Existing
races were all timed by the simulation, which is also the column default, so they keep working.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "timing_provider",
                sa.String(length=64),
                nullable=False,
                server_default="simulation",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.drop_column("timing_provider")
