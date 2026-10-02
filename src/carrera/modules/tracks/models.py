from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, func, true
from sqlalchemy.orm import Mapped, mapped_column

from carrera.core.storage.base import Base


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    lane_count: Mapped[int] = mapped_column(default=2)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    image_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class TrackLayout(Base):
    __tablename__ = "track_layouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    """Opaque layout description. Its format belongs to the track planner."""
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
