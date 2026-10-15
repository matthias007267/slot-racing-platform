"""Remember which packages were supplied by the catalogue.

A package the user created has no source UUID. The flag separates it from a
bundled box, so a later catalogue reconcile can drop obsolete boxes without
looking at their names.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slot_racing.modules.track_planner.packages import standard_packages

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("track_part_packages") as batch_op:
        batch_op.add_column(
            sa.Column(
                "bundled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    for spec in standard_packages():
        op.execute(
            sa.text(
                "UPDATE track_part_packages SET bundled = 1 "
                "WHERE lower(trim(manufacturer)) = 'carrera' "
                "AND trim(article_number) = :article"
            ).bindparams(article=spec.article_number.strip())
        )


def downgrade() -> None:
    with op.batch_alter_table("track_part_packages") as batch_op:
        batch_op.drop_column("bundled")
