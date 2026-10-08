"""Championship tables. Race results stay on the race; these rows only name and score them."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column

from slot_racing.core.storage.base import Base


class Championship(Base):
    __tablename__ = "championships"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    season_year: Mapped[int]
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="planned", server_default="planned")
    """``planned``, ``active`` or ``completed``. A completed row is not edited."""
    teams_enabled: Mapped[bool] = mapped_column(default=False, server_default=false())
    fastest_lap_bonus: Mapped[int] = mapped_column(default=0, server_default="0")
    drop_count: Mapped[int] = mapped_column(default=0, server_default="0")
    tie_break: Mapped[str] = mapped_column(
        String(32), default="points_wins_places", server_default="points_wins_places"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChampionshipPoint(Base):
    __tablename__ = "championship_points"
    __table_args__ = (UniqueConstraint("championship_id", "place"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    championship_id: Mapped[int] = mapped_column(ForeignKey("championships.id", ondelete="CASCADE"))
    place: Mapped[int]
    points: Mapped[int]


class ChampionshipRaceLink(Base):
    """One race in one championship. The race row and its results are not owned here."""

    __tablename__ = "championship_races"
    __table_args__ = (
        UniqueConstraint("race_id"),
        UniqueConstraint("championship_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    championship_id: Mapped[int] = mapped_column(ForeignKey("championships.id", ondelete="CASCADE"))
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id", ondelete="CASCADE"))
    sort_order: Mapped[int]


class ChampionshipTeam(Base):
    __tablename__ = "championship_teams"
    __table_args__ = (UniqueConstraint("championship_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    championship_id: Mapped[int] = mapped_column(ForeignKey("championships.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))


class ChampionshipMember(Base):
    """Current roster. A past race keeps its own snapshot and does not read this row."""

    __tablename__ = "championship_members"
    __table_args__ = (UniqueConstraint("championship_id", "driver_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    championship_id: Mapped[int] = mapped_column(ForeignKey("championships.id", ondelete="CASCADE"))
    team_id: Mapped[int] = mapped_column(ForeignKey("championship_teams.id", ondelete="CASCADE"))
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id", ondelete="CASCADE"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChampionshipRaceTeam(Base):
    """Team of one driver for one finished race. Later roster changes do not rewrite it."""

    __tablename__ = "championship_race_teams"
    __table_args__ = (UniqueConstraint("championship_id", "race_id", "driver_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    championship_id: Mapped[int] = mapped_column(ForeignKey("championships.id", ondelete="CASCADE"))
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id", ondelete="CASCADE"))
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id", ondelete="CASCADE"))
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("championship_teams.id", ondelete="RESTRICT")
    )
    team_name: Mapped[str | None] = mapped_column(String(100))
