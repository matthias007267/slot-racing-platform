"""Logical timing description of a track, independent of any hardware.

A :class:`TimingLayout` is the ordered list of *positions* along one lap. A position is a place
where cars are timed: the start/finish line (always first) or a sector boundary. The sector
that ends at the k-th position of the lap sequence is sector ``k``; a layout with ``n`` sector
positions therefore has ``n + 1`` sectors, the last one ending at start/finish and completing
the lap.

A :class:`TimingSensor` is something that reports a car passing one position. It has its own
identity (``id``, shown in events) and an optional ``hardware_id`` (GPIO pin, camera zone, ...)
that only the timing providers interpret. The race engine only ever sees positions.

A :class:`TimingSetup` combines a layout with its sensors and is what a track stores and what a
timing session receives.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from carrera.core.errors import ValidationError

MAX_ID_LENGTH = 64
MAX_TEXT_LENGTH = 100


class TimingPositionType(StrEnum):
    START_FINISH = "START_FINISH"
    SECTOR = "SECTOR"


@dataclass(frozen=True, slots=True)
class TimingPosition:
    """A logical place where cars are timed."""

    id: str
    """Stable identifier. It does not change when positions are reordered or renamed."""
    type: TimingPositionType
    order: int
    """1-based place along the lap. The start/finish line is order 1."""
    name: str | None = None
    """Display name chosen by the user. ``None`` means the default name for the position."""

    @property
    def sector_number(self) -> int | None:
        """Number of the sector position (``SECTOR_1`` is order 2), ``None`` for start/finish."""
        return None if self.type is TimingPositionType.START_FINISH else self.order - 1

    @property
    def label(self) -> str:
        """Language independent name: ``START_FINISH`` or ``SECTOR_n``."""
        if self.type is TimingPositionType.START_FINISH:
            return TimingPositionType.START_FINISH.value
        return f"SECTOR_{self.sector_number}"


@dataclass(frozen=True, slots=True)
class TimingLayout:
    """Ordered positions of a track. The engine and the timing sessions work with this."""

    positions: tuple[TimingPosition, ...]

    def __post_init__(self) -> None:
        positions = self.positions
        if not positions:
            raise ValidationError("error.timing.no_positions")
        starts = [p for p in positions if p.type is TimingPositionType.START_FINISH]
        if not starts:
            raise ValidationError("error.timing.start_finish_missing")
        if len(starts) > 1:
            raise ValidationError("error.timing.start_finish_duplicate")
        ids = [p.id for p in positions]
        for position_id in ids:
            _check_text(position_id, "position", MAX_ID_LENGTH, required=True)
        for position_id in {i for i in ids if ids.count(i) > 1}:
            raise ValidationError("error.timing.position_duplicate", position=position_id)
        for position in positions:
            _check_text(position.name, "position_name", MAX_TEXT_LENGTH)
        if [p.order for p in positions] != list(range(1, len(positions) + 1)):
            raise ValidationError("error.timing.order_invalid")
        if positions[0].type is not TimingPositionType.START_FINISH:
            raise ValidationError("error.timing.start_finish_first")

    @classmethod
    def from_position_ids(cls, position_ids: Sequence[str]) -> TimingLayout:
        """Build a layout from position ids in driving order, start/finish first."""
        return cls(
            tuple(
                TimingPosition(
                    id=position_id,
                    type=(
                        TimingPositionType.START_FINISH if index == 0 else TimingPositionType.SECTOR
                    ),
                    order=index + 1,
                )
                for index, position_id in enumerate(position_ids)
            )
        )

    @property
    def sector_count(self) -> int:
        """Number of sectors of one lap (sector positions plus the closing start/finish)."""
        return len(self.positions)

    @property
    def lap_sequence(self) -> tuple[TimingPosition, ...]:
        """Positions in the order a car passes them during one lap, ending at start/finish."""
        return (*self.positions[1:], self.positions[0])

    def position(self, position_id: str) -> TimingPosition:
        for position in self.positions:
            if position.id == position_id:
                return position
        raise ValidationError("error.timing.position_unknown", position=position_id)


@dataclass(frozen=True, slots=True)
class TimingSensor:
    """Reports cars passing one logical position."""

    id: str
    position_id: str
    name: str | None = None
    hardware_id: str | None = None
    """Opaque identifier of the physical or virtual device, for example ``GPIO17``."""
    active: bool = True


@dataclass(frozen=True, slots=True)
class TimingSetup:
    """A layout plus the sensors that time its positions. Every position has one sensor.

    The order of a sensor is the order of its position. Inactive sensors may be stored but a
    setup with an inactive sensor cannot time a race (:meth:`ensure_usable`).
    """

    layout: TimingLayout
    sensors: tuple[TimingSensor, ...]

    def __post_init__(self) -> None:
        positions = {p.id: p for p in self.layout.positions}
        seen_ids: set[str] = set()
        seen_hardware: set[str] = set()
        by_position: dict[str, int] = {}
        for sensor in self.sensors:
            _check_text(sensor.id, "sensor", MAX_ID_LENGTH, required=True)
            _check_text(sensor.name, "sensor_name", MAX_TEXT_LENGTH)
            _check_text(sensor.hardware_id, "hardware_id", MAX_TEXT_LENGTH)
            if sensor.id in seen_ids:
                raise ValidationError("error.timing.sensor_duplicate", sensor=sensor.id)
            seen_ids.add(sensor.id)
            if sensor.hardware_id:
                if sensor.hardware_id in seen_hardware:
                    raise ValidationError(
                        "error.timing.hardware_duplicate", hardware_id=sensor.hardware_id
                    )
                seen_hardware.add(sensor.hardware_id)
            if sensor.position_id not in positions:
                raise ValidationError(
                    "error.timing.sensor_unknown_position",
                    sensor=sensor.id,
                    position=sensor.position_id,
                )
            by_position[sensor.position_id] = by_position.get(sensor.position_id, 0) + 1
        for position in self.layout.positions:
            count = by_position.get(position.id, 0)
            if count == 0:
                raise ValidationError(
                    "error.timing.position_without_sensor", position=position.label
                )
            if count > 1:
                raise ValidationError(
                    "error.timing.position_multiple_sensors", position=position.label
                )
        ordered = sorted(self.sensors, key=lambda s: positions[s.position_id].order)
        object.__setattr__(self, "sensors", tuple(ordered))

    @classmethod
    def for_layout(cls, layout: TimingLayout) -> TimingSetup:
        """Setup whose sensors have the same ids as their positions (tests, simple cases)."""
        return cls(layout, tuple(TimingSensor(p.id, p.id) for p in layout.positions))

    @classmethod
    def from_position_ids(cls, position_ids: Sequence[str]) -> TimingSetup:
        return cls.for_layout(TimingLayout.from_position_ids(position_ids))

    def sensor_at(self, position_id: str) -> TimingSensor:
        for sensor in self.sensors:
            if sensor.position_id == position_id:
                return sensor
        raise ValidationError("error.timing.position_unknown", position=position_id)

    def sensor(self, sensor_id: str) -> TimingSensor:
        for sensor in self.sensors:
            if sensor.id == sensor_id:
                return sensor
        raise ValidationError("error.timing.sensor_unknown", sensor=sensor_id)

    def order_of(self, sensor_id: str) -> int:
        return self.layout.position(self.sensor(sensor_id).position_id).order

    @property
    def is_usable(self) -> bool:
        return all(sensor.active for sensor in self.sensors)

    def ensure_usable(self) -> None:
        """Raise if a position is only covered by an inactive sensor."""
        for sensor in self.sensors:
            if not sensor.active:
                position = self.layout.position(sensor.position_id)
                raise ValidationError(
                    "error.timing.sensor_inactive", sensor=sensor.id, position=position.label
                )

    def with_sensor(self, sensor: TimingSensor) -> TimingSetup:
        """Replace the sensor of ``sensor.position_id``."""
        kept = tuple(s for s in self.sensors if s.position_id != sensor.position_id)
        return replace(self, sensors=(*kept, sensor))


def default_timing_setup() -> TimingSetup:
    """Layout used for tracks that have no stored configuration yet."""
    layout = TimingLayout.from_position_ids(["start_finish", "sector_1", "sector_2"])
    ids = ("start_finish", "sensor_01", "sensor_02")
    return TimingSetup(
        layout,
        tuple(TimingSensor(sensor, p.id) for sensor, p in zip(ids, layout.positions, strict=True)),
    )


def _check_text(value: str | None, field: str, limit: int, *, required: bool = False) -> None:
    text = (value or "").strip()
    if required and not text:
        raise ValidationError(f"error.timing.{field}_blank")
    if len(text) > limit:
        raise ValidationError("error.timing.text_too_long", limit=limit)
