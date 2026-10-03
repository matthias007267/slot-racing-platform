"""shared race vehicle

Drops the unique pair of race and vehicle. Several drivers in one race may use the same vehicle
record, so a car that exists more than once does not have to be created again.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("race_participants", recreate="always") as batch_op:
        batch_op.drop_constraint(
            batch_op.f("uq_race_participants_race_id_vehicle_id"), type_="unique"
        )


def downgrade() -> None:
    with op.batch_alter_table("race_participants", recreate="always") as batch_op:
        batch_op.create_unique_constraint(
            batch_op.f("uq_race_participants_race_id_vehicle_id"), ["race_id", "vehicle_id"]
        )
