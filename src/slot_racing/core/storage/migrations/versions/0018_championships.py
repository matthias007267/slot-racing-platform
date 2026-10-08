"""championships

A championship names stored races and keeps its own points scheme, roster and the team a
driver had when a finished race was first scored. Race results are not copied.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "championships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("season_year", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="planned"),
        sa.Column("teams_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fastest_lap_bonus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("drop_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "tie_break",
            sa.String(length=32),
            nullable=False,
            server_default="points_wins_places",
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_championships")),
    )
    op.create_table(
        "championship_points",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("championship_id", sa.Integer(), nullable=False),
        sa.Column("place", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["championship_id"],
            ["championships.id"],
            name=op.f("fk_championship_points_championship_id_championships"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_championship_points")),
        sa.UniqueConstraint(
            "championship_id",
            "place",
            name=op.f("uq_championship_points_championship_id_place"),
        ),
    )
    op.create_table(
        "championship_races",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("championship_id", sa.Integer(), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["championship_id"],
            ["championships.id"],
            name=op.f("fk_championship_races_championship_id_championships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["race_id"],
            ["races.id"],
            name=op.f("fk_championship_races_race_id_races"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_championship_races")),
        sa.UniqueConstraint("race_id", name=op.f("uq_championship_races_race_id")),
        sa.UniqueConstraint(
            "championship_id",
            "sort_order",
            name=op.f("uq_championship_races_championship_id_sort_order"),
        ),
    )
    op.create_table(
        "championship_teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("championship_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["championship_id"],
            ["championships.id"],
            name=op.f("fk_championship_teams_championship_id_championships"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_championship_teams")),
        sa.UniqueConstraint(
            "championship_id", "name", name=op.f("uq_championship_teams_championship_id_name")
        ),
    )
    op.create_table(
        "championship_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("championship_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["championship_id"],
            ["championships.id"],
            name=op.f("fk_championship_members_championship_id_championships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["championship_teams.id"],
            name=op.f("fk_championship_members_team_id_championship_teams"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["driver_id"],
            ["drivers.id"],
            name=op.f("fk_championship_members_driver_id_drivers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_championship_members")),
        sa.UniqueConstraint(
            "championship_id",
            "driver_id",
            name=op.f("uq_championship_members_championship_id_driver_id"),
        ),
    )
    op.create_table(
        "championship_race_teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("championship_id", sa.Integer(), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("team_name", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["championship_id"],
            ["championships.id"],
            name=op.f("fk_championship_race_teams_championship_id_championships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["race_id"],
            ["races.id"],
            name=op.f("fk_championship_race_teams_race_id_races"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["driver_id"],
            ["drivers.id"],
            name=op.f("fk_championship_race_teams_driver_id_drivers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["championship_teams.id"],
            name=op.f("fk_championship_race_teams_team_id_championship_teams"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_championship_race_teams")),
        sa.UniqueConstraint(
            "championship_id",
            "race_id",
            "driver_id",
            name=op.f("uq_championship_race_teams_championship_id_race_id_driver_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("championship_race_teams")
    op.drop_table("championship_members")
    op.drop_table("championship_teams")
    op.drop_table("championship_races")
    op.drop_table("championship_points")
    op.drop_table("championships")
