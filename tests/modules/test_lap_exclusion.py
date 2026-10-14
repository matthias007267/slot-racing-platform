"""One stored lap can leave the statistics and come back. The race result stays."""

from __future__ import annotations

from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.core.domain import RaceId
from slot_racing.core.statistics import TimeScope, career_summary, race_report, track_records
from slot_racing.core.storage import Database
from slot_racing.modules.races.models import Lap, TimeMeasurement
from slot_racing.modules.statistics.ui.page import StatisticsPage
from tests.modules.conftest import Env
from tests.modules.test_race_statistics import _choose, _finish, _finish_pair
from tests.modules.test_ui_management import cells, open_page


def test_excluding_a_lap_updates_the_figures_and_keeps_the_race_result(
    qtbot: QtBot, env: Env
) -> None:
    track = env.track("Ring", lanes=2)
    race_id, anna = _finish_pair(env, track.id)
    stored = env.races.get_results(race_id)
    _window, page = open_page(qtbot, env, "statistics")
    assert isinstance(page, StatisticsPage)
    _choose(page.layout_combo, "unassigned")
    assert page.records_table.rowCount() == 4
    fastest = cells(page.records_table, 0)
    assert fastest[0] == "0:08.000"
    assert fastest[1] == "Anna"
    asked: list[str] = []

    def decline(text: str) -> bool:
        asked.append(text)
        return False

    page.confirm = decline
    page.records_table.selectRow(0)
    page.exclude_button.click()
    assert page.records_table.rowCount() == 4
    assert fastest[1] in asked[0]
    assert fastest[2] in asked[0]
    assert fastest[0] in asked[0]
    assert env.races.get_results(race_id) == stored

    def accept(text: str) -> bool:
        asked.append(text)
        return True

    page.confirm = accept
    page.records_table.selectRow(0)
    page.exclude_button.click()
    assert page.records_table.rowCount() == 3
    assert cells(page.records_table, 0)[0] == "0:08.200"
    assert page.excluded_table.rowCount() == 1
    assert cells(page.excluded_table, 0)[0] == "0:08.000"
    assert cells(page.excluded_table, 0)[1] == "Anna"
    assert env.races.get_results(race_id) == stored
    scope = TimeScope(track_id=int(track.id), unassigned=True)
    summary = career_summary(env.races.list_completed(), scope, driver_id=int(anna.id))
    assert summary.wins == 1
    assert summary.laps == 1
    assert summary.best_lap_ns == 8_200_000_000
    assert summary.average_lap_ns == 8_200_000_000
    loaded = env.races.get_completed(race_id)
    assert loaded is not None
    report = race_report(loaded)
    anna_line = next(line for line in report.lines if line.driver_label == "Anna")
    assert anna_line.position == 1
    assert anna_line.lap_count == 2
    assert anna_line.best_lap_ns == 8_000_000_000
    fastest_ns = next(line.time_ns for line in track_records(env.races.list_completed(), scope))
    assert fastest_ns == 8_200_000_000

    for _ in range(4):
        if page.records_table.isHidden():
            break
        page.records_table.selectRow(0)
        page.exclude_button.click()
    assert not page.records_empty.isHidden()
    assert page.records_empty.text() == "Keine gültige Runde für diese Layoutzuordnung."
    assert page.records_table.isHidden()
    assert page.excluded_table.rowCount() == 4
    assert env.races.get_results(race_id) == stored

    page.excluded_table.selectRow(0)
    page.restore_button.click()
    assert not page.records_table.isHidden()
    assert page.records_table.rowCount() == 1
    assert page.excluded_table.rowCount() == 3
    restored = career_summary(
        env.races.list_completed(),
        TimeScope(track_id=int(track.id), unassigned=True),
        driver_id=int(anna.id),
    )
    assert restored.laps == 1
    assert restored.best_lap_ns == 8_000_000_000

    _again, reopened = open_page(qtbot, env, "statistics")
    assert isinstance(reopened, StatisticsPage)
    _choose(reopened.layout_combo, "unassigned")
    assert reopened.records_table.rowCount() == 1
    assert reopened.excluded_table.rowCount() == 3
    reopened.excluded_table.selectRow(0)
    reopened.confirm = accept
    reopened.restore_button.click()
    assert reopened.excluded_table.rowCount() == 2
    assert env.races.get_results(race_id) == stored


def test_a_time_trial_exclusion_moves_the_lane_best_and_can_be_linked_later(
    qtbot: QtBot, env: Env
) -> None:
    track = env.track("Ring")
    race = _finish(env, track.id, "Versuch", "Zoe", "Porsche", (7_000_000_000, 9_000_000_000))
    stored = env.races.get_results(race.id)
    measurements = env.races.list_time_measurements(race_id=race.id)
    assert len(measurements) == 2
    assert measurements[0].excluded is False
    _window, page = open_page(qtbot, env, "statistics")
    assert isinstance(page, StatisticsPage)
    page.track_combo.setCurrentIndex(page.track_combo.findData(track.id))
    _choose(page.layout_combo, "unassigned")
    assert cells(page.table, 0)[3] == "7,000 s"
    page.confirm = lambda _text: True
    _select_time(page, "0:07.000")
    page.exclude_button.click()
    assert cells(page.table, 0)[3] == "9,000 s"
    assert [row.time_ns for row in env.races.lane_rankings(track_id=track.id)[0].places] == [
        9_000_000_000
    ]
    assert {row.time_ns for row in env.races.list_time_measurements(race_id=race.id)} == {
        7_000_000_000,
        9_000_000_000,
    }
    assert env.races.get_results(race.id) == stored

    _unlink(env, race.id, 9_000_000_000)
    _select_time(page, "0:09.000")
    page.exclude_button.click()
    assert cells(page.table, 0)[3] == "-"
    assert not page.records_empty.isHidden()
    assert env.races.lane_rankings(track_id=track.id) == []
    linked = env.races.list_time_measurements(race_id=race.id)
    assert {row.excluded for row in linked} == {True}
    assert env.races.get_results(race.id) == stored

    page.excluded_table.selectRow(0)
    page.restore_button.click()
    assert cells(page.table, 0)[3] == "7,000 s"
    assert [row.time_ns for row in env.races.lane_rankings(track_id=track.id)[0].places] == [
        7_000_000_000
    ]


def _select_time(page: StatisticsPage, time_text: str) -> None:
    for row in range(page.records_table.rowCount()):
        if cells(page.records_table, row)[0] == time_text:
            page.records_table.selectRow(row)
            return
    raise AssertionError(time_text)


def _unlink(env: Env, race_id: RaceId, time_ns: int) -> None:
    database = env.runtime.services.get(Database)
    with database.session() as session:
        row = session.scalar(
            select(TimeMeasurement).where(
                TimeMeasurement.race_id == int(race_id),
                TimeMeasurement.time_ns == time_ns,
            )
        )
        assert row is not None
        row.lap_id = None
        lap = session.scalar(
            select(Lap).where(Lap.race_id == int(race_id), Lap.lap_time_ns == time_ns)
        )
        assert lap is not None
        assert lap.statistics_excluded is False
