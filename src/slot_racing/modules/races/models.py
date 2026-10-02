from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from slot_racing.core.storage.base import Base
from slot_racing.core.timing_registry import DEFAULT_TIMING_PROVIDER


class Race(Base):
    __tablename__ = "races"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="RESTRICT"))
    track_layout_id: Mapped[int | None] = mapped_column(
        ForeignKey("track_layouts.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), default="created")
    target_laps: Mapped[int] = mapped_column(default=10)
    timing_provider: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_TIMING_PROVIDER, server_default=DEFAULT_TIMING_PROVIDER
    )
    """Id of the timing provider that times this race. Resolved to a factory at the start."""
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class RaceParticipant(Base):
    __tablename__ = "race_participants"
    __table_args__ = (
        UniqueConstraint("race_id", "lane"),
        UniqueConstraint("race_id", "driver_id"),
        UniqueConstraint("race_id", "vehicle_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id", ondelete="CASCADE"))
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id", ondelete="RESTRICT"))
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"))
    lane: Mapped[int]
    final_position: Mapped[int | None]
    laps_completed: Mapped[int] = mapped_column(default=0, server_default="0")
    finished: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    total_time_ns: Mapped[int | None] = mapped_column(BigInteger)
    best_lap_ns: Mapped[int | None] = mapped_column(BigInteger)


class Lap(Base):
    __tablename__ = "laps"
    __table_args__ = (UniqueConstraint("participant_id", "lap_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id", ondelete="CASCADE"))
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("race_participants.id", ondelete="CASCADE")
    )
    lap_number: Mapped[int]
    lap_time_ns: Mapped[int] = mapped_column(BigInteger)
    race_time_ns: Mapped[int] = mapped_column(BigInteger)


class Sector(Base):
    __tablename__ = "sectors"
    __table_args__ = (UniqueConstraint("lap_id", "sector_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lap_id: Mapped[int] = mapped_column(ForeignKey("laps.id", ondelete="CASCADE"))
    sector_number: Mapped[int]
    sector_time_ns: Mapped[int] = mapped_column(BigInteger)
