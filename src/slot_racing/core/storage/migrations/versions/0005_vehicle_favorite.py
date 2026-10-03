"""vehicle favorite

Adds ``vehicles.is_favorite``. The favorite is the vehicle suggested for its driver when a race
is created. Existing vehicles start without that mark.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("vehicles", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("vehicles", recreate="always") as batch_op:
        batch_op.drop_column("is_favorite")
