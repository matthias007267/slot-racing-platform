"""A free session is a separate mode. Its laps never become a result or a statistic."""

from datetime import datetime

from slot_racing.core.championship import PointsRule, score_championship
from slot_racing.core.domain import RaceMode, RaceStatus
from slot_racing.core.statistics import (
    HistoryLap,
    HistoryParticipant,
    HistoryRace,
    TimeScope,
    career_summary,
    race_report,
    track_records,
)

_WHEN = datetime(2026, 6, 1, 12, 0)


def test_free_laps_stay_out_of_statistics_even_when_they_are_not_excluded() -> None:
    free = _race(RaceMode.FREE, 4_000_000_000, race_id=1)
    official = _race(RaceMode.LAPS, 10_000_000_000, race_id=2)
    assert free.laps[0].excluded is False
    scope = TimeScope(track_id=1, layout_id=4)

    summary = career_summary((free, official), scope, driver_id=1)
    assert summary.races == 1
    assert summary.wins == 1
    assert summary.laps == 1
    assert summary.best_lap_ns == 10_000_000_000
    assert [item.race_id for item in summary.history] == [2]

    records = track_records((free, official), scope)
    assert [line.time_ns for line in records] == [10_000_000_000]
    assert records[0].race_id == 2

    report = race_report(free)
    assert report.official is False
    assert report.lines == ()
    assert report.series == ()

    standing = score_championship(
        (free, official),
        PointsRule(((1, 25),), fastest_lap_bonus=3),
    )
    assert len(standing) == 1
    assert standing[0].points == 28
    assert standing[0].counted_races == 1
    assert standing[0].wins == 1
    assert standing[0].bonus_points == 3


def _race(mode: RaceMode, time_ns: int, *, race_id: int) -> HistoryRace:
    person = HistoryParticipant(
        participant_id=race_id,
        driver_id=1,
        driver_label="Anna",
        vehicle_id=1,
        vehicle_label="Porsche",
        lane=1,
        position=1,
        finished=True,
        disqualified=False,
        total_time_ns=time_ns,
    )
    lap = HistoryLap(
        participant_id=race_id,
        lap_number=1,
        lap_time_ns=time_ns,
        lane=1,
        lap_id=race_id,
        excluded=False,
    )
    return HistoryRace(
        race_id=race_id,
        name=f"Lauf {race_id}",
        track_id=1,
        track_name="Ring",
        layout_id=4,
        status=RaceStatus.FINISHED,
        mode=mode,
        finished_at=_WHEN,
        participants=(person,),
        laps=(lap,),
    )
