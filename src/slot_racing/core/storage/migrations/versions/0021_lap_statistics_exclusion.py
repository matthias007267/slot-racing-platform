"""mark a stored lap as excluded from statistics

The lap and any time-trial measurement stay in the database. Existing rows
count, because the flag defaults to false. A measurement may point at the lap
it came from; older measurements stay unlinked until an exclusion matches them.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        batch_op.add_column(
            sa.Column(
                "statistics_excluded",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("time_measurements", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "statistics_excluded",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("lap_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_time_measurements_lap_id_laps"),
            "laps",
            ["lap_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("time_measurements", recreate="always") as batch_op:
        batch_op.drop_constraint(op.f("fk_time_measurements_lap_id_laps"), type_="foreignkey")
        batch_op.drop_column("lap_id")
        batch_op.drop_column("statistics_excluded")
    with op.batch_alter_table("laps") as batch_op:
        batch_op.drop_column("statistics_excluded")
