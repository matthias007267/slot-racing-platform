from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from carrera.core.storage.base import Base


class TimingConfiguration(Base):
    """The timing configuration of one track: its logical layout and the sensors."""

    __tablename__ = "timing_configurations"
    __table_args__ = (UniqueConstraint("track_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    source_type: Mapped[str] = mapped_column(String(32))
    """Provider normally used to time the track, for example ``simulation``, ``camera`` or
    ``raspberry_pi``. Informational for now; the layout is the same for every provider."""
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class TimingPosition(Base):
    """A logical timing position of a track (start/finish or a sector boundary)."""

    __tablename__ = "timing_positions"
    __table_args__ = (
        UniqueConstraint("configuration_id", "position_id"),
        UniqueConstraint("configuration_id", "sequence_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    configuration_id: Mapped[int] = mapped_column(
        ForeignKey("timing_configurations.id", ondelete="CASCADE")
    )
    position_id: Mapped[str] = mapped_column(String(64))
    """Stable identifier used in events, for example ``sector_1``."""
    type: Mapped[str] = mapped_column(String(16))
    sequence_index: Mapped[int]
    """1-based order along the lap; 1 is START_FINISH."""
    name: Mapped[str | None] = mapped_column(String(100))


class TimingSensor(Base):
    """A sensor that reports one logical position. ``hardware_id`` is opaque to everything but
    the timing provider that owns the device."""

    __tablename__ = "timing_sensors"
    __table_args__ = (
        UniqueConstraint("configuration_id", "sensor_id", "lane"),
        UniqueConstraint("configuration_id", "sensor_id"),
        UniqueConstraint("configuration_id", "hardware_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    configuration_id: Mapped[int] = mapped_column(
        ForeignKey("timing_configurations.id", ondelete="CASCADE")
    )
    sensor_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    """Type of the position the sensor reports (mirrors ``timing_positions.type``)."""
    sequence_index: Mapped[int]
    """Order of the sensor; equals the order of its position (1 is START_FINISH)."""
    lane: Mapped[int | None]
    """Reserved for sensors that only watch one lane. Not used yet."""
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    name: Mapped[str | None] = mapped_column(String(100))
    position_id: Mapped[int | None] = mapped_column(
        ForeignKey("timing_positions.id", ondelete="CASCADE")
    )
    hardware_id: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
