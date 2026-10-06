"""track part identity is name plus article number

Drop the system column and the old system/article/scale uniqueness. Parts that
share a normalised designation and article number become one row. Plan instances
move onto that row. Catalogue rails that match this identity are stored at 1:24.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Snapshot of the seeded catalogue. A later catalogue change does not rewrite stored scales.
_CATALOG_SCALE = "1:24"
_CATALOG_PARTS = (
    ("Standardgerade", "20020601"),
    ("1/3-Gerade", "20020611"),
    ("1/4-Gerade", "20020612"),
    ("Spurwechsel links", "20030343"),
    ("Spurwechsel rechts", "20030345"),
    ("Doppelspurwechsel", "20030347"),
    ("Weiche", "20020517"),
    ("Engstelle links", "20030350"),
    ("Engstelle rechts", "20030351"),
    ("Schikane", "20030373"),
    ("Pitlane-Gerade", "20030341"),
    ("Pitlane-Einfahrt", "20030356-E"),
    ("Pitlane-Ausfahrt", "20030356-A"),
    ("Kurve R1 30°", "20020577"),
    ("Kurve R1 60°", "20020571"),
    ("Kurve R2 30°", "20020572"),
    ("Kurve R3 30°", "20020573"),
    ("Kurve R4 15°", "20020578"),
    ("Steilkurve R1 30°", "20020574"),
    ("Kreuzung", "20020587"),
    ("Randstreifen Standardgerade", "20020560"),
)


def upgrade() -> None:
    bind = op.get_bind()
    _consolidate(bind)
    bind.execute(
        sa.text(
            "UPDATE track_part_definitions "
            "SET name = trim(name), article_number = trim(article_number)"
        )
    )
    _apply_catalog_scale(bind)
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.drop_constraint(
            "uq_track_part_definitions_system_article_number_scale",
            type_="unique",
        )
        batch_op.drop_column("system")
    op.create_index(
        "uq_track_part_definitions_identity",
        "track_part_definitions",
        [sa.text("lower(trim(name))"), sa.text("trim(article_number)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_track_part_definitions_identity",
        table_name="track_part_definitions",
    )
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.add_column(
            sa.Column("system", sa.String(length=80), nullable=False, server_default="")
        )
        batch_op.create_unique_constraint(
            "uq_track_part_definitions_system_article_number_scale",
            ["system", "article_number", "scale"],
        )


def _consolidate(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text("SELECT id, name, article_number, slot_paths FROM track_part_definitions")
    ).mappings()
    groups: dict[tuple[str, str], list[sa.RowMapping]] = {}
    for row in rows:
        groups.setdefault(_identity(str(row["name"]), str(row["article_number"])), []).append(row)
    for members in groups.values():
        if len(members) < 2:
            continue
        keeper = _keeper_id(members)
        for member in members:
            drop = int(member["id"])
            if drop == keeper:
                continue
            bind.execute(
                sa.text(
                    "UPDATE track_plan_instances SET part_id = :keeper WHERE part_id = :drop"
                ),
                {"keeper": keeper, "drop": drop},
            )
            bind.execute(
                sa.text("DELETE FROM track_part_connectors WHERE part_id = :drop"),
                {"drop": drop},
            )
            bind.execute(
                sa.text("DELETE FROM track_part_definitions WHERE id = :drop"),
                {"drop": drop},
            )


def _apply_catalog_scale(bind: sa.Connection) -> None:
    wanted = {_identity(name, article) for name, article in _CATALOG_PARTS}
    rows = bind.execute(
        sa.text("SELECT id, name, article_number FROM track_part_definitions")
    ).mappings()
    for row in rows:
        if _identity(str(row["name"]), str(row["article_number"])) not in wanted:
            continue
        bind.execute(
            sa.text("UPDATE track_part_definitions SET scale = :scale WHERE id = :id"),
            {"scale": _CATALOG_SCALE, "id": int(row["id"])},
        )


def _keeper_id(members: list[sa.RowMapping]) -> int:
    """Prefer a row that already stores grooves, then the earliest id."""

    def rank(member: sa.RowMapping) -> tuple[int, int]:
        return (1 if _has_paths(member["slot_paths"]) else 0, -int(member["id"]))

    chosen = max(members, key=rank)
    return int(chosen["id"])


def _has_paths(raw: object) -> bool:
    if raw is None:
        return False
    if isinstance(raw, str):
        return raw.strip() not in {"", "null", "[]"}
    if isinstance(raw, list):
        return len(raw) > 0
    return True


def _identity(name: str, article: str) -> tuple[str, str]:
    return (name.strip().casefold(), article.strip())
