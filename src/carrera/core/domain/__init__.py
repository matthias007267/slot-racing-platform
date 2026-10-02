"""Plain domain types shared between modules. No persistence, no UI."""

from carrera.core.domain.ids import DriverId, RaceId, TrackId, VehicleId
from carrera.core.domain.race import Participant, ParticipantResult, RaceStatus
from carrera.core.domain.timing import SensorRole, TimingLayout, TimingPoint

__all__ = [
    "DriverId",
    "Participant",
    "ParticipantResult",
    "RaceId",
    "RaceStatus",
    "SensorRole",
    "TimingLayout",
    "TimingPoint",
    "TrackId",
    "VehicleId",
]
