"""drivers, vehicles, tracks and race results

Adds start numbers, timestamps, activity flags and driver/vehicle links, stores race results on
the participants and protects races from deleting their track, drivers and vehicles.
Existing rows are kept: ``drivers.nickname`` becomes ``drivers.display_name`` and new NOT NULL
columns get server defaults. The migration environment disables SQLite foreign keys while tables
are rebuilt, so no cascades fire.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("(CURRENT_TIMESTAMP)")


def upgrade() -> None:
    with op.batch_alter_table("drivers", recreate="always") as batch_op:
        batch_op.alter_column("nickname", new_column_name="display_name")
        batch_op.add_column(sa.Column("start_number", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("created_at", sa.DateTime(), server_default=NOW, nullable=False)
        )
        batch_op.add_column(
            sa.Column("updated_at", sa.DateTime(), server_default=NOW, nullable=False)
        )
        batch_op.create_unique_constraint(batch_op.f("uq_drivers_start_number"), ["start_number"])

    with op.batch_alter_table("tracks", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False)
        )
        batch_op.add_column(sa.Column("image_path", sa.String(length=500), nullable=True))
        batch_op.add_column(
            sa.Column("created_at", sa.DateTime(), server_default=NOW, nullable=False)
        )
        batch_op.add_column(
            sa.Column("updated_at", sa.DateTime(), server_default=NOW, nullable=False)
        )

    with op.batch_alter_table("vehicles", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("model", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("start_number", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False)
        )
        batch_op.add_column(sa.Column("driver_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("created_at", sa.DateTime(), server_default=NOW, nullable=False)
        )
        batch_op.add_column(
            sa.Column("updated_at", sa.DateTime(), server_default=NOW, nullable=False)
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_vehicles_driver_id_drivers"),
            "drivers",
            ["driver_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_races_track_id_tracks"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_races_track_id_tracks"),
            "tracks",
            ["track_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("race_participants", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("laps_completed", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("finished", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.add_column(sa.Column("total_time_ns", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("best_lap_ns", sa.BigInteger(), nullable=True))
        batch_op.create_unique_constraint(
            batch_op.f("uq_race_participants_race_id_vehicle_id"), ["race_id", "vehicle_id"]
        )
        batch_op.drop_constraint(
            batch_op.f("fk_race_participants_vehicle_id_vehicles"), type_="foreignkey"
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_race_participants_vehicle_id_vehicles"),
            "vehicles",
            ["vehicle_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("race_participants", recreate="always") as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_race_participants_vehicle_id_vehicles"), type_="foreignkey"
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_race_participants_vehicle_id_vehicles"),
            "vehicles",
            ["vehicle_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.drop_constraint(
            batch_op.f("uq_race_participants_race_id_vehicle_id"), type_="unique"
        )
        batch_op.drop_column("best_lap_ns")
        batch_op.drop_column("total_time_ns")
        batch_op.drop_column("finished")
        batch_op.drop_column("laps_completed")

    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_races_track_id_tracks"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_races_track_id_tracks"),
            "tracks",
            ["track_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("vehicles", recreate="always") as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_vehicles_driver_id_drivers"), type_="foreignkey")
        for column in ("updated_at", "created_at", "driver_id", "is_active", "start_number", "model"):
            batch_op.drop_column(column)

    with op.batch_alter_table("tracks", recreate="always") as batch_op:
        for column in ("updated_at", "created_at", "image_path", "is_active"):
            batch_op.drop_column(column)

    with op.batch_alter_table("drivers", recreate="always") as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_drivers_start_number"), type_="unique")
        for column in ("updated_at", "created_at", "start_number"):
            batch_op.drop_column(column)
        batch_op.alter_column("display_name", new_column_name="nickname")
