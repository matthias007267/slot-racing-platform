"""track part library

Definitions, their joints, and the instances placed on a track. A definition is stored once.
Each placement keeps its own position and rotation.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "track_part_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("system", sa.String(length=80), nullable=False),
        sa.Column("article_number", sa.String(length=40), nullable=False),
        sa.Column("scale", sa.String(length=8), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("length_mm", sa.Float(), nullable=True),
        sa.Column("width_mm", sa.Float(), nullable=True),
        sa.Column("height_mm", sa.Float(), nullable=True),
        sa.Column("radius_mm", sa.Float(), nullable=True),
        sa.Column("angle_deg", sa.Float(), nullable=True),
        sa.Column("lane_count", sa.Integer(), nullable=False),
        sa.Column("outline", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_part_definitions")),
    )
    op.create_table(
        "track_part_connectors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("x_mm", sa.Float(), nullable=False),
        sa.Column("y_mm", sa.Float(), nullable=False),
        sa.Column("z_mm", sa.Float(), nullable=False),
        sa.Column("direction_deg", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("lanes", sa.JSON(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["track_part_definitions.id"],
            name=op.f("fk_track_part_connectors_part_id_track_part_definitions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_part_connectors")),
    )
    op.create_table(
        "track_plan_instances",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.Column("x_mm", sa.Float(), nullable=False),
        sa.Column("y_mm", sa.Float(), nullable=False),
        sa.Column("z_mm", sa.Float(), nullable=False),
        sa.Column("rotation_x_deg", sa.Float(), nullable=False),
        sa.Column("rotation_y_deg", sa.Float(), nullable=False),
        sa.Column("rotation_z_deg", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["track_part_definitions.id"],
            name=op.f("fk_track_plan_instances_part_id_track_part_definitions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_plan_instances_track_id_tracks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_plan_instances")),
    )


def downgrade() -> None:
    op.drop_table("track_plan_instances")
    op.drop_table("track_part_connectors")
    op.drop_table("track_part_definitions")
