"""part lineage, revision and catalogue key

A geometry change of a part that is already on a plan inserts a new revision.
Older plans keep the previous row. The current name and article number stay
unique only among current revisions, so a frozen revision may share them.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

from slot_racing.modules.track_planner.parts import catalog_storage_key, standard_catalog

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_definitions_identity"))
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.add_column(sa.Column("lineage_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(
            sa.Column(
                "is_current",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "origin",
                sa.String(length=16),
                nullable=False,
                server_default="custom",
            )
        )
        batch_op.add_column(sa.Column("catalog_key", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column(
                "manufacturer",
                sa.String(length=80),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(sa.Column("description", sa.String(length=500), nullable=True))
    bind = op.get_bind()
    catalog = {catalog_storage_key(spec.name, spec.article_number) for spec in standard_catalog()}
    rows = bind.execute(sa.text("SELECT id, name, article_number FROM track_part_definitions"))
    for row in rows:
        key = catalog_storage_key(row.name, row.article_number)
        known = key in catalog
        bind.execute(
            sa.text(
                "UPDATE track_part_definitions "
                "SET lineage_id = :lineage, catalog_key = :catalog_key, "
                "origin = :origin, manufacturer = :manufacturer "
                "WHERE id = :part_id"
            ),
            {
                "lineage": uuid4().hex,
                "catalog_key": key if known else None,
                "origin": "catalog" if known else "custom",
                "manufacturer": "Carrera" if known else "",
                "part_id": row.id,
            },
        )
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.alter_column(
            "lineage_id",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default=sa.text("(lower(hex(randomblob(16))))"),
        )
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_definitions_identity"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_track_part_definitions_identity "
            "ON track_part_definitions (lower(trim(name)), trim(article_number)) "
            "WHERE is_current = 1"
        )
    )
    op.create_index(
        "uq_track_part_lineage_revision",
        "track_part_definitions",
        ["lineage_id", "revision"],
        unique=True,
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_track_part_lineage_current "
            "ON track_part_definitions (lineage_id) WHERE is_current = 1"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE track_plan_instances SET part_id = ("
            "SELECT current.id FROM track_part_definitions AS current "
            "JOIN track_part_definitions AS older "
            "ON older.lineage_id = current.lineage_id "
            "WHERE older.id = track_plan_instances.part_id AND current.is_current = 1"
            ") WHERE part_id IN ("
            "SELECT id FROM track_part_definitions WHERE is_current = 0)"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE track_part_package_contents SET part_id = ("
            "SELECT current.id FROM track_part_definitions AS current "
            "JOIN track_part_definitions AS older "
            "ON older.lineage_id = current.lineage_id "
            "WHERE older.id = track_part_package_contents.part_id "
            "AND current.is_current = 1"
            ") WHERE part_id IN ("
            "SELECT id FROM track_part_definitions WHERE is_current = 0)"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM track_part_stock WHERE part_id IN ("
            "SELECT id FROM track_part_definitions WHERE is_current = 0)"
        )
    )
    bind.execute(sa.text("DELETE FROM track_part_definitions WHERE is_current = 0"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_lineage_current"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_lineage_revision"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_definitions_identity"))
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.drop_column("description")
        batch_op.drop_column("manufacturer")
        batch_op.drop_column("catalog_key")
        batch_op.drop_column("origin")
        batch_op.drop_column("is_current")
        batch_op.drop_column("revision")
        batch_op.drop_column("lineage_id")
    op.create_index(
        "uq_track_part_definitions_identity",
        "track_part_definitions",
        [sa.text("lower(trim(name))"), sa.text("trim(article_number)")],
        unique=True,
    )
