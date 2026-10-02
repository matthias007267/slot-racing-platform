"""Plain domain types shared between modules. No persistence, no UI."""

from carrera.core.domain.ids import DriverId, RaceId, TrackId, VehicleId
from carrera.core.domain.race import Participant, ParticipantResult, RaceStatus
from carrera.core.domain.timing import (
    TimingLayout,
    TimingPosition,
    TimingPositionType,
    TimingSensor,
    TimingSetup,
    default_timing_setup,
)

__all__ = [
    "DriverId",
    "Participant",
    "ParticipantResult",
    "RaceId",
    "RaceStatus",
    "TimingLayout",
    "TimingPosition",
    "TimingPositionType",
    "TimingSensor",
    "TimingSetup",
    "TrackId",
    "VehicleId",
    "default_timing_setup",
]
