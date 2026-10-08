"""Ended races feed the report, the career panels and the layout records."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from slot_racing.core.catalog import DriverInfo
from slot_racing.core.domain import ParticipantResult, RaceId, TrackId
from slot_racing.core.statistics import TimeScope, career_summary, track_records
from slot_racing.core.storage import Database
from slot_racing.modules.drivers_vehicles.ui.drivers_page import DriversPage
from slot_racing.modules.drivers_vehicles.ui.vehicles_page import VehiclesPage
from slot_racing.modules.races.models import RaceParticipant, TimeMeasurement
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.races.ui.results_view import ResultsView
from slot_racing.modules.statistics.ui.page import RecordDialog, StatisticsPage
from slot_racing.modules.track_planner.document import STRAIGHT_H, add_piece
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.uikit.career import MISSING
from slot_racing.uikit.chart import ChartPoint, ChartSeries, LineChart
from tests.modules.conftest import Env
from tests.modules.test_ui_management import cells, open_page


def test_a_new_race_keeps_the_current_layout_and_an_old_race_keeps_none(env: Env) -> None:
    track = env.track("Ring", lanes=2)
    before = _finish(env, track.id, "Historisch", "Anna", "Porsche", (8_000_000_000,))
    historic = env.races.get_completed(before.id)
    assert historic is not None and historic.layout_id is None
    planner = env.runtime.services.get(TrackPlannerService)
    planner.save(planner.load(track.id))
    first = env.tracks.current_layout_id(track.id)
    assert first is not None
    on_plan = _finish(env, track.id, "Plan", "Ben", "BMW", (9_000_000_000,))
    planner.save(add_piece(planner.load(track.id), STRAIGHT_H, 0, 0))
    planner.save(planner.load(track.id))
    second = env.tracks.current_layout_id(track.id)
    assert second is not None and second != first
    assert len(env.races.list_layouts(track.id)) == 2
    revised = _finish(env, track.id, "Neu", "Cara", "Audi", (7_000_000_000,))
    stored = {race.race_id: race.layout_id for race in env.races.list_completed()}
    assert stored[int(before.id)] is None
    assert stored[int(on_plan.id)] == first
    assert stored[int(revised.id)] == second
    current = track_records(
        env.races.list_completed(), TimeScope(track_id=int(track.id), layout_id=second)
    )
    assert [line.race_name for line in current] == ["Neu"]
    unassigned = track_records(
        env.races.list_completed(), TimeScope(track_id=int(track.id), unassigned=True)
    )
    assert [line.race_name for line in unassigned] == ["Historisch"]
    assert _measurement_layout(env, revised.id) == second
    assert _measurement_layout(env, before.id) is None


def test_deleting_a_race_removes_its_laps_from_the_records(env: Env) -> None:
    track = env.track("Ring")
    planner = env.runtime.services.get(TrackPlannerService)
    planner.save(planner.load(track.id))
    layout_id = env.tracks.current_layout_id(track.id)
    assert layout_id is not None
    race, _driver = _finish_pair(env, track.id)
    scope = TimeScope(track_id=int(track.id), layout_id=layout_id)
    assert track_records(env.races.list_completed(), scope)
    env.races.delete_race(race)
    assert track_records(env.races.list_completed(), scope) == ()
    assert env.races.get_completed(race) is None


def test_an_open_race_is_not_a_result_and_a_disqualification_drops_out(env: Env) -> None:
    track = env.track("Ring")
    race = env.races.create_race("Offen", track.id, 3)
    driver = env.driver("Anna")
    vehicle = env.vehicle("Porsche", driver.id)
    participant = env.races.add_participant(race.id, driver.id, vehicle.id, 1)
    env.races.record_lap(race.id, 1, 1, 8_000_000_000, 8_000_000_000, {})
    assert env.races.list_completed() == ()
    assert env.races.get_completed(race.id) is None
    env.races.record_finished(
        race.id,
        [ParticipantResult(driver.id, 1, 1, 1, True, 8_000_000_000, 8_000_000_000)],
        aborted=False,
    )
    _disqualify(env, participant.id)
    scope = TimeScope(track_id=int(track.id), unassigned=True)
    summary = career_summary(env.races.list_completed(), scope, driver_id=int(driver.id))
    assert summary.wins == 0
    assert summary.laps == 0
    assert track_records(env.races.list_completed(), scope) == ()


def test_the_same_plan_is_not_a_new_layout(env: Env) -> None:
    track = env.track("Ring")
    planner = env.runtime.services.get(TrackPlannerService)
    planner.save(add_piece(planner.load(track.id), STRAIGHT_H, 0, 0))
    planner.save(planner.load(track.id))
    assert len(env.races.list_layouts(track.id)) == 1
    assert env.races.list_layouts(track.id)[0].current is True


def test_migration_adds_an_empty_layout_column() -> None:
    database = Database.in_memory()
    database.migrate("0016")
    with database.engine.connect() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(time_measurements)")
        names = {row[1] for row in rows}
    assert "track_layout_id" not in names
    database.migrate()
    with database.engine.connect() as connection:
        info = list(connection.exec_driver_sql("PRAGMA table_info(time_measurements)"))
    column = next(row for row in info if row[1] == "track_layout_id")
    assert column[3] == 0
    assert database.schema_revision() == "0017"
    database.dispose()


def test_a_finished_race_shows_the_report_and_an_open_one_does_not(qtbot: QtBot, env: Env) -> None:
    track = env.track("Ring", lanes=2)
    finished, _driver = _finish_pair(env, track.id)
    view = ResultsView(env.runtime.translator, env.races)
    qtbot.addWidget(view)
    view.show_race(finished)
    assert view.report.table.rowCount() == 2
    assert cells(view.report.table, 0)[0] == "1"
    assert "0:08.000" in cells(view.report.table, 0)[6]
    assert cells(view.report.table, 0)[8] == "+0,000 s"
    assert view.report.status.text() == "Wertung: reguläres Ergebnis."
    open_race = env.races.create_race("Offen", track.id, 3)
    view.show_race(open_race.id)
    assert not view.report.empty.isHidden()
    assert view.report.table.isHidden()
    assert view.report.empty.text() == "Für dieses Rennen liegt noch keine Auswertung vor."


@pytest.mark.parametrize("attempt", [1, 2])
def test_driver_and_vehicle_statistics_follow_the_selection(
    qtbot: QtBot, env: Env, attempt: int
) -> None:
    del attempt
    track = env.track("Ring")
    other = env.track("Oval")
    race, driver = _finish_pair(env, track.id)
    _finish(env, other.id, "Woanders", "Cara", "Audi", (4_000_000_000,))
    _window, page = open_page(qtbot, env, "drivers")
    assert isinstance(page, DriversPage)
    assert page.career is not None
    page.refresh()
    page.select_id(int(driver.id))
    assert _text(page, "career-driver-races") == "1"
    assert _text(page, "career-driver-wins") == "1"
    assert _text(page, "career-driver-laps") == "2"
    assert _text(page, "career-driver-best") == MISSING
    assert _text(page, "career-driver-distance") == MISSING
    _choose(page.career.track_combo, track.id)
    _choose(page.career.layout_combo, "unassigned")
    assert _text(page, "career-driver-best") == "0:08.000"
    assert page.career.history_table.rowCount() == 1
    assert cells(page.career.history_table, 0)[1] == "Paar"
    _window_v, vehicles = open_page(qtbot, env, "vehicles")
    assert isinstance(vehicles, VehiclesPage)
    assert vehicles.career is not None
    vehicles.refresh()
    driven = next(
        person
        for person in env.races.require_race(race).participants
        if person.driver_id == driver.id
    )
    assert driven.vehicle_id is not None
    vehicles.select_id(int(driven.vehicle_id))
    assert _text(vehicles, "career-vehicle-races") == "1"
    assert _text(vehicles, "career-vehicle-best") == MISSING
    _choose(vehicles.career.track_combo, track.id)
    _choose(vehicles.career.layout_combo, "unassigned")
    assert _text(vehicles, "career-vehicle-best") == "0:08.000"
    env.races.delete_race(race)
    page.career.show_subject(int(driver.id))
    _choose(page.career.track_combo, track.id)
    _choose(page.career.layout_combo, "unassigned")
    assert _text(page, "career-driver-races") == "0"


@pytest.mark.parametrize("attempt", [1, 2])
def test_track_records_open_the_race_and_respect_the_lane(
    qtbot: QtBot, env: Env, attempt: int
) -> None:
    del attempt
    track = env.track("Ring", lanes=2)
    _finish_pair(env, track.id)
    _window, page = open_page(qtbot, env, "statistics")
    assert isinstance(page, StatisticsPage)
    page.track_combo.setCurrentIndex(page.track_combo.findData(track.id))
    assert cells(page.table, 0)[0] == "Bahn 1"
    _choose(page.layout_combo, "unassigned")
    assert page.records_table.rowCount() == 4
    assert cells(page.records_table, 0)[0] == "0:08.000"
    _choose(page.lane_combo, 2)
    assert page.records_table.rowCount() == 2
    assert cells(page.records_table, 0)[1] == "Ben"
    opened: list[RecordDialog] = []

    def run(dialog: RecordDialog) -> int:
        opened.append(dialog)
        return 0

    page.dialog_runner = run  # type: ignore[assignment]
    page.records_table.selectRow(0)
    page.show_selected_record()
    assert len(opened) == 1
    assert opened[0].report.table.rowCount() == 2


def test_statistics_without_a_track_hides_the_records(qtbot: QtBot, env: Env) -> None:
    _window, page = open_page(qtbot, env, "statistics")
    assert isinstance(page, StatisticsPage)
    assert page.table.rowCount() == 0
    assert page.records_table.isHidden()
    assert "Keine Strecke" in page.empty.text()


def test_a_chart_with_many_laps_still_draws(qtbot: QtBot) -> None:
    chart = LineChart()
    qtbot.addWidget(chart)
    chart.resize(240, 180)
    points = tuple(
        ChartPoint(index, 8 + (index % 7) * 0.05, marked=index == 2500) for index in range(5000)
    )
    chart.set_series((ChartSeries("Anna", points),), empty="Keine gültigen Runden.")
    chart.show()
    image = chart.grab()
    assert not image.isNull()
    assert image.width() == 240


def _finish(
    env: Env,
    track_id: TrackId,
    name: str,
    driver_name: str,
    vehicle_name: str,
    times: tuple[int, ...],
) -> RaceInfo:
    race = env.races.create_time_trial(name, track_id)
    driver = env.driver(driver_name)
    vehicle = env.vehicle(vehicle_name, driver.id)
    env.races.add_participant(race.id, driver.id, vehicle.id, 1)
    total = 0
    for number, time_ns in enumerate(times, start=1):
        total += time_ns
        env.races.record_lap(race.id, 1, number, time_ns, total, {})
    env.races.record_finished(
        race.id,
        [ParticipantResult(driver.id, 1, 1, len(times), True, total, min(times))],
        aborted=False,
    )
    return race


def _finish_pair(env: Env, track_id: TrackId) -> tuple[RaceId, DriverInfo]:
    race = env.races.create_race("Paar", track_id, 2)
    anna = env.driver("Anna")
    ben = env.driver("Ben")
    porsche = env.vehicle("Porsche", anna.id)
    bmw = env.vehicle("BMW", ben.id)
    env.races.add_participant(race.id, anna.id, porsche.id, 1)
    env.races.add_participant(race.id, ben.id, bmw.id, 2)
    env.races.record_lap(race.id, 1, 1, 8_000_000_000, 8_000_000_000, {})
    env.races.record_lap(race.id, 1, 2, 8_200_000_000, 16_200_000_000, {})
    env.races.record_lap(race.id, 2, 1, 9_000_000_000, 9_000_000_000, {})
    env.races.record_lap(race.id, 2, 2, 9_100_000_000, 18_100_000_000, {})
    env.races.record_finished(
        race.id,
        [
            ParticipantResult(anna.id, 1, 1, 2, True, 16_200_000_000, 8_000_000_000),
            ParticipantResult(ben.id, 2, 2, 2, True, 18_100_000_000, 9_000_000_000),
        ],
        aborted=False,
    )
    return race.id, anna


def _disqualify(env: Env, participant_id: int) -> None:
    database = env.runtime.services.get(Database)
    with database.session() as session:
        row = session.get(RaceParticipant, participant_id)
        assert row is not None
        row.disqualified = True


def _measurement_layout(env: Env, race_id: RaceId) -> int | None:
    from sqlalchemy import select

    database = env.runtime.services.get(Database)
    with database.session() as session:
        row = session.scalar(select(TimeMeasurement).where(TimeMeasurement.race_id == int(race_id)))
        assert row is not None
        return None if row.track_layout_id is None else int(row.track_layout_id)


def _text(page: object, name: str) -> str:
    label = page.findChild(QLabel, name)  # type: ignore[attr-defined]
    assert isinstance(label, QLabel)
    return label.text()


def _choose(combo: object, data: object) -> None:
    index = combo.findData(data)  # type: ignore[attr-defined]
    assert index >= 0
    combo.setCurrentIndex(index)  # type: ignore[attr-defined]
