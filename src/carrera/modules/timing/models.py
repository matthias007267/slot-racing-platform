from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from carrera.core.storage.base import Base


class TimingConfiguration(Base):
    __tablename__ = "timing_configurations"

    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    source_type: Mapped[str] = mapped_column(String(32))
    """For example ``simulation``, ``camera`` or ``raspberry_pi``."""
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class TimingSensor(Base):
    __tablename__ = "timing_sensors"
    __table_args__ = (UniqueConstraint("configuration_id", "sensor_id", "lane"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    configuration_id: Mapped[int] = mapped_column(
        ForeignKey("timing_configurations.id", ondelete="CASCADE")
    )
    sensor_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    sequence_index: Mapped[int]
    """Position along the lap; 0 is START_FINISH."""
    lane: Mapped[int | None]
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
