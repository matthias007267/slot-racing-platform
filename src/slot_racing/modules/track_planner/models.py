"""Library parts and the instances placed on a track plan.

Definitions are shared. Instances belong to one track and point at a definition.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from slot_racing.core.storage.base import Base


class TrackPartDefinition(Base):
    """One library part. Identity is the normalised name plus the article number."""

    __tablename__ = "track_part_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_number: Mapped[str] = mapped_column(String(40))
    # Always 1:24, 1:32 or 1:43. The scale is a property, not part of the identity.
    scale: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32))
    length_mm: Mapped[float | None] = mapped_column(Float)
    width_mm: Mapped[float | None] = mapped_column(Float)
    height_mm: Mapped[float | None] = mapped_column(Float)
    radius_mm: Mapped[float | None] = mapped_column(Float)
    angle_deg: Mapped[float | None] = mapped_column(Float)
    lane_count: Mapped[int]
    outline: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # Grooves for diverging parts. Null or empty means the ordinary lanes are derived.
    slot_paths: Mapped[list[Any] | None] = mapped_column(JSON)
    # A catalog part the user removed. The row stays so seeding does not recreate it.
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Set together when this definition rides on a host instead of joining the track.
    attachment_host_shape: Mapped[str | None] = mapped_column(String(16))
    attachment_slots: Mapped[str | None] = mapped_column(String(40))
    attachment_host_length_mm: Mapped[float | None] = mapped_column(Float)
    attachment_host_radius_mm: Mapped[float | None] = mapped_column(Float)
    attachment_host_angle_deg: Mapped[float | None] = mapped_column(Float)
    attachment_host_lanes: Mapped[int | None] = mapped_column(Integer)


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
    # One placed connecting rail may be start and finish. The definition stays unmarked.
    is_start_straight: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Instances that share a group id move and select together. Empty means ungrouped.
    group_id: Mapped[str | None] = mapped_column(String(40))
    # Position in the plan. New instances are appended, so this is the creation order.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The rail this instance rides on. Empty for every ordinary track piece.
    host_instance_id: Mapped[str | None] = mapped_column(String(40))
    attachment_slot: Mapped[str | None] = mapped_column(String(16))


class TrackPartStock(Base):
    """How many of one library part the user owns. The definition itself is not copied."""

    __tablename__ = "track_part_stock"
    __table_args__ = (CheckConstraint("quantity >= 0", name="quantity"),)

    part_id: Mapped[int] = mapped_column(
        ForeignKey("track_part_definitions.id", ondelete="CASCADE"), primary_key=True
    )
    quantity: Mapped[int] = mapped_column(Integer)


Index(
    "uq_track_part_definitions_identity",
    func.lower(func.trim(TrackPartDefinition.name)),
    func.trim(TrackPartDefinition.article_number),
    unique=True,
)
