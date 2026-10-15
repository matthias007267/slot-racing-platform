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
    UniqueConstraint,
    false,
    func,
    true,
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
    # Stable across renames and geometry revisions. One lineage, many frozen bodies.
    lineage_id: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # The library places the current revision. Older ones stay for saved plans.
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # catalog, modified, or custom. Seeding must not overwrite modified or custom rows.
    origin: Mapped[str] = mapped_column(String(16), default="custom", server_default="custom")
    # Set for a bundled catalogue part. A rename keeps this, so seeding does not insert a twin.
    catalog_key: Mapped[str | None] = mapped_column(String(200))
    manufacturer: Mapped[str] = mapped_column(String(80), default="", server_default="")
    description: Mapped[str | None] = mapped_column(String(500))
    # Source UUID. Empty for a part created in this application.
    source_id: Mapped[str] = mapped_column(String(40), default="", server_default="")
    # Internal piece key. Empty when this article has a single body.
    piece_key: Mapped[str] = mapped_column(String(40), default="", server_default="")
    # Comma-separated system names. evolution, digital, or both.
    systems: Mapped[str] = mapped_column(String(40), default="", server_default="")
    selectable: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # Kept for a saved plan. Hidden from the active library and from new plans.
    legacy: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


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
    target_source_id: Mapped[str] = mapped_column(String(40), default="", server_default="")
    edge_types: Mapped[str] = mapped_column(String(80), default="", server_default="")
    exclusive: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    has_neighbor: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    neighbor_x: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    neighbor_y: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    neighbor_angle: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    lane_points: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)


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
    host_dx: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    host_dy: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    host_da: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")


class TrackPartTemplate(Base):
    """A placement recipe. It is not a physical part and has no stock of its own."""

    __tablename__ = "track_part_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(40), default="", server_default="")
    article_number: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))


class TrackPartTemplateMember(Base):
    __tablename__ = "track_part_template_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("track_part_templates.id", ondelete="CASCADE")
    )
    part_id: Mapped[int] = mapped_column(
        ForeignKey("track_part_definitions.id", ondelete="RESTRICT")
    )
    x_mm: Mapped[float] = mapped_column(Float)
    y_mm: Mapped[float] = mapped_column(Float)
    angle_deg: Mapped[float] = mapped_column(Float, default=0.0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class TrackPartStock(Base):
    """Manual correction on top of package-derived pieces. May be negative.

    The physical count is ``max(0, derived + quantity)``. A missing row means
    no correction. This column is not a count of purchased boxes.
    """

    __tablename__ = "track_part_stock"

    part_id: Mapped[int] = mapped_column(
        ForeignKey("track_part_definitions.id", ondelete="CASCADE"), primary_key=True
    )
    quantity: Mapped[int] = mapped_column(Integer)


class TrackPartPackage(Base):
    """One retail box. The article number is the sales SKU, not a placed piece."""

    __tablename__ = "track_part_packages"
    __table_args__ = (UniqueConstraint("manufacturer", "article_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    manufacturer: Mapped[str] = mapped_column(String(80))
    article_number: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    # A bundled package the user removed. Seeding must not insert it again.
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Supplied by the bundled catalogue. A package the user created stays false.
    bundled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class TrackPartPackageContent(Base):
    """How many of one physical definition a single package contains."""

    __tablename__ = "track_part_package_contents"
    __table_args__ = (
        UniqueConstraint("package_id", "part_id"),
        CheckConstraint("quantity > 0", name="quantity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("track_part_packages.id", ondelete="CASCADE")
    )
    part_id: Mapped[int] = mapped_column(
        ForeignKey("track_part_definitions.id", ondelete="RESTRICT")
    )
    quantity: Mapped[int] = mapped_column(Integer)


class TrackPartPackageStock(Base):
    """How many of one package the user owns. Zero is stored on purpose."""

    __tablename__ = "track_part_package_stock"
    __table_args__ = (CheckConstraint("quantity >= 0", name="quantity"),)

    package_id: Mapped[int] = mapped_column(
        ForeignKey("track_part_packages.id", ondelete="RESTRICT"), primary_key=True
    )
    quantity: Mapped[int] = mapped_column(Integer)


Index(
    "uq_track_part_definitions_identity",
    func.lower(func.trim(TrackPartDefinition.name)),
    func.trim(TrackPartDefinition.article_number),
    func.trim(TrackPartDefinition.piece_key),
    unique=True,
    sqlite_where=(TrackPartDefinition.is_current.is_(True)) & (TrackPartDefinition.source_id == ""),
)
Index(
    "uq_track_part_source_current",
    TrackPartDefinition.source_id,
    unique=True,
    sqlite_where=(TrackPartDefinition.is_current.is_(True)) & (TrackPartDefinition.source_id != ""),
)
Index(
    "uq_track_part_lineage_revision",
    TrackPartDefinition.lineage_id,
    TrackPartDefinition.revision,
    unique=True,
)
Index(
    "uq_track_part_lineage_current",
    TrackPartDefinition.lineage_id,
    unique=True,
    sqlite_where=TrackPartDefinition.is_current.is_(True),
)
