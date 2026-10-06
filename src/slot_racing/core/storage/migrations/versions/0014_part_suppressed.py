"""remember catalogue parts the user removed

Deleting an unused bundled part must not let the next library load insert it
again. The row stays, marked suppressed, so plans that already use a part are
unchanged and seeding can see the identity. Custom parts are still deleted.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "suppressed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    # Batch copies on SQLite can drop an expression index. Put the identity back.
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_definitions_identity"))
    op.create_index(
        "uq_track_part_definitions_identity",
        "track_part_definitions",
        [sa.text("lower(trim(name))"), sa.text("trim(article_number)")],
        unique=True,
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_track_part_definitions_identity"))
    with op.batch_alter_table("track_part_definitions") as batch_op:
        batch_op.drop_column("suppressed")
    op.create_index(
        "uq_track_part_definitions_identity",
        "track_part_definitions",
        [sa.text("lower(trim(name))"), sa.text("trim(article_number)")],
        unique=True,
    )
