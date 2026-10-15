"""Source identity, piece keys, templates and archived catalogue rows.

The active library is identified by the source UUID. An official article number
may be shared. Legacy rows stay so a saved plan can still resolve them.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_definitions_identity"))
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.add_column(sa.Column("source_id", sa.String(length=40), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("piece_key", sa.String(length=40), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("systems", sa.String(length=40), nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("selectable", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("legacy", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("track_part_connectors") as batch_op:
        batch_op.add_column(
            sa.Column("target_source_id", sa.String(length=40), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("edge_types", sa.String(length=80), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("exclusive", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("has_neighbor", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("neighbor_x", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("neighbor_y", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(
            sa.Column("neighbor_angle", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("lane_points", sa.JSON(), nullable=True))
    with op.batch_alter_table("track_plan_instances") as batch_op:
        batch_op.add_column(sa.Column("host_dx", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("host_dy", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("host_da", sa.Float(), nullable=False, server_default="0"))
    op.create_table(
        "track_part_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("article_number", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
    )
    op.create_table(
        "track_part_template_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Integer(),
            sa.ForeignKey("track_part_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "part_id",
            sa.Integer(),
            sa.ForeignKey("track_part_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("x_mm", sa.Float(), nullable=False),
        sa.Column("y_mm", sa.Float(), nullable=False),
        sa.Column("angle_deg", sa.Float(), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_track_part_definitions_identity "
            "ON track_part_definitions ("
            "lower(trim(name)), trim(article_number), trim(piece_key)"
            ") WHERE is_current = 1 AND source_id = ''"
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_track_part_source_current "
            "ON track_part_definitions (source_id) "
            "WHERE is_current = 1 AND source_id != ''"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_source_current"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_definitions_identity"))
    op.drop_table("track_part_template_members")
    op.drop_table("track_part_templates")
    with op.batch_alter_table("track_plan_instances") as batch_op:
        batch_op.drop_column("host_da")
        batch_op.drop_column("host_dy")
        batch_op.drop_column("host_dx")
    with op.batch_alter_table("track_part_connectors") as batch_op:
        batch_op.drop_column("lane_points")
        batch_op.drop_column("neighbor_angle")
        batch_op.drop_column("neighbor_y")
        batch_op.drop_column("neighbor_x")
        batch_op.drop_column("has_neighbor")
        batch_op.drop_column("exclusive")
        batch_op.drop_column("edge_types")
        batch_op.drop_column("target_source_id")
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.drop_column("legacy")
        batch_op.drop_column("selectable")
        batch_op.drop_column("systems")
        batch_op.drop_column("piece_key")
        batch_op.drop_column("source_id")
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_track_part_definitions_identity "
            "ON track_part_definitions (lower(trim(name)), trim(article_number)) "
            "WHERE is_current = 1"
        )
    )
