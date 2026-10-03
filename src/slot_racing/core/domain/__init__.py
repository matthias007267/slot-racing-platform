"""Plain domain types shared between modules. No persistence, no UI."""

from slot_racing.core.domain.ids import DriverId, RaceId, TrackId, VehicleId
from slot_racing.core.domain.lanes import DEFAULT_LANE_COUNT, MAX_LANE_COUNT, MIN_LANE_COUNT
from slot_racing.core.domain.race import Participant, ParticipantResult, RaceMode, RaceStatus
from slot_racing.core.domain.timing import (
    TimingLayout,
    TimingPosition,
    TimingPositionType,
    TimingSensor,
    TimingSetup,
    default_timing_setup,
)

__all__ = [
    "DEFAULT_LANE_COUNT",
    "MAX_LANE_COUNT",
    "MIN_LANE_COUNT",
    "DriverId",
    "Participant",
    "ParticipantResult",
    "RaceId",
    "RaceMode",
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
