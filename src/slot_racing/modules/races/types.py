"""Read models returned by the race services. Plain data, no persistence or UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from slot_racing.core.domain import DriverId, RaceId, RaceMode, RaceStatus, TrackId, VehicleId


@dataclass(frozen=True, slots=True)
class ParticipantInfo:
    id: int
    driver_id: DriverId
    driver_label: str
    vehicle_id: VehicleId | None
    vehicle_label: str
    lane: int
    start_number: int | str | None = None


@dataclass(frozen=True, slots=True)
class RaceInfo:
    id: RaceId
    name: str
    track_id: TrackId | None
    track_name: str
    lane_count: int
    status: RaceStatus
    mode: RaceMode
    laps: int
    timing_provider: str
    participants: tuple[ParticipantInfo, ...]
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def is_editable(self) -> bool:
        return self.status in (RaceStatus.CREATED, RaceStatus.READY)

    @property
    def is_over(self) -> bool:
        return self.status in (RaceStatus.FINISHED, RaceStatus.ABORTED)


@dataclass(frozen=True, slots=True)
class ResultRow:
    """Stored result of one participant. ``position`` is the one the race engine determined."""

    participant_id: int
    position: int | None
    driver_label: str
    vehicle_label: str
    lane: int
    start_number: int | str | None
    laps_completed: int
    finished: bool
    total_time_ns: int | None
    best_lap_ns: int | None
    last_lap_ns: int | None
    average_lap_ns: int | None


@dataclass(frozen=True, slots=True)
class TimeMeasurementInfo:
    """One stored time-trial measurement. ``lane`` belongs to that run."""

    id: int
    race_id: RaceId
    track_id: TrackId | None
    driver_id: DriverId
    driver_label: str
    vehicle_id: VehicleId | None
    vehicle_label: str
    lane: int
    time_ns: int
    recorded_at: datetime | None


@dataclass(frozen=True, slots=True)
class TimeBest:
    """Fastest stored measurement for one combination. The lane is always part of the result."""

    measurement_id: int
    driver_id: DriverId
    driver_label: str
    vehicle_id: VehicleId | None
    vehicle_label: str
    lane: int
    track_id: TrackId | None
    time_ns: int
    recorded_at: datetime | None


@dataclass(frozen=True, slots=True)
class LaneRanking:
    """Drivers on one lane, fastest personal time on that lane first."""

    lane: int
    places: tuple[TimeBest, ...]


@dataclass(frozen=True, slots=True)
class LapRecord:
    participant_id: int
    lane: int
    lap_number: int
    lap_time_ns: int
    race_time_ns: int
    sector_times_ns: tuple[int, ...]
