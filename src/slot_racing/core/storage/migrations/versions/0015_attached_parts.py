"""attached parts ride on one plan instance

An accessory is another instance of a library part. It stores the id of the
rail it follows and a slot relative to that rail. Existing rows stay null, so
an older plan is not reclassified and its pose is not rewritten.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.add_column(sa.Column("attachment_host_shape", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("attachment_slots", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("attachment_host_length_mm", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("attachment_host_radius_mm", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("attachment_host_angle_deg", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("attachment_host_lanes", sa.Integer(), nullable=True))
    # Batch copies on SQLite can drop an expression index. Put the identity back.
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_definitions_identity"))
    op.create_index(
        "uq_track_part_definitions_identity",
        "track_part_definitions",
        [sa.text("lower(trim(name))"), sa.text("trim(article_number)")],
        unique=True,
    )
    with op.batch_alter_table("track_plan_instances") as batch_op:
        batch_op.add_column(sa.Column("host_instance_id", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("attachment_slot", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("track_plan_instances") as batch_op:
        batch_op.drop_column("attachment_slot")
        batch_op.drop_column("host_instance_id")
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_definitions_identity"))
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.drop_column("attachment_host_lanes")
        batch_op.drop_column("attachment_host_angle_deg")
        batch_op.drop_column("attachment_host_radius_mm")
        batch_op.drop_column("attachment_host_length_mm")
        batch_op.drop_column("attachment_slots")
        batch_op.drop_column("attachment_host_shape")
    op.create_index(
        "uq_track_part_definitions_identity",
        "track_part_definitions",
        [sa.text("lower(trim(name))"), sa.text("trim(article_number)")],
        unique=True,
    )
