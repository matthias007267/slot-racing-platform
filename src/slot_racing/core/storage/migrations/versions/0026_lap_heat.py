"""Remember which heat stored a lap.

A discarded attempt deletes the laps of that heat and the times linked to
them. Existing laps stay, with an empty heat id, so older history is not
assigned to a heat after the fact. Deleting a heat does not delete the laps.

Revision ID: 0026
Revises: 0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("laps", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("heat_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_laps_heat_id_race_heats"),
            "race_heats",
            ["heat_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("laps", recreate="always") as batch_op:
        batch_op.drop_constraint(op.f("fk_laps_heat_id_race_heats"), type_="foreignkey")
        batch_op.drop_column("heat_id")
