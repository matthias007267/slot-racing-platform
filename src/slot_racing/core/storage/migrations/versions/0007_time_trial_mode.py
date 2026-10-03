"""time trial mode

Adds ``races.mode`` so a race can be scored as a lap race or a time trial. Existing races are
lap races. Measured times of a time trial are stored on their own rows, including the lane they
were driven on, and older measurements are kept when a faster one arrives.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("mode", sa.String(length=32), nullable=False, server_default="laps")
        )
    op.create_table(
        "time_measurements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=True),
        sa.Column("lane", sa.Integer(), nullable=False),
        sa.Column("time_ns", sa.BigInteger(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["driver_id"],
            ["drivers.id"],
            name=op.f("fk_time_measurements_driver_id_drivers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["race_id"],
            ["races.id"],
            name=op.f("fk_time_measurements_race_id_races"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_time_measurements_track_id_tracks"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name=op.f("fk_time_measurements_vehicle_id_vehicles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_measurements")),
    )


def downgrade() -> None:
    op.drop_table("time_measurements")
    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.drop_column("mode")
