from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, true
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from slot_racing.core.storage.base import Base


class StartNumberColumn(TypeDecorator[int | str | None]):
    """Integer column that also keeps an already stored text token such as ``B``.

    Numeric values stay integers. A non-numeric token is written and read unchanged, so the
    selection can offer it without renumbering anything that is already stored.
    """

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: int | str | None, dialect: Dialect) -> int | str | None:
        del dialect
        if value is None or isinstance(value, int):
            return value
        text = value.strip()
        if text.isdigit():
            return int(text)
        return text

    def process_result_value(self, value: int | str | None, dialect: Dialect) -> int | str | None:
        del dialect
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return int(text)
            return text
        return value


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str | None] = mapped_column(String(50))
    start_number: Mapped[int | str | None] = mapped_column(StartNumberColumn, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    manufacturer: Mapped[str | None] = mapped_column(String(100))
    scale: Mapped[str | None] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)
    start_number: Mapped[int | str | None] = mapped_column(StartNumberColumn)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id", ondelete="SET NULL"))
    """Owner. Many vehicles may belong to one driver."""
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
