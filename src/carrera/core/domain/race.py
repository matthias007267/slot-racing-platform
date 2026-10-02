"""Race related value types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from carrera.core.domain.ids import DriverId, VehicleId


class RaceStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


@dataclass(frozen=True, slots=True)
class Participant:
    """A driver assigned to a lane."""

    driver_id: DriverId
    lane: int
    vehicle_id: VehicleId | None = None

    def __post_init__(self) -> None:
        if self.lane < 1:
            raise ValueError("lane numbers start at 1")


@dataclass(frozen=True, slots=True)
class ParticipantResult:
    """Standing of one participant. Times are integer nanoseconds of race time."""

    driver_id: DriverId
    lane: int
    position: int
    laps_completed: int
    finished: bool
    total_time_ns: int | None
    best_lap_ns: int | None
