"""retail packages separate from physical part stock

A sales article can contain several physical definitions. Existing
``track_part_stock.quantity`` values stay as the manual correction and are
not read as a number of purchased boxes. Package stock starts empty, so the
physical count does not change until a user enters package quantities.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "track_part_packages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("manufacturer", sa.String(length=80), nullable=False),
        sa.Column("article_number", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_part_packages")),
        sa.UniqueConstraint(
            "manufacturer",
            "article_number",
            name=op.f("uq_track_part_packages_manufacturer_article_number"),
        ),
    )
    op.create_table(
        "track_part_package_contents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_track_part_package_contents_quantity")),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["track_part_packages.id"],
            name=op.f("fk_track_part_package_contents_package_id_track_part_packages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["track_part_definitions.id"],
            name=op.f("fk_track_part_package_contents_part_id_track_part_definitions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_part_package_contents")),
        sa.UniqueConstraint(
            "package_id",
            "part_id",
            name=op.f("uq_track_part_package_contents_package_id_part_id"),
        ),
    )
    op.create_table(
        "track_part_package_stock",
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity >= 0", name=op.f("ck_track_part_package_stock_quantity")),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["track_part_packages.id"],
            name=op.f("fk_track_part_package_stock_package_id_track_part_packages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("package_id", name=op.f("pk_track_part_package_stock")),
    )
    with op.batch_alter_table("track_part_stock") as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_track_part_stock_quantity"), type_="check")


def downgrade() -> None:
    op.drop_table("track_part_package_stock")
    op.drop_table("track_part_package_contents")
    op.drop_table("track_part_packages")
    with op.batch_alter_table("track_part_stock") as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_track_part_stock_quantity"), "quantity >= 0"
        )
