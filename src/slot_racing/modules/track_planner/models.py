"""Library parts and the instances placed on a track plan.

Definitions are shared. Instances belong to one track and point at a definition.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from slot_racing.core.storage.base import Base


class TrackPartDefinition(Base):
    __tablename__ = "track_part_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    system: Mapped[str] = mapped_column(String(80))
    article_number: Mapped[str] = mapped_column(String(40))
    # Empty when the system already names the scale. No manufacturer column.
    scale: Mapped[str | None] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32))
    length_mm: Mapped[float | None] = mapped_column(Float)
    width_mm: Mapped[float | None] = mapped_column(Float)
    height_mm: Mapped[float | None] = mapped_column(Float)
    radius_mm: Mapped[float | None] = mapped_column(Float)
    angle_deg: Mapped[float | None] = mapped_column(Float)
    lane_count: Mapped[int]
    outline: Mapped[list[Any]] = mapped_column(JSON, default=list)


class TrackPartConnector(Base):
    __tablename__ = "track_part_connectors"

    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(
        ForeignKey("track_part_definitions.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(40))
    x_mm: Mapped[float] = mapped_column(Float)
    y_mm: Mapped[float] = mapped_column(Float)
    z_mm: Mapped[float] = mapped_column(Float, default=0.0)
    direction_deg: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(16))
    lanes: Mapped[list[Any]] = mapped_column(JSON)
    sort_order: Mapped[int] = mapped_column(default=0)


class TrackPlanInstance(Base):
    __tablename__ = "track_plan_instances"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    part_id: Mapped[int] = mapped_column(
        ForeignKey("track_part_definitions.id", ondelete="RESTRICT")
    )
    x_mm: Mapped[float] = mapped_column(Float)
    y_mm: Mapped[float] = mapped_column(Float)
    z_mm: Mapped[float] = mapped_column(Float, default=0.0)
    rotation_x_deg: Mapped[float] = mapped_column(Float, default=0.0)
    rotation_y_deg: Mapped[float] = mapped_column(Float, default=0.0)
    rotation_z_deg: Mapped[float] = mapped_column(Float, default=0.0)
