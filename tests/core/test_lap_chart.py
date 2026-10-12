"""The lap chart is a view. Stored lap numbers and the race report stay untouched."""

from __future__ import annotations

from slot_racing.core.domain.race import RaceMode, RaceStatus
from slot_racing.core.lap_chart import LapChartPoint, build_lap_chart
from slot_racing.core.statistics import (
    HistoryHeat,
    HistoryLap,
    HistoryParticipant,
    HistoryRace,
    race_report,
)

_S = 1_000_000_000


def test_one_run_uses_the_stored_lap_as_the_lap_inside_that_run() -> None:
    race = _race((_person(1),), _times(1, 1, (4 * _S, 5 * _S, 6 * _S)))
    stored = tuple(lap.lap_number for lap in race.laps)
    chart = build_lap_chart(race)
    assert chart.aligned is True
    assert [(run.run_number, run.x_start, run.x_end) for run in chart.runs] == [(1, 1, 3)]
    points = chart.series[0].points
    assert [(point.absolute_lap, point.lap_in_run, point.x) for point in points] == [
        (1, 1, 1),
        (2, 2, 2),
        (3, 3, 3),
    ]
    assert stored == tuple(lap.lap_number for lap in race.laps)


def test_a_later_lap_is_not_drawn_on_another_drivers_earlier_lap() -> None:
    race = _race(
        (_person(1, position=1), _person(2, position=2, lane=2)),
        _times(1, 1, (5 * _S, 5 * _S, 5 * _S)) + _times(2, 2, (6 * _S, 6 * _S), start=2),
    )
    chart = build_lap_chart(race)
    first = {point.absolute_lap: point.x for point in chart.series[0].points}
    second = {point.absolute_lap: point.x for point in chart.series[1].points}
    assert first[1] == 1
    assert second[2] == 2
    assert first[1] != second[2]
    assert chart.series[1].points[0].lap_in_run == 2


def test_two_heats_of_six_show_lap_seven_as_run_two_lap_one() -> None:
    race = _two_by_six()
    before = _figures(race)
    stored = tuple((lap.participant_id, lap.lap_number, lap.lap_time_ns) for lap in race.laps)
    chart = build_lap_chart(race)
    assert _figures(race) == before
    again = tuple((lap.participant_id, lap.lap_number, lap.lap_time_ns) for lap in race.laps)
    assert stored == again
    assert chart.aligned is True
    point = _point(chart.series[0].points, 7)
    assert point.run_number == 2
    assert point.lap_in_run == 1
    assert point.absolute_lap == 7
    assert point.lane == 1
    assert point.x == 7
    local_ones = [item.x for item in chart.series[0].points if item.lap_in_run == 1]
    assert local_ones == [1, 7]
    assert len({item.x for item in chart.series[0].points}) == 12
    report = race_report(race)
    assert report.series[0].points[6].lap_number == 7
    assert report.lines[0].best_lap_ns == 5 * _S
    assert report.lines[0].total_time_ns == 60_123_000_000
    assert report.lines[0].average_lap_ns == 5_010_250_000
    assert report.series[0].points[-1].time_ns == 5 * _S
    assert report.lines[0].position == 1
    assert report.lines[1].position == 2
    assert report.lines[1].gap_ns == 72 * _S - 60_123_000_000


def test_unequal_heats_keep_the_width_of_the_longer_one() -> None:
    race = _race(
        (_person(1, lane=2),),
        _times(1, 1, (5 * _S,) * 6) + _times(1, 2, (5 * _S,) * 10, start=7),
        (_heat(1, (1, 1)), _heat(2, (1, 2))),
    )
    chart = build_lap_chart(race)
    assert [(run.run_number, run.x_start, run.x_end) for run in chart.runs] == [
        (1, 1, 6),
        (2, 7, 16),
    ]
    assert _point(chart.series[0].points, 7).lap_in_run == 1
    assert _point(chart.series[0].points, 16).x == 16
    assert _point(chart.series[0].points, 16).lap_in_run == 10


def test_three_heats_restart_the_local_lap_each_time() -> None:
    laps = _times(1, 1, (5 * _S,) * 6)
    laps += _times(1, 2, (5 * _S,) * 6, start=7)
    laps += _times(1, 1, (5 * _S,) * 6, start=13)
    race = _race(
        (_person(1),),
        laps,
        (_heat(1, (1, 1)), _heat(2, (1, 2)), _heat(3, (1, 1))),
    )
    chart = build_lap_chart(race)
    assert [run.run_number for run in chart.runs] == [1, 2, 3]
    assert [
        (item.absolute_lap, item.run_number, item.lap_in_run, item.x)
        for item in (
            _point(chart.series[0].points, 1),
            _point(chart.series[0].points, 7),
            _point(chart.series[0].points, 13),
        )
    ] == [(1, 1, 1, 1), (7, 2, 1, 7), (13, 3, 1, 13)]


def test_drivers_on_the_same_heats_share_one_axis_and_keep_their_lanes() -> None:
    race = _two_by_six()
    chart = build_lap_chart(race)
    matthias = chart.series[0]
    anna = chart.series[1]
    assert [point.x for point in matthias.points] == [point.x for point in anna.points]
    assert {point.lane for point in matthias.points if point.run_number == 1} == {2}
    assert {point.lane for point in anna.points if point.run_number == 1} == {1}
    assert {point.lane for point in matthias.points if point.run_number == 2} == {1}


def test_a_shorter_run_does_not_stretch_and_a_skipped_heat_stays_later() -> None:
    anna = _times(1, 1, (5 * _S,) * 6) + _times(1, 2, (5 * _S,) * 4, start=7)
    ben = _times(2, 1, (6 * _S,) * 4)
    race = _race(
        (_person(1, position=1, lane=2), _person(2, position=2, lane=1)),
        anna + ben,
        (_heat(1, (1, 1)), _heat(2, (1, 2), (2, 1))),
    )
    chart = build_lap_chart(race)
    assert [(run.run_number, run.x_end - run.x_start + 1) for run in chart.runs] == [(1, 6), (2, 4)]
    ben_points = chart.series[1].points
    opened = ben_points[0]
    assert (opened.absolute_lap, opened.run_number, opened.lap_in_run, opened.x) == (1, 2, 1, 7)
    assert ben_points[0].x != chart.series[0].points[0].x


def test_a_return_to_the_same_lane_without_a_separating_group_is_not_guessed() -> None:
    race = _race(
        (_person(1),),
        _times(1, 1, (5 * _S,) * 8),
        (_heat(1, (1, 1)), _heat(2, (1, 2)), _heat(3, (1, 1))),
    )
    chart = build_lap_chart(race)
    assert chart.aligned is False
    assert chart.runs == ()
    assert [point.run_number for point in chart.series[0].points] == [None] * 8
    assert [point.x for point in chart.series[0].points] == list(range(1, 9))
    assert [point.absolute_lap for point in chart.series[0].points] == list(range(1, 9))


def test_two_heats_on_the_same_lane_are_not_split() -> None:
    race = _race(
        (_person(1),),
        _times(1, 1, (5 * _S,) * 4),
        (_heat(1, (1, 1)), _heat(2, (1, 1))),
    )
    chart = build_lap_chart(race)
    assert chart.aligned is False
    assert chart.runs == ()


def test_a_driver_without_a_seat_unaligns_every_series() -> None:
    race = _race(
        (_person(1, position=1), _person(2, position=2, lane=2)),
        _times(1, 1, (5 * _S,) * 6)
        + _times(1, 2, (5 * _S,) * 6, start=7)
        + _times(2, 2, (6 * _S,)),
        (_heat(1, (1, 1)), _heat(2, (1, 2))),
    )
    chart = build_lap_chart(race)
    assert chart.aligned is False
    assert chart.runs == ()
    assert all(point.run_number is None for series in chart.series for point in series.points)
    assert _point(chart.series[0].points, 7).x == 7
    assert _point(chart.series[0].points, 7).lap_in_run is None


def test_a_missing_lane_is_not_treated_as_a_heat() -> None:
    race = _race(
        (_person(1),),
        (HistoryLap(1, 1, 5 * _S, 1), HistoryLap(1, 2, 5 * _S, None)),
        (_heat(1, (1, 1)), _heat(2, (1, 2))),
    )
    chart = build_lap_chart(race)
    assert chart.aligned is False
    assert chart.series[0].points[1].lane is None
    assert chart.series[0].points[1].run_number is None


def test_a_heat_with_no_stored_laps_does_not_invent_a_band() -> None:
    race = _race(
        (_person(1),),
        _times(1, 1, (5 * _S,) * 3),
        (_heat(1, (1, 1)), _heat(2, (1, 2))),
    )
    chart = build_lap_chart(race)
    assert chart.aligned is True
    assert [run.run_number for run in chart.runs] == [1]
    assert chart.runs[0].x_end == 3


def test_a_skipped_sequence_keeps_its_stored_number() -> None:
    race = _race(
        (_person(1),),
        _times(1, 1, (5 * _S,) * 3) + _times(1, 2, (5 * _S,) * 2, start=4),
        (_heat(1, (1, 1)), _heat(3, (1, 2))),
    )
    chart = build_lap_chart(race)
    assert [run.run_number for run in chart.runs] == [1, 3]
    assert _point(chart.series[0].points, 4).run_number == 3
    assert _point(chart.series[0].points, 4).lap_in_run == 1
    assert 2 not in {run.run_number for run in chart.runs}


def test_a_disqualified_driver_is_left_out_and_does_not_renumber_the_heats() -> None:
    race = _race(
        (_person(1, position=1), _person(2, position=None, lane=2, disqualified=True)),
        _times(1, 1, (5 * _S,) * 2) + _times(2, 2, (9 * _S,) * 4),
        (_heat(1, (1, 1), (2, 2)),),
    )
    before = race_report(race)
    chart = build_lap_chart(race)
    assert race_report(race) == before
    assert [series.participant_id for series in chart.series] == [1]
    assert [run.run_number for run in chart.runs] == [1]
    assert before.lines[1].disqualified is True
    assert before.lines[1].best_lap_ns is None


def test_an_outlier_stays_in_the_average_and_keeps_its_absolute_lap() -> None:
    times = (5 * _S, 5 * _S, 5 * _S, 20 * _S)
    race = _race(
        (_person(1, total=35 * _S),),
        _times(1, 1, times),
        (_heat(1, (1, 1)),),
    )
    before = race_report(race)
    chart = build_lap_chart(race)
    after = race_report(race)
    assert after == before
    assert before.lines[0].best_lap_ns == 5 * _S
    assert before.lines[0].average_lap_ns == 8_750_000_000
    assert before.lines[0].outlier_laps == (4,)
    assert before.series[0].points[-1].lap_number == 4
    point = chart.series[0].points[-1]
    assert point.absolute_lap == 4
    assert point.outlier is True
    assert point.lap_time_ns == 20 * _S


def _two_by_six() -> HistoryRace:
    matthias = _times(1, 2, (5 * _S,) * 6) + _times(1, 1, (5_123_000_000,) + (5 * _S,) * 5, start=7)
    anna = _times(2, 1, (6 * _S,) * 6) + _times(2, 2, (6 * _S,) * 6, start=7)
    return _race(
        (
            _person(1, "Matthias", "Porsche", position=1, lane=1, total=60_123_000_000),
            _person(2, "Anna", "BMW", position=2, lane=2, total=72 * _S),
        ),
        matthias + anna,
        (_heat(1, (1, 2), (2, 1)), _heat(2, (1, 1), (2, 2))),
    )


def _figures(race: HistoryRace) -> tuple[object, ...]:
    report = race_report(race)
    rows = []
    for line in report.lines:
        series = next(
            (item for item in report.series if item.participant_id == line.participant_id),
            None,
        )
        last = None if series is None or not series.points else series.points[-1].time_ns
        numbers = () if series is None else tuple(point.lap_number for point in series.points)
        rows.append(
            (
                line.position,
                line.total_time_ns,
                line.average_lap_ns,
                line.best_lap_ns,
                last,
                line.outlier_laps,
                numbers,
            )
        )
    return (report.official, tuple(rows))


def _point(points: tuple[LapChartPoint, ...], absolute: int) -> LapChartPoint:
    return next(point for point in points if point.absolute_lap == absolute)


def _times(
    participant_id: int, lane: int, times: tuple[int, ...], *, start: int = 1
) -> tuple[HistoryLap, ...]:
    return tuple(
        HistoryLap(participant_id, start + offset, time_ns, lane)
        for offset, time_ns in enumerate(times)
    )


def _heat(sequence: int, *seats: tuple[int, int]) -> HistoryHeat:
    return HistoryHeat(sequence, seats)


def _person(
    participant_id: int,
    driver: str | None = None,
    vehicle: str | None = None,
    *,
    position: int | None = 1,
    lane: int = 1,
    total: int | None = 8 * _S,
    disqualified: bool = False,
) -> HistoryParticipant:
    return HistoryParticipant(
        participant_id=participant_id,
        driver_id=participant_id,
        driver_label=driver or f"Fahrer {participant_id}",
        vehicle_id=participant_id,
        vehicle_label=vehicle or f"Wagen {participant_id}",
        lane=lane,
        position=position,
        finished=not disqualified,
        disqualified=disqualified,
        total_time_ns=total,
    )


def _race(
    people: tuple[HistoryParticipant, ...],
    laps: tuple[HistoryLap, ...],
    heats: tuple[HistoryHeat, ...] = (),
) -> HistoryRace:
    return HistoryRace(
        race_id=1,
        name="Finale",
        track_id=1,
        track_name="Ring",
        layout_id=None,
        status=RaceStatus.FINISHED,
        mode=RaceMode.LAPS,
        finished_at=None,
        participants=people,
        laps=laps,
        heats=heats,
    )
