"""track timing layout: logical positions and sensor assignment

Adds ``timing_positions`` and extends ``timing_sensors`` with a name, the position it reports,
an opaque hardware id and an active flag. Existing sensor rows are kept (the new columns are
nullable or have defaults). A track is limited to one timing configuration. Tracks without stored
positions keep working: the application falls back to the default layout.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "timing_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("configuration_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("sequence_index", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["timing_configurations.id"],
            name=op.f("fk_timing_positions_configuration_id_timing_configurations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timing_positions")),
        sa.UniqueConstraint(
            "configuration_id",
            "position_id",
            name=op.f("uq_timing_positions_configuration_id_position_id"),
        ),
        sa.UniqueConstraint(
            "configuration_id",
            "sequence_index",
            name=op.f("uq_timing_positions_configuration_id_sequence_index"),
        ),
    )

    with op.batch_alter_table("timing_configurations", recreate="always") as batch_op:
        batch_op.create_unique_constraint(op.f("uq_timing_configurations_track_id"), ["track_id"])

    with op.batch_alter_table("timing_sensors", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("position_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("hardware_id", sa.String(length=100), nullable=True))
        batch_op.add_column(
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False)
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_timing_sensors_position_id_timing_positions"),
            "timing_positions",
            ["position_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_timing_sensors_configuration_id_sensor_id"),
            ["configuration_id", "sensor_id"],
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_timing_sensors_configuration_id_hardware_id"),
            ["configuration_id", "hardware_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("timing_sensors", recreate="always") as batch_op:
        batch_op.drop_constraint(
            batch_op.f("uq_timing_sensors_configuration_id_hardware_id"), type_="unique"
        )
        batch_op.drop_constraint(
            batch_op.f("uq_timing_sensors_configuration_id_sensor_id"), type_="unique"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_timing_sensors_position_id_timing_positions"), type_="foreignkey"
        )
        batch_op.drop_column("is_active")
        batch_op.drop_column("hardware_id")
        batch_op.drop_column("position_id")
        batch_op.drop_column("name")

    with op.batch_alter_table("timing_configurations", recreate="always") as batch_op:
        batch_op.drop_constraint(op.f("uq_timing_configurations_track_id"), type_="unique")

    op.drop_table("timing_positions")
