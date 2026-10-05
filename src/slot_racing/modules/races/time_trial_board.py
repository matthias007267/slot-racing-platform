"""Live time-trial board, derived from stored measurements.

Nothing here is saved again. The absolute lane record is the fastest measurement on that lane.
The current attempt is the subset that belongs to one race. A slower lap never replaces either
figure, and lanes are never combined.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from slot_racing.core.domain import RaceId
from slot_racing.modules.races.types import ParticipantInfo, TimeMeasurementInfo

RECENT_LAP_COUNT = 5


@dataclass(frozen=True, slots=True)
class LaneRecordLine:
    """Absolute best on one lane, whoever drove it. Empty when the lane has no measurement."""

    lane: int
    driver_label: str | None
    vehicle_label: str | None
    time_ns: int | None


@dataclass(frozen=True, slots=True)
class ActiveLaneLine:
    """Who is assigned to a lane in the current race. An empty lane stays in the list."""

    lane: int
    driver_label: str | None
    vehicle_label: str | None
    occupied: bool


@dataclass(frozen=True, slots=True)
class RecentLapLine:
    """One completed lap of the current attempt. ``offset`` -1 is the newest lap."""

    offset: int
    time_ns: int


@dataclass(frozen=True, slots=True)
class DriverAttempt:
    """Last laps of one driver who is on the track now, plus the two bests kept apart."""

    lane: int
    driver_label: str
    vehicle_label: str
    recent: tuple[RecentLapLine, ...]
    session_best_ns: int | None
    lane_record_ns: int | None


@dataclass(frozen=True, slots=True)
class TimeTrialBoard:
    records: tuple[LaneRecordLine, ...]
    active: tuple[ActiveLaneLine, ...]
    attempts: tuple[DriverAttempt, ...]


def format_lap_seconds(duration_ns: int | None) -> str:
    """One lap as German seconds, for example ``8,241 s``. Missing times stay ``-``."""
    if duration_ns is None:
        return "-"
    millis = duration_ns // 1_000_000
    seconds, fraction = divmod(millis, 1000)
    return f"{seconds},{fraction:03d} s"


def build_time_trial_board(
    *,
    lane_count: int,
    race_id: RaceId,
    measurements: Sequence[TimeMeasurementInfo],
    participants: Sequence[ParticipantInfo],
) -> TimeTrialBoard:
    """One line per track lane. ``measurements`` are already limited to that track."""
    lanes = range(1, max(lane_count, 0) + 1)
    by_lane: dict[int, list[TimeMeasurementInfo]] = {lane: [] for lane in lanes}
    for row in measurements:
        bucket = by_lane.get(row.lane)
        if bucket is not None:
            bucket.append(row)
    assigned: dict[int, ParticipantInfo] = {}
    for entry in participants:
        lane = entry.lane
        if lane is not None and lane in by_lane:
            assigned[lane] = entry

    records: list[LaneRecordLine] = []
    active: list[ActiveLaneLine] = []
    attempts: list[DriverAttempt] = []
    for lane in lanes:
        rows = by_lane[lane]
        record = _best(rows)
        records.append(
            LaneRecordLine(
                lane=lane,
                driver_label=None if record is None else record.driver_label,
                vehicle_label=None if record is None else record.vehicle_label,
                time_ns=None if record is None else record.time_ns,
            )
        )
        driver = assigned.get(lane)
        active.append(
            ActiveLaneLine(
                lane=lane,
                driver_label=None if driver is None else driver.driver_label,
                vehicle_label=None if driver is None else driver.vehicle_label,
                occupied=driver is not None,
            )
        )
        if driver is None:
            continue
        session = [row for row in rows if row.race_id == race_id]
        ordered = sorted(session, key=_chronological)
        tail = ordered[-RECENT_LAP_COUNT:]
        count = len(tail)
        session_best = _best(session)
        attempts.append(
            DriverAttempt(
                lane=lane,
                driver_label=driver.driver_label,
                vehicle_label=driver.vehicle_label,
                recent=tuple(
                    RecentLapLine(offset=index - count, time_ns=row.time_ns)
                    for index, row in enumerate(tail)
                ),
                session_best_ns=None if session_best is None else session_best.time_ns,
                lane_record_ns=None if record is None else record.time_ns,
            )
        )
    return TimeTrialBoard(records=tuple(records), active=tuple(active), attempts=tuple(attempts))


def _best(rows: Sequence[TimeMeasurementInfo]) -> TimeMeasurementInfo | None:
    """Fastest time. An equal time keeps the earlier measurement."""
    if not rows:
        return None
    return min(rows, key=lambda row: (row.time_ns, _stamp(row.recorded_at), row.id))


def _chronological(row: TimeMeasurementInfo) -> tuple[tuple[int, float], int]:
    return (_stamp(row.recorded_at), row.id)


def _stamp(moment: datetime | None) -> tuple[int, float]:
    if moment is None:
        return (0, 0.0)
    return (1, moment.timestamp())
