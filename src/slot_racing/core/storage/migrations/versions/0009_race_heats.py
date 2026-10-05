"""race heats and time-trial duration

A driver's lane belongs to one heat. Existing races have no heat rows and keep the single lane
stored on the participant. Time trials may store a duration in whole minutes; an empty duration
still ends only when the session is stopped.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("duration_minutes", sa.Integer(), nullable=True))
    with op.batch_alter_table("race_participants", recreate="always") as batch_op:
        batch_op.alter_column("lane", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(
            sa.Column(
                "disqualified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("laps", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("lane", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE laps SET lane = ("
            "SELECT race_participants.lane FROM race_participants "
            "WHERE race_participants.id = laps.participant_id)"
        )
    )
    op.create_table(
        "race_heats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="planned", nullable=False),
        sa.ForeignKeyConstraint(
            ["race_id"],
            ["races.id"],
            name=op.f("fk_race_heats_race_id_races"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_race_heats")),
        sa.UniqueConstraint("race_id", "sequence", name=op.f("uq_race_heats_race_id_sequence")),
    )
    op.create_table(
        "race_heat_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("heat_id", sa.Integer(), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="assigned", nullable=False),
        sa.ForeignKeyConstraint(
            ["heat_id"],
            ["race_heats.id"],
            name=op.f("fk_race_heat_entries_heat_id_race_heats"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["race_participants.id"],
            name=op.f("fk_race_heat_entries_participant_id_race_participants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_race_heat_entries")),
        sa.UniqueConstraint("heat_id", "lane", name=op.f("uq_race_heat_entries_heat_id_lane")),
        sa.UniqueConstraint(
            "heat_id",
            "participant_id",
            name=op.f("uq_race_heat_entries_heat_id_participant_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("race_heat_entries")
    op.drop_table("race_heats")
    with op.batch_alter_table("laps", recreate="always") as batch_op:
        batch_op.drop_column("lane")
    with op.batch_alter_table("race_participants", recreate="always") as batch_op:
        batch_op.drop_column("disqualified")
        batch_op.alter_column("lane", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("races", recreate="always") as batch_op:
        batch_op.drop_column("duration_minutes")
