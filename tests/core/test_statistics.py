"""Figures come only from stored laps. Missing comparisons stay empty."""

from __future__ import annotations

from datetime import datetime

from slot_racing.core.domain.race import RaceMode, RaceStatus
from slot_racing.core.statistics import (
    HistoryLap,
    HistoryParticipant,
    HistoryRace,
    TimeScope,
    average_ns,
    career_summary,
    outlier_lap_numbers,
    race_report,
    stdev_ns,
    track_records,
    valid_lap_times,
)

_WHEN = datetime(2026, 5, 1, 12, 0, 0)


def test_only_positive_stored_laps_count() -> None:
    laps = (
        HistoryLap(1, 1, 0, 1),
        HistoryLap(1, 2, -5, 1),
        HistoryLap(1, 3, 8_000, 1),
        HistoryLap(2, 1, 9_000, 2),
    )
    assert valid_lap_times(laps, 1) == (8_000,)
    assert valid_lap_times(laps, 2) == (9_000,)
    assert valid_lap_times(laps, 3) == ()


def test_average_rounds_half_up_and_stdev_needs_two_laps() -> None:
    assert average_ns(()) is None
    assert average_ns((2, 3)) == 3
    assert average_ns((2, 2, 3)) == 2
    assert stdev_ns(()) is None
    assert stdev_ns((8_000,)) is None
    assert stdev_ns((10, 20, 30)) == 10


def test_outliers_are_marked_and_still_counted() -> None:
    laps = tuple(HistoryLap(1, number, time, 1) for number, time in enumerate((10, 10, 10, 100), 1))
    assert outlier_lap_numbers(laps, 1) == (4,)
    assert average_ns(valid_lap_times(laps, 1)) == 33
    short = laps[:3]
    assert outlier_lap_numbers(short, 1) == ()


def test_a_lap_race_gap_needs_the_same_number_of_valid_laps() -> None:
    race = _race(
        RaceMode.LAPS,
        (
            _person(1, position=1, lane=1, total=20_000),
            _person(2, driver=2, position=2, lane=2, total=25_000),
            _person(3, driver=3, position=3, lane=3, total=9_000, finished=False),
        ),
        (
            HistoryLap(1, 1, 10_000, 1),
            HistoryLap(1, 2, 10_000, 1),
            HistoryLap(2, 1, 12_000, 2),
            HistoryLap(2, 2, 13_000, 2),
            HistoryLap(3, 1, 9_000, 3),
        ),
    )
    report = race_report(race)
    assert report.official is True
    assert [(line.position, line.lap_count, line.gap_ns) for line in report.lines] == [
        (1, 2, 0),
        (2, 2, 5_000),
        (3, 1, None),
    ]
    assert report.lines[0].best_lap_ns == 10_000
    assert report.lines[0].stdev_ns == 0
    assert report.series[0].points[0].lap_number == 1


def test_a_time_trial_gap_uses_the_best_valid_lap() -> None:
    race = _race(
        RaceMode.TIME_TRIAL,
        (
            _person(1, position=1, lane=1, total=30_000),
            _person(2, driver=2, position=2, lane=2, total=None, finished=False),
        ),
        (
            HistoryLap(1, 1, 12_000, 1),
            HistoryLap(1, 2, 9_000, 1),
            HistoryLap(2, 1, 11_000, 2),
        ),
    )
    report = race_report(race)
    assert [line.gap_ns for line in report.lines] == [0, 2_000]
    assert [line.best_lap_ns for line in report.lines] == [9_000, 11_000]


def test_an_aborted_race_is_reported_without_a_win() -> None:
    race = _race(
        RaceMode.LAPS,
        (_person(1, position=1, total=8_000),),
        (HistoryLap(1, 1, 8_000, 1),),
        status=RaceStatus.ABORTED,
    )
    assert race_report(race).official is False
    summary = career_summary((race,), _scope(), driver_id=1)
    assert (summary.races, summary.wins, summary.podiums, summary.laps) == (1, 0, 0, 1)


def test_a_disqualification_is_not_promoted_and_does_not_set_the_record() -> None:
    race = _race(
        RaceMode.LAPS,
        (
            _person(1, position=1, disqualified=True, total=5_000),
            _person(2, driver=2, position=2, lane=2, total=12_000),
        ),
        (
            HistoryLap(1, 1, 5_000, 1),
            HistoryLap(2, 1, 12_000, 2),
        ),
    )
    report = race_report(race)
    assert report.lines[0].disqualified is True
    assert report.lines[0].best_lap_ns is None
    assert report.lines[0].gap_ns is None
    assert report.lines[1].gap_ns is None
    assert report.lines[1].position == 2
    summary = career_summary((race,), _scope(), driver_id=1)
    assert (summary.wins, summary.podiums, summary.laps) == (0, 0, 0)
    second = career_summary((race,), _scope(), driver_id=2)
    assert (second.wins, second.podiums) == (0, 1)
    assert [line.driver_id for line in track_records((race,), _scope())] == [2]


def test_a_shared_first_place_counts_for_both_drivers() -> None:
    race = _race(
        RaceMode.TIME_TRIAL,
        (
            _person(1, position=1, total=8_000),
            _person(2, driver=2, position=1, lane=2, total=8_000),
        ),
        (HistoryLap(1, 1, 8_000, 1), HistoryLap(2, 1, 8_000, 2)),
    )
    assert race_report(race).lines[0].gap_ns == 0
    assert race_report(race).lines[1].gap_ns == 0
    first = career_summary((race,), _scope(), driver_id=1)
    second = career_summary((race,), _scope(), driver_id=2)
    assert first.wins == 1
    assert second.wins == 1


def test_lap_times_from_two_tracks_or_layouts_do_not_form_one_best() -> None:
    home = _race(RaceMode.LAPS, (_person(1, total=8_000),), (HistoryLap(1, 1, 8_000, 1),), track=1)
    away = _race(
        RaceMode.LAPS,
        (_person(1, total=4_000),),
        (HistoryLap(1, 1, 4_000, 1),),
        track=2,
        race_id=2,
    )
    other_layout = _race(
        RaceMode.LAPS,
        (_person(1, total=3_000),),
        (HistoryLap(1, 1, 3_000, 1),),
        track=1,
        layout=9,
        race_id=3,
    )
    races = (home, away, other_layout)
    mixed = career_summary(races, TimeScope(), driver_id=1)
    assert mixed.races == 3
    assert mixed.laps == 3
    assert mixed.best_lap_ns is None
    assert mixed.distance_mm is None
    assert mixed.trend == ()
    one_track = career_summary(races, TimeScope(track_id=1), driver_id=1)
    assert one_track.best_lap_ns is None
    assigned = career_summary(races, _scope(track=1, layout=4), driver_id=1)
    assert assigned.best_lap_ns == 8_000
    assert assigned.laps == 1
    unassigned = career_summary(races, TimeScope(track_id=1, unassigned=True), driver_id=1)
    assert unassigned.best_lap_ns is None
    assert unassigned.races == 0


def test_records_stay_inside_one_layout_and_keep_the_earlier_equal_time() -> None:
    early = _race(
        RaceMode.LAPS,
        (_person(1, total=8_000),),
        (HistoryLap(1, 1, 8_000, 1),),
        when=datetime(2026, 5, 1, 10, 0, 0),
    )
    later = _race(
        RaceMode.TIME_TRIAL,
        (_person(3, driver=3, position=1, total=8_000),),
        (HistoryLap(3, 1, 8_000, 1),),
        race_id=2,
        when=datetime(2026, 5, 2, 10, 0, 0),
    )
    other = _race(
        RaceMode.LAPS,
        (_person(1, total=1_000),),
        (HistoryLap(1, 1, 1_000, 1),),
        layout=8,
        race_id=3,
    )
    lines = track_records((later, early, other), _scope())
    assert [(line.race_id, line.time_ns, line.driver_id) for line in lines] == [
        (1, 8_000, 1),
        (2, 8_000, 3),
    ]
    assert track_records((early,), TimeScope(track_id=1)) == ()
    assert track_records((), _scope()) == ()


def test_a_date_filter_ignores_a_race_without_a_finish_time() -> None:
    missing = _race(
        RaceMode.LAPS,
        (_person(1, total=8_000),),
        (HistoryLap(1, 1, 8_000, 1),),
        when=None,
    )
    kept = career_summary(
        (missing,), TimeScope(track_id=1, layout_id=4, since=_WHEN.date()), driver_id=1
    )
    assert kept.races == 0
    dated = career_summary((missing,), _scope(), driver_id=1)
    assert dated.races == 1
    assert dated.history[0].best_lap_ns == 8_000


def test_an_empty_history_has_no_invented_figures() -> None:
    summary = career_summary((), _scope(), driver_id=1)
    assert summary.races == 0
    assert summary.best_lap_ns is None
    assert summary.average_lap_ns is None
    assert summary.stdev_ns is None
    assert summary.distance_mm is None
    assert summary.history == ()
    empty = _race(RaceMode.LAPS, (), ())
    assert race_report(empty).lines == ()


def _scope(*, track: int = 1, layout: int = 4) -> TimeScope:
    return TimeScope(track_id=track, layout_id=layout)


def _person(
    participant_id: int,
    *,
    driver: int | None = None,
    position: int | None = 1,
    lane: int = 1,
    total: int | None = 8_000,
    finished: bool = True,
    disqualified: bool = False,
) -> HistoryParticipant:
    driver_id = participant_id if driver is None else driver
    return HistoryParticipant(
        participant_id=participant_id,
        driver_id=driver_id,
        driver_label=f"Fahrer {driver_id}",
        vehicle_id=driver_id,
        vehicle_label=f"Wagen {driver_id}",
        lane=lane,
        position=position,
        finished=finished,
        disqualified=disqualified,
        total_time_ns=total,
    )


def _race(
    mode: RaceMode,
    people: tuple[HistoryParticipant, ...],
    laps: tuple[HistoryLap, ...],
    *,
    status: RaceStatus = RaceStatus.FINISHED,
    track: int = 1,
    layout: int | None = 4,
    race_id: int = 1,
    when: datetime | None = _WHEN,
) -> HistoryRace:
    return HistoryRace(
        race_id=race_id,
        name=f"Rennen {race_id}",
        track_id=track,
        track_name="Ring",
        layout_id=layout,
        status=status,
        mode=mode,
        finished_at=when,
        participants=people,
        laps=laps,
    )
