"""Presentation of one race's lap times. Nothing here writes or renumbers stored laps.

The x position is shared across drivers only when every driver's laps match the stored
heats uniquely. A heat seats a driver on a lane. Consecutive laps on that lane belong to
that heat. The laps do not store a heat id. Two heats on the same lane, with no different
lane between the stored laps, cannot be split without guessing a length. The chart then
keeps each driver's stored lap number as its own x and draws no heat bands.

A race without heats is one run. The local lap is the stored lap number, so two drivers
share a column only for the same race lap. The stored ``lap_number`` is never rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from slot_racing.core.statistics import (
    HistoryHeat,
    HistoryLap,
    HistoryParticipant,
    HistoryRace,
    outlier_lap_numbers,
)


@dataclass(frozen=True, slots=True)
class LapChartPoint:
    """One dot. ``absolute_lap`` is the stored lap number and is never rewritten."""

    participant_id: int
    absolute_lap: int
    x: int
    run_number: int | None
    lap_in_run: int | None
    lap_time_ns: int
    lane: int | None
    outlier: bool


@dataclass(frozen=True, slots=True)
class LapChartRun:
    """A driven heat on the shared axis. ``x_start`` and ``x_end`` are inclusive."""

    run_number: int
    x_start: int
    x_end: int


@dataclass(frozen=True, slots=True)
class LapChartSeries:
    participant_id: int
    driver_label: str
    vehicle_label: str
    points: tuple[LapChartPoint, ...]


@dataclass(frozen=True, slots=True)
class LapChart:
    """``aligned`` is false when heat boundaries could not be recovered without guessing."""

    aligned: bool
    runs: tuple[LapChartRun, ...]
    series: tuple[LapChartSeries, ...]


@dataclass(frozen=True, slots=True)
class _Seat:
    sequence: int
    lane: int


@dataclass(frozen=True, slots=True)
class _Placed:
    lap: HistoryLap
    run_number: int
    lap_in_run: int


def build_lap_chart(race: HistoryRace) -> LapChart:
    """Chart facts for one ended race. The race object and its laps are not modified."""
    people = _ordered(race.participants)
    seats = _seats(race.heats)
    placed: dict[int, tuple[_Placed, ...]] = {}
    aligned = True
    for person in people:
        if person.disqualified:
            continue
        laps = tuple(
            lap
            for lap in race.laps
            if lap.participant_id == person.participant_id and lap.lap_time_ns > 0
        )
        if not laps:
            continue
        assignment = _place(
            laps,
            seats.get(person.participant_id, ()),
            heats_exist=bool(race.heats),
        )
        if assignment is None:
            aligned = False
            break
        placed[person.participant_id] = assignment
    if not aligned:
        placed = {}
    runs = _runs(placed) if aligned else ()
    offsets = _offsets(runs)
    marked = {
        person.participant_id: set(outlier_lap_numbers(race.laps, person.participant_id))
        for person in people
    }
    series: list[LapChartSeries] = []
    for person in people:
        if person.disqualified:
            continue
        points = _points(
            person,
            race,
            placed,
            offsets,
            aligned,
            marked.get(person.participant_id, set()),
        )
        if not points:
            continue
        series.append(
            LapChartSeries(
                participant_id=person.participant_id,
                driver_label=person.driver_label,
                vehicle_label=person.vehicle_label,
                points=points,
            )
        )
    return LapChart(aligned=aligned, runs=runs, series=tuple(series))


def _ordered(people: Sequence[HistoryParticipant]) -> tuple[HistoryParticipant, ...]:
    return tuple(
        sorted(
            people,
            key=lambda person: (person.position is None, person.position or 0, person.lane or 0),
        )
    )


def _seats(heats: Sequence[HistoryHeat]) -> dict[int, tuple[_Seat, ...]]:
    found: dict[int, list[_Seat]] = {}
    for heat in sorted(heats, key=lambda item: item.sequence):
        for participant_id, lane in heat.seats:
            found.setdefault(participant_id, []).append(_Seat(heat.sequence, lane))
    return {participant_id: tuple(seats) for participant_id, seats in found.items()}


def _place(
    laps: Sequence[HistoryLap], seats: tuple[_Seat, ...], *, heats_exist: bool
) -> tuple[_Placed, ...] | None:
    ordered = tuple(sorted(laps, key=lambda lap: lap.lap_number))
    if not seats:
        if heats_exist:
            return None
        # One race, no heat plan: the stored lap number is the lap inside that run.
        # Compacting gaps would put a later lap on top of another driver's earlier lap.
        return tuple(_Placed(lap, 1, lap.lap_number) for lap in ordered)
    if len(seats) == 1:
        sequence = seats[0].sequence
        return tuple(_Placed(lap, sequence, index) for index, lap in enumerate(ordered, start=1))
    if any(lap.lane is None for lap in ordered):
        return None
    groups: list[tuple[int, list[HistoryLap]]] = []
    for lap in ordered:
        lane = lap.lane
        if lane is None:
            return None
        if groups and groups[-1][0] == lane:
            groups[-1][1].append(lap)
        else:
            groups.append((lane, [lap]))
    mapping = _match_groups(tuple(lane for lane, _group in groups), seats)
    if mapping is None:
        return None
    placed: list[_Placed] = []
    for (_lane, group), seat_index in zip(groups, mapping, strict=True):
        sequence = seats[seat_index].sequence
        placed.extend(_Placed(lap, sequence, index) for index, lap in enumerate(group, start=1))
    return tuple(placed)


def _match_groups(lanes: tuple[int, ...], seats: tuple[_Seat, ...]) -> tuple[int, ...] | None:
    """Seat index of each lane group, when exactly one ordered matching exists.

    Empty seats may be skipped. A return to a lane that was already used is only accepted
    when a different lane was stored between the two groups. One uninterrupted group can
    belong to more than one visit, and that split is not stored.
    """
    found: list[tuple[int, ...]] = []

    def walk(group_index: int, seat_index: int, chosen: list[int]) -> None:
        if len(found) > 1:
            return
        if group_index == len(lanes):
            found.append(tuple(chosen))
            return
        lane = lanes[group_index]
        for index in range(seat_index, len(seats)):
            if seats[index].lane != lane:
                continue
            chosen.append(index)
            walk(group_index + 1, index + 1, chosen)
            chosen.pop()
            if len(found) > 1:
                return

    walk(0, 0, [])
    if len(found) != 1:
        return None
    return found[0]


def _runs(placed: dict[int, tuple[_Placed, ...]]) -> tuple[LapChartRun, ...]:
    widths: dict[int, int] = {}
    for assignment in placed.values():
        for item in assignment:
            widths[item.run_number] = max(widths.get(item.run_number, 0), item.lap_in_run)
    runs: list[LapChartRun] = []
    cursor = 1
    for sequence in sorted(widths):
        width = widths[sequence]
        runs.append(LapChartRun(sequence, cursor, cursor + width - 1))
        cursor += width
    return tuple(runs)


def _offsets(runs: Sequence[LapChartRun]) -> dict[int, int]:
    return {run.run_number: run.x_start - 1 for run in runs}


def _points(
    person: HistoryParticipant,
    race: HistoryRace,
    placed: dict[int, tuple[_Placed, ...]],
    offsets: dict[int, int],
    aligned: bool,
    outliers: set[int],
) -> tuple[LapChartPoint, ...]:
    if aligned:
        assignment = placed.get(person.participant_id, ())
        return tuple(
            LapChartPoint(
                participant_id=person.participant_id,
                absolute_lap=item.lap.lap_number,
                x=offsets[item.run_number] + item.lap_in_run,
                run_number=item.run_number,
                lap_in_run=item.lap_in_run,
                lap_time_ns=item.lap.lap_time_ns,
                lane=item.lap.lane,
                outlier=item.lap.lap_number in outliers,
            )
            for item in assignment
        )
    ordered = sorted(
        (
            lap
            for lap in race.laps
            if lap.participant_id == person.participant_id and lap.lap_time_ns > 0
        ),
        key=lambda lap: lap.lap_number,
    )
    return tuple(
        LapChartPoint(
            participant_id=person.participant_id,
            absolute_lap=lap.lap_number,
            x=lap.lap_number,
            run_number=None,
            lap_in_run=None,
            lap_time_ns=lap.lap_time_ns,
            lane=lap.lane,
            outlier=lap.lap_number in outliers,
        )
        for lap in ordered
    )
