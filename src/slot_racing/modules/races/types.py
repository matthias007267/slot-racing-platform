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
    lane: int | None
    start_number: int | str | None = None
    disqualified: bool = False


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
    duration_minutes: int | None = None
    active_lanes: int = 0
    """Lanes this race plans. The track's ``lane_count`` stays the physical count."""
    repetitions: int = 1
    """How often the rotation plan is driven. One is a single pass of that plan."""

    @property
    def is_editable(self) -> bool:
        return self.status in (RaceStatus.CREATED, RaceStatus.READY)

    @property
    def is_over(self) -> bool:
        return self.status in (RaceStatus.FINISHED, RaceStatus.ABORTED)


@dataclass(frozen=True, slots=True)
class RestartSeat:
    """One copied assignment. ``issue_key`` is empty when the assignment can be kept."""

    driver_id: DriverId
    driver_label: str
    vehicle_id: VehicleId | None
    vehicle_label: str
    lane: int | None
    issue_key: str | None = None
    issue_driver: str = ""
    issue_vehicle: str = ""
    issue_lane: int | None = None
    issue_maximum: int | None = None


@dataclass(frozen=True, slots=True)
class RestartPreview:
    """Settings a new race would take from a finished or aborted one. Nothing is stored yet."""

    source_id: RaceId
    source_name: str
    suggested_name: str
    track_id: TrackId | None
    track_name: str
    lane_count: int
    mode: RaceMode
    laps: int
    timing_provider: str
    duration_minutes: int | None
    active_lanes: int
    repetitions: int
    uses_heats: bool
    seats: tuple[RestartSeat, ...]


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
    excluded: bool = False


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


@dataclass(frozen=True, slots=True)
class HeatInfo:
    """One stored heat. ``seats`` is ``(participant_id, lane)`` for drivers who are in it."""

    sequence: int
    status: str
    seats: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class HeatSeat:
    lane: int
    participant_id: int | None
    driver_label: str


@dataclass(frozen=True, slots=True)
class LaneChange:
    """Where a driver goes for the next heat. ``from_lane`` is empty when they did not just race."""

    driver_label: str
    from_lane: int | None
    to_lane: int


@dataclass(frozen=True, slots=True)
class HeatBriefing:
    """Who is needed before the next heat, including lanes that stay empty."""

    race_id: RaceId
    sequence: int
    heat_count: int
    seats: tuple[HeatSeat, ...]
    changes: tuple[LaneChange, ...]
    drivers: tuple[tuple[int, str], ...]
