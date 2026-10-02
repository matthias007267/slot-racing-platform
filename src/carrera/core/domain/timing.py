"""Logical description of the timing points of a track.

A layout is an ordered list of timing points along one lap. The first point is always the
start/finish line. The remaining points are intermediate sector sensors in driving order.

Sector ``k`` (1-based) is the stretch that ends at the k-th point of the lap sequence
(``SECTOR_1`` ... ``SECTOR_n``, then ``START_FINISH``). A layout with ``n`` intermediate
sensors therefore has ``n + 1`` sectors; the last one ends at the start/finish line and
completes the lap.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class SensorRole(StrEnum):
    START_FINISH = "START_FINISH"
    SECTOR = "SECTOR"


@dataclass(frozen=True, slots=True)
class TimingPoint:
    sensor_id: str
    role: SensorRole
    sector_number: int | None = None

    @property
    def label(self) -> str:
        if self.role is SensorRole.START_FINISH:
            return SensorRole.START_FINISH.value
        return f"SECTOR_{self.sector_number}"


@dataclass(frozen=True, slots=True)
class TimingLayout:
    points: tuple[TimingPoint, ...]

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("a timing layout needs at least a start/finish point")
        if self.points[0].role is not SensorRole.START_FINISH:
            raise ValueError("the first timing point must be the start/finish line")
        if any(p.role is SensorRole.START_FINISH for p in self.points[1:]):
            raise ValueError("only one start/finish point is allowed")
        ids = [p.sensor_id for p in self.points]
        if len(set(ids)) != len(ids):
            raise ValueError("sensor ids must be unique within a layout")

    @classmethod
    def from_sensor_ids(cls, sensor_ids: Sequence[str]) -> TimingLayout:
        """Build a layout from sensor ids in driving order, start/finish first."""
        points = [
            TimingPoint(
                sensor_id=sensor_id,
                role=SensorRole.START_FINISH if index == 0 else SensorRole.SECTOR,
                sector_number=None if index == 0 else index,
            )
            for index, sensor_id in enumerate(sensor_ids)
        ]
        return cls(tuple(points))

    @property
    def sector_count(self) -> int:
        return len(self.points)

    @property
    def lap_sequence(self) -> tuple[TimingPoint, ...]:
        """Points in the order a car passes them during one lap, ending at start/finish."""
        return (*self.points[1:], self.points[0])
