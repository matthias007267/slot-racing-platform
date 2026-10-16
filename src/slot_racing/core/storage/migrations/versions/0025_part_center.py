"""Store the part centre, its rotation and the short label.

These values used to be read from the bundled files through ``source_id``.
A copy or a custom part has no source id, so a reload lost them. They are
columns now. The angle is degrees, the unit already stored in the source
``center.angle`` and used by the painter. Nothing is converted or rounded.

A row whose source id is a bundled body, and whose new columns are still the
default, receives that body's original centre and label. A row without a
source id keeps the compatible fallback: centre (0, 0), angle 0, empty label.
Those copies no longer have a file to copy from, so the migration does not
invent a centre from the outline. Plan poses, joints and stock are not
updated. A value that is already not the default is left as stored.

Revision ID: 0025
Revises: 0024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slot_racing.modules.track_planner.catalog_import import load_carrera_library

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.add_column(
            sa.Column("center_x_mm", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("center_y_mm", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("center_angle_deg", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("short_label", sa.String(length=80), nullable=False, server_default="")
        )
    statement = sa.text(
        "UPDATE track_part_definitions SET "
        "center_x_mm = :x, center_y_mm = :y, center_angle_deg = :angle, "
        "short_label = :label "
        "WHERE source_id = :source_id "
        "AND center_x_mm = 0 AND center_y_mm = 0 AND center_angle_deg = 0 "
        "AND short_label = ''"
    )
    bind = op.get_bind()
    for spec in load_carrera_library().parts:
        bind.execute(
            statement,
            {
                "x": spec.center_x_mm,
                "y": spec.center_y_mm,
                "angle": spec.center_angle_deg,
                "label": spec.short_label,
                "source_id": spec.source_id,
            },
        )


def downgrade() -> None:
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.drop_column("short_label")
        batch_op.drop_column("center_angle_deg")
        batch_op.drop_column("center_y_mm")
        batch_op.drop_column("center_x_mm")
