"""Race, driver, vehicle and track statistics.

The functions read plain facts and return plain facts. They do not open the database and they
do not draw. A stored lap is one completed lap: the pass that only starts a lap is not a row.
A non-positive time is ignored. An outlier stays in every average; it is only marked.
Missing comparisons stay ``None``. Nothing here invents a time, a place or a length.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from slot_racing.core.domain.race import RaceMode, RaceStatus

# A lap this far from the median of the same run is marked. It is still counted.
_OUTLIER_FACTOR = 1.5
_OUTLIER_MINIMUM = 4


@dataclass(frozen=True, slots=True)
class HistoryLap:
    participant_id: int
    lap_number: int
    lap_time_ns: int
    lane: int | None


@dataclass(frozen=True, slots=True)
class HistoryParticipant:
    participant_id: int
    driver_id: int
    driver_label: str
    vehicle_id: int | None
    vehicle_label: str
    lane: int | None
    position: int | None
    finished: bool
    disqualified: bool
    total_time_ns: int | None


@dataclass(frozen=True, slots=True)
class HistoryRace:
    """One stored race that has ended, with the laps that were actually written."""

    race_id: int
    name: str
    track_id: int | None
    track_name: str
    layout_id: int | None
    status: RaceStatus
    mode: RaceMode
    finished_at: datetime | None
    participants: tuple[HistoryParticipant, ...]
    laps: tuple[HistoryLap, ...]


@dataclass(frozen=True, slots=True)
class LayoutRevision:
    """One saved layout row. ``current`` is the plan the planner would open today."""

    layout_id: int
    track_id: int
    current: bool


@dataclass(frozen=True, slots=True)
class TimeScope:
    """Which stored races a career figure or a record list may use.

    Lap times are comparable only inside one track and one layout bucket. ``unassigned`` is the
    bucket of races that were stored before a layout existed. It is not the current plan.
    """

    track_id: int | None = None
    layout_id: int | None = None
    unassigned: bool = False
    driver_id: int | None = None
    vehicle_id: int | None = None
    lane: int | None = None
    since: date | None = None
    until: date | None = None


@dataclass(frozen=True, slots=True)
class ReportLine:
    participant_id: int
    position: int | None
    driver_label: str
    vehicle_label: str
    lane: int | None
    lap_count: int
    total_time_ns: int | None
    best_lap_ns: int | None
    average_lap_ns: int | None
    gap_ns: int | None
    stdev_ns: int | None
    disqualified: bool
    outlier_laps: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LapPoint:
    lap_number: int
    time_ns: int
    outlier: bool


@dataclass(frozen=True, slots=True)
class LapSeries:
    participant_id: int
    label: str
    points: tuple[LapPoint, ...]


@dataclass(frozen=True, slots=True)
class RaceReport:
    """Evaluation of one ended race. ``official`` is false when the race was aborted."""

    official: bool
    mode: RaceMode
    lines: tuple[ReportLine, ...]
    series: tuple[LapSeries, ...]


@dataclass(frozen=True, slots=True)
class CareerRace:
    race_id: int
    name: str
    when: datetime | None
    mode: RaceMode
    status: RaceStatus
    position: int | None
    laps: int
    best_lap_ns: int | None


@dataclass(frozen=True, slots=True)
class CareerSummary:
    """Counts always come from the filtered races. Lap-time headlines stay empty until the
    filter names one track and one layout bucket, so two layouts cannot become one best time.
    ``distance_mm`` stays empty: no stored length is read here.
    """

    races: int
    wins: int
    podiums: int
    laps: int
    best_lap_ns: int | None
    average_lap_ns: int | None
    stdev_ns: int | None
    distance_mm: None
    history: tuple[CareerRace, ...]
    trend: tuple[tuple[datetime, int], ...]


@dataclass(frozen=True, slots=True)
class RecordLine:
    time_ns: int
    driver_id: int
    driver_label: str
    vehicle_id: int | None
    vehicle_label: str
    lane: int | None
    when: datetime | None
    race_id: int
    race_name: str
    lap_number: int


def valid_lap_times(laps: Sequence[HistoryLap], participant_id: int) -> tuple[int, ...]:
    """Completed laps with a positive time, in lap order. A start-only pass is not in ``laps``."""
    ordered = sorted(
        (lap for lap in laps if lap.participant_id == participant_id),
        key=lambda lap: lap.lap_number,
    )
    return tuple(lap.lap_time_ns for lap in ordered if lap.lap_time_ns > 0)


def average_ns(times: Sequence[int]) -> int | None:
    if not times:
        return None
    return _half_up(sum(times), len(times))


def stdev_ns(times: Sequence[int]) -> int | None:
    """Sample standard deviation. One lap has no spread."""
    count = len(times)
    if count < 2:
        return None
    mean = sum(times) / count
    variance = sum((time - mean) ** 2 for time in times) / (count - 1)
    return int(math.sqrt(variance) + 0.5)


def outlier_lap_numbers(laps: Sequence[HistoryLap], participant_id: int) -> tuple[int, ...]:
    """Lap numbers that sit far from the median. They remain part of every other figure."""
    ordered = sorted(
        (lap for lap in laps if lap.participant_id == participant_id and lap.lap_time_ns > 0),
        key=lambda lap: lap.lap_number,
    )
    if len(ordered) < _OUTLIER_MINIMUM:
        return ()
    median = _median(tuple(lap.lap_time_ns for lap in ordered))
    if median <= 0:
        return ()
    high = median * _OUTLIER_FACTOR
    low = median / _OUTLIER_FACTOR
    return tuple(
        lap.lap_number for lap in ordered if lap.lap_time_ns > high or lap.lap_time_ns < low
    )


def race_report(race: HistoryRace) -> RaceReport:
    """Standings, gaps and series for one ended race.

    A lap race compares total time only when both drivers finished the same number of valid laps.
    A time trial compares the best valid lap. An aborted race is reported, but it is not official.
    """
    official = race.status is RaceStatus.FINISHED
    leader = _leader(race)
    leader_times = () if leader is None else valid_lap_times(race.laps, leader.participant_id)
    leader_score = None if leader is None else _comparison(race, leader, leader_times)
    lines: list[ReportLine] = []
    series: list[LapSeries] = []
    for participant in _ordered(race.participants):
        times = valid_lap_times(race.laps, participant.participant_id)
        outliers = outlier_lap_numbers(race.laps, participant.participant_id)
        own_score = _comparison(race, participant, times)
        gap = _gap(race, participant, times, leader, leader_times, leader_score, own_score)
        lines.append(
            ReportLine(
                participant_id=participant.participant_id,
                position=participant.position,
                driver_label=participant.driver_label,
                vehicle_label=participant.vehicle_label,
                lane=participant.lane,
                lap_count=len(times),
                total_time_ns=participant.total_time_ns,
                best_lap_ns=None if not times or participant.disqualified else min(times),
                average_lap_ns=None if participant.disqualified else average_ns(times),
                gap_ns=gap,
                stdev_ns=None if participant.disqualified else stdev_ns(times),
                disqualified=participant.disqualified,
                outlier_laps=() if participant.disqualified else outliers,
            )
        )
        if participant.disqualified or not times:
            continue
        marked = set(outliers)
        points = tuple(
            LapPoint(lap.lap_number, lap.lap_time_ns, lap.lap_number in marked)
            for lap in sorted(
                (
                    lap
                    for lap in race.laps
                    if lap.participant_id == participant.participant_id and lap.lap_time_ns > 0
                ),
                key=lambda lap: lap.lap_number,
            )
        )
        series.append(LapSeries(participant.participant_id, participant.driver_label, points))
    return RaceReport(official=official, mode=race.mode, lines=tuple(lines), series=tuple(series))


def career_summary(
    races: Sequence[HistoryRace],
    scope: TimeScope,
    *,
    driver_id: int | None = None,
    vehicle_id: int | None = None,
) -> CareerSummary:
    """History of one driver or one vehicle. Pass exactly one of the two ids."""
    subject_driver = driver_id if driver_id is not None else scope.driver_id
    subject_vehicle = vehicle_id if vehicle_id is not None else scope.vehicle_id
    chosen = [race for race in races if _in_scope(race, scope)]
    comparable = _times_are_comparable(scope)
    history: list[CareerRace] = []
    trend: list[tuple[datetime, int]] = []
    wins = 0
    podiums = 0
    lap_total = 0
    pooled: list[int] = []
    for race in sorted(chosen, key=_race_order):
        people = _matching(
            race,
            driver_id=subject_driver,
            vehicle_id=subject_vehicle,
            lane=scope.lane,
        )
        if not people:
            continue
        race_times: list[int] = []
        best_position: int | None = None
        for person in people:
            times = () if person.disqualified else valid_lap_times(race.laps, person.participant_id)
            race_times.extend(times)
            lap_total += len(times)
            if _counts_result(race, person):
                if person.position == 1:
                    wins += 1
                if person.position is not None and 1 <= person.position <= 3:
                    podiums += 1
                if person.position is not None and (
                    best_position is None or person.position < best_position
                ):
                    best_position = person.position
        pooled.extend(race_times)
        best = min(race_times) if race_times else None
        history.append(
            CareerRace(
                race_id=race.race_id,
                name=race.name,
                when=race.finished_at,
                mode=race.mode,
                status=race.status,
                position=best_position,
                laps=len(race_times),
                best_lap_ns=best,
            )
        )
        if comparable and best is not None and race.finished_at is not None:
            trend.append((race.finished_at, best))
    return CareerSummary(
        races=len(history),
        wins=wins,
        podiums=podiums,
        laps=lap_total,
        best_lap_ns=min(pooled) if comparable and pooled else None,
        average_lap_ns=average_ns(pooled) if comparable else None,
        stdev_ns=stdev_ns(pooled) if comparable else None,
        distance_mm=None,
        history=tuple(history),
        trend=tuple(trend) if comparable else (),
    )


def track_records(races: Sequence[HistoryRace], scope: TimeScope) -> tuple[RecordLine, ...]:
    """Fastest valid laps in one layout bucket. Other layouts and other tracks are left out."""
    if not _times_are_comparable(scope):
        return ()
    lines: list[RecordLine] = []
    for race in races:
        if not _in_scope(race, scope):
            continue
        people = {
            person.participant_id: person
            for person in _matching(
                race,
                driver_id=scope.driver_id,
                vehicle_id=scope.vehicle_id,
                lane=scope.lane,
            )
            if not person.disqualified
        }
        for lap in race.laps:
            person = people.get(lap.participant_id)
            if person is None or lap.lap_time_ns <= 0:
                continue
            if scope.lane is not None and (lap.lane or person.lane) != scope.lane:
                continue
            lines.append(
                RecordLine(
                    time_ns=lap.lap_time_ns,
                    driver_id=person.driver_id,
                    driver_label=person.driver_label,
                    vehicle_id=person.vehicle_id,
                    vehicle_label=person.vehicle_label,
                    lane=lap.lane if lap.lane is not None else person.lane,
                    when=race.finished_at,
                    race_id=race.race_id,
                    race_name=race.name,
                    lap_number=lap.lap_number,
                )
            )
    lines.sort(key=_record_order)
    return tuple(lines)


def _times_are_comparable(scope: TimeScope) -> bool:
    if scope.track_id is None:
        return False
    return scope.unassigned or scope.layout_id is not None


def _in_scope(race: HistoryRace, scope: TimeScope) -> bool:
    if scope.track_id is not None and race.track_id != scope.track_id:
        return False
    if scope.unassigned and race.layout_id is not None:
        return False
    if scope.layout_id is not None and race.layout_id != scope.layout_id:
        return False
    if scope.since is None and scope.until is None:
        return True
    if race.finished_at is None:
        return False
    day = race.finished_at.date()
    if scope.since is not None and day < scope.since:
        return False
    return scope.until is None or day <= scope.until


def _matching(
    race: HistoryRace,
    *,
    driver_id: int | None,
    vehicle_id: int | None,
    lane: int | None,
) -> tuple[HistoryParticipant, ...]:
    found = []
    for person in race.participants:
        if driver_id is not None and person.driver_id != driver_id:
            continue
        if vehicle_id is not None and person.vehicle_id != vehicle_id:
            continue
        if lane is not None and person.lane != lane:
            continue
        found.append(person)
    return tuple(found)


def _counts_result(race: HistoryRace, person: HistoryParticipant) -> bool:
    return race.status is RaceStatus.FINISHED and not person.disqualified


def _leader(race: HistoryRace) -> HistoryParticipant | None:
    leaders = [
        person for person in race.participants if person.position == 1 and not person.disqualified
    ]
    if not leaders:
        return None
    return min(leaders, key=lambda person: person.participant_id)


def _comparison(race: HistoryRace, person: HistoryParticipant, times: Sequence[int]) -> int | None:
    if person.disqualified or not times:
        return None
    if race.mode is RaceMode.TIME_TRIAL:
        return min(times)
    if not person.finished or person.total_time_ns is None:
        return None
    return person.total_time_ns


def _gap(
    race: HistoryRace,
    person: HistoryParticipant,
    times: Sequence[int],
    leader: HistoryParticipant | None,
    leader_times: Sequence[int],
    leader_score: int | None,
    own_score: int | None,
) -> int | None:
    if leader is None or leader_score is None or own_score is None or person.disqualified:
        return None
    if race.mode is RaceMode.TIME_TRIAL:
        return own_score - leader_score
    if len(times) != len(leader_times):
        return None
    return own_score - leader_score


def _ordered(people: Sequence[HistoryParticipant]) -> tuple[HistoryParticipant, ...]:
    return tuple(
        sorted(
            people,
            key=lambda person: (person.position is None, person.position or 0, person.lane or 0),
        )
    )


def _race_order(race: HistoryRace) -> tuple[tuple[int, float], int]:
    if race.finished_at is None:
        return ((1, 0.0), race.race_id)
    return ((0, race.finished_at.timestamp()), race.race_id)


def _record_order(line: RecordLine) -> tuple[int, tuple[int, float], int, int]:
    stamp = (1, 0.0) if line.when is None else (0, line.when.timestamp())
    return (line.time_ns, stamp, line.race_id, line.lap_number)


def _median(times: Sequence[int]) -> float:
    ordered = sorted(times)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _half_up(total: int, count: int) -> int:
    return (total + count // 2) // count
