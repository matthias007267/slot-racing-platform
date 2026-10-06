"""Time-trial board: absolute lane records, current drivers, and this attempt's laps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QTableWidget
from pytestqt.qtbot import QtBot

from slot_racing.core.clock import NANOS_PER_SECOND
from slot_racing.core.domain import DriverId, RaceId, TrackId, VehicleId
from slot_racing.modules.drivers_vehicles.service import VehicleInput
from slot_racing.modules.races.time_trial_board import (
    RECENT_LAP_COUNT,
    build_time_trial_board,
    format_lap_seconds,
)
from slot_racing.modules.races.types import ParticipantInfo, TimeMeasurementInfo
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from tests.modules.conftest import Env
from tests.modules.test_ui_management import cells, column_text, open_page
from tests.support.start_sequence import release_start_lights

_ORIGIN = datetime(2026, 10, 1, tzinfo=UTC)


def test_lap_seconds_use_a_german_decimal_comma() -> None:
    assert format_lap_seconds(None) == "-"
    assert format_lap_seconds(0) == "0,000 s"
    assert format_lap_seconds(8_241_000_000) == "8,241 s"


def test_absolute_best_is_one_row_per_lane_with_its_driver_and_vehicle() -> None:
    board = build_time_trial_board(
        lane_count=4,
        race_id=RaceId(2),
        measurements=(
            _lap(1, race=1, lane=1, driver="Lisa", vehicle="Audi R8", time_ns=8_402_000_000),
            _lap(2, race=1, lane=1, driver="Max", vehicle="Porsche 911", time_ns=8_241_000_000),
            _lap(3, race=1, lane=2, driver="Thomas", vehicle="BMW M4", time_ns=8_315_000_000),
            _lap(4, race=1, lane=3, driver="Max", vehicle="Porsche 911", time_ns=8_198_000_000),
            _lap(5, race=9, lane=4, driver="Lisa", vehicle="Audi R8", time_ns=9_000_000_000),
        ),
        participants=(),
    )
    shown = [
        (line.lane, line.driver_label, line.vehicle_label, line.time_ns) for line in board.records
    ]
    assert shown == [
        (1, "Max", "Porsche 911", 8_241_000_000),
        (2, "Thomas", "BMW M4", 8_315_000_000),
        (3, "Max", "Porsche 911", 8_198_000_000),
        (4, "Lisa", "Audi R8", 9_000_000_000),
    ]


def test_a_faster_time_replaces_the_lane_best_and_a_slower_one_does_not() -> None:
    held = (
        _lap(1, race=1, lane=1, driver="Lisa", vehicle="Audi R8", time_ns=8_102_000_000),
        _lap(2, race=1, lane=2, driver="Thomas", vehicle="BMW M4", time_ns=8_315_000_000),
    )
    slower = build_time_trial_board(
        lane_count=2,
        race_id=RaceId(2),
        measurements=(
            *held,
            _lap(3, race=2, lane=1, driver="Max", vehicle="Porsche 911", time_ns=8_241_000_000),
        ),
        participants=(),
    )
    assert slower.records[0].driver_label == "Lisa"
    assert slower.records[0].time_ns == 8_102_000_000
    assert slower.records[1].driver_label == "Thomas"
    faster = build_time_trial_board(
        lane_count=2,
        race_id=RaceId(2),
        measurements=(
            *held,
            _lap(4, race=2, lane=1, driver="Max", vehicle="Porsche 911", time_ns=8_001_000_000),
        ),
        participants=(),
    )
    updated = faster.records[0]
    assert (updated.driver_label, updated.vehicle_label, updated.time_ns) == (
        "Max",
        "Porsche 911",
        8_001_000_000,
    )
    assert faster.records[1].time_ns == 8_315_000_000


def test_an_equal_time_keeps_the_earlier_measurement() -> None:
    board = build_time_trial_board(
        lane_count=1,
        race_id=RaceId(1),
        measurements=(
            _lap(2, race=1, lane=1, driver="Later", vehicle="BMW M4", time_ns=8_000_000_000, at=1),
            _lap(1, race=1, lane=1, driver="Earlier", vehicle="Porsche 911", time_ns=8_000_000_000),
        ),
        participants=(),
    )
    assert board.records[0].driver_label == "Earlier"
    assert board.records[0].vehicle_label == "Porsche 911"


def test_lane_bests_are_not_mixed() -> None:
    board = build_time_trial_board(
        lane_count=2,
        race_id=RaceId(1),
        measurements=(
            _lap(1, race=1, lane=1, driver="Max", vehicle="Porsche 911", time_ns=9_000_000_000),
            _lap(2, race=1, lane=2, driver="Thomas", vehicle="BMW M4", time_ns=7_000_000_000),
        ),
        participants=(),
    )
    assert board.records[0].time_ns == 9_000_000_000
    assert board.records[0].driver_label == "Max"
    assert board.records[1].time_ns == 7_000_000_000
    assert board.records[1].driver_label == "Thomas"


def test_current_drivers_keep_their_lane_and_empty_lanes_stay_visible() -> None:
    board = build_time_trial_board(
        lane_count=4,
        race_id=RaceId(2),
        measurements=(),
        participants=(
            _driver(1, "Max", "Porsche 911"),
            _driver(3, "Thomas", "BMW M4"),
            _driver(4, "Lisa", "Audi R8"),
        ),
    )
    shown = [
        (line.lane, line.occupied, line.driver_label, line.vehicle_label) for line in board.active
    ]
    assert shown == [
        (1, True, "Max", "Porsche 911"),
        (2, False, None, None),
        (3, True, "Thomas", "BMW M4"),
        (4, True, "Lisa", "Audi R8"),
    ]
    assert [attempt.lane for attempt in board.attempts] == [1, 3, 4]


def test_last_five_laps_are_separate_per_driver_and_newest_is_minus_one() -> None:
    laps = [
        _lap(
            index,
            race=2,
            lane=1,
            driver="Max",
            vehicle="Porsche 911",
            time_ns=8_000_000_000 + index,
        )
        for index in range(1, 8)
    ]
    laps.append(_lap(20, race=2, lane=3, driver="Thomas", vehicle="BMW M4", time_ns=9_000_000_000))
    board = build_time_trial_board(
        lane_count=3,
        race_id=RaceId(2),
        measurements=laps,
        participants=(_driver(1, "Max", "Porsche 911"), _driver(3, "Thomas", "BMW M4")),
    )
    assert [attempt.lane for attempt in board.attempts] == [1, 3]
    max_laps = board.attempts[0].recent
    assert len(max_laps) == RECENT_LAP_COUNT
    assert [line.offset for line in max_laps] == [-5, -4, -3, -2, -1]
    assert [line.time_ns for line in max_laps] == [
        8_000_000_003,
        8_000_000_004,
        8_000_000_005,
        8_000_000_006,
        8_000_000_007,
    ]
    assert [line.time_ns for line in board.attempts[1].recent] == [9_000_000_000]
    assert [line.offset for line in board.attempts[1].recent] == [-1]
    assert board.attempts[0].session_best_ns == 8_000_000_001
    assert board.attempts[0].session_best_ns not in {line.time_ns for line in max_laps}


def test_fewer_than_five_laps_are_shown_with_the_newest_last() -> None:
    board = build_time_trial_board(
        lane_count=1,
        race_id=RaceId(1),
        measurements=(
            _lap(1, race=1, lane=1, driver="Max", vehicle="Porsche 911", time_ns=8_421_000_000),
            _lap(
                2,
                race=1,
                lane=1,
                driver="Max",
                vehicle="Porsche 911",
                time_ns=8_241_000_000,
                at=1,
            ),
        ),
        participants=(_driver(1, "Max", "Porsche 911"),),
    )
    recent = board.attempts[0].recent
    assert [(line.offset, line.time_ns) for line in recent] == [
        (-2, 8_421_000_000),
        (-1, 8_241_000_000),
    ]
    assert board.attempts[0].session_best_ns == 8_241_000_000


def test_session_best_stays_apart_from_the_historical_lane_record() -> None:
    board = build_time_trial_board(
        lane_count=1,
        race_id=RaceId(2),
        measurements=(
            _lap(1, race=1, lane=1, driver="Lisa", vehicle="Audi R8", time_ns=8_102_000_000),
            _lap(2, race=2, lane=1, driver="Max", vehicle="Porsche 911", time_ns=8_241_000_000),
        ),
        participants=(_driver(1, "Max", "Porsche 911"),),
    )
    attempt = board.attempts[0]
    assert attempt.driver_label == "Max"
    assert attempt.session_best_ns == 8_241_000_000
    assert attempt.lane_record_ns == 8_102_000_000
    assert board.records[0].driver_label == "Lisa"
    assert board.records[0].time_ns == 8_102_000_000


def test_a_running_time_trial_updates_the_board_from_each_new_lap(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=4)
    lisa = env.driver("Lisa")
    alex = env.driver("Alex")
    audi = env.vehicles.create_vehicle(VehicleInput(name="Audi", model="R8", driver_id=lisa.id))
    old_bmw = env.vehicles.create_vehicle(VehicleInput(name="BMW", model="M4", driver_id=alex.id))
    past = env.races.create_time_trial("Gestern", track.id)
    env.races.add_participant(past.id, lisa.id, audi.id, 1)
    env.races.add_participant(past.id, alex.id, old_bmw.id, 3)
    env.races.record_lap(past.id, 1, 1, 4_000_000_000, 4_000_000_000, {})
    env.races.record_lap(past.id, 3, 1, 6_000_000_000, 6_000_000_000, {})

    max_driver = env.driver("Max")
    thomas = env.driver("Thomas")
    porsche = env.vehicle("Porsche", driver_id=max_driver.id)
    bmw = env.vehicles.create_vehicle(VehicleInput(name="BMW", model="M4", driver_id=thomas.id))
    race = env.races.create_time_trial("Heute", track.id)
    env.races.add_participant(race.id, max_driver.id, porsche.id, 1)
    env.races.add_participant(race.id, thomas.id, bmw.id, 3)

    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    assert column_text(page.table, 0, "Name") == "Heute"
    page.table.selectRow(0)
    page.buttons["start"].click()
    release_start_lights(page.live)
    window.resize(1280, 800)
    window.show()
    QApplication.processEvents()

    live = page.live
    assert isinstance(live, LiveRaceView)
    assert live.stage.isHidden()
    assert not live.board.isHidden()
    assert [header_text(live.board.records_table, column) for column in range(4)] == [
        "Bahn",
        "Fahrer",
        "Fahrzeug",
        "Bestzeit",
    ]
    assert cells(live.board.records_table, 0) == ["1", "Lisa", "Audi R8", "4,000 s"]
    assert cells(live.board.records_table, 1) == ["2", "-", "-", "-"]
    assert cells(live.board.records_table, 2) == ["3", "Alex", "BMW M4", "6,000 s"]
    assert cells(live.board.records_table, 3) == ["4", "-", "-", "-"]
    assert cells(live.board.active_table, 0) == ["1", "Max", "Porsche 911"]
    assert cells(live.board.active_table, 1) == ["2", "frei", "-"]
    assert cells(live.board.active_table, 2) == ["3", "Thomas", "BMW M4"]
    assert cells(live.board.active_table, 3) == ["4", "frei", "-"]
    assert _attempt_names(live) == ["time-trial-attempt-1", "time-trial-attempt-3"]
    assert _label(live, "time-trial-session-best-1") == "Beste Runde dieses Versuchs: -"
    assert _label(live, "time-trial-lane-record-1") == "Bahnrekord: 4,000 s"
    assert _label(live, "time-trial-attempt-title-1") == "Bahn 1 - Max - Porsche 911"
    assert _label(live, "time-trial-attempt-title-3") == "Bahn 3 - Thomas - BMW M4"
    badge = live.board.findChild(QLabel, "time-trial-lane-badge-1")
    assert badge is not None and badge.text() == "1"
    assert badge.font().pixelSize() >= 28
    assert _laps(live, 1).rowCount() == 0

    titles = [
        live.board.findChild(QLabel, name)
        for name in (
            "time-trial-records-section-title",
            "time-trial-active-section-title",
            "time-trial-laps-section-title",
        )
    ]
    assert [label.text() if label is not None else "" for label in titles] == [
        "Absolute Bestzeiten",
        "Aktuell",
        "Live-Runden",
    ]
    assert all(label is not None and not label.isHidden() for label in titles)
    first, third = _cards(live)
    assert (
        third.mapTo(live.board, third.rect().topLeft()).x()
        > first.mapTo(live.board, first.rect().topLeft()).x()
    )
    assert first.width() > 80 and abs(first.width() - third.width()) <= 4

    runner = live.runner
    assert runner is not None
    env.clock.advance(6 * NANOS_PER_SECOND)
    runner.tick()
    assert _laps(live, 1).rowCount() == 1
    assert cells(_laps(live, 1), 0)[0] == "-1"
    assert _laps(live, 3).rowCount() == 1
    session_lane_1 = _label(live, "time-trial-session-best-1")
    assert session_lane_1.startswith("Beste Runde dieses Versuchs: ")
    assert session_lane_1 != "Beste Runde dieses Versuchs: -"
    assert session_lane_1 != "Beste Runde dieses Versuchs: 4,000 s"
    assert _label(live, "time-trial-lane-record-1") == "Bahnrekord: 4,000 s"
    assert cells(live.board.records_table, 0) == ["1", "Lisa", "Audi R8", "4,000 s"]
    thomas_time = cells(_laps(live, 3), 0)[1]
    assert cells(live.board.records_table, 2)[1:] == ["Thomas", "BMW M4", thomas_time]
    assert cells(live.board.records_table, 2)[3] != "6,000 s"
    assert cells(live.board.records_table, 1) == ["2", "-", "-", "-"]
    assert cells(_laps(live, 1), 0)[1] != cells(_laps(live, 3), 0)[1]

    for _ in range(80):
        measured = [
            row for row in env.races.list_time_measurements(race_id=race.id) if row.lane == 1
        ]
        if len(measured) >= 6:
            break
        env.clock.advance(NANOS_PER_SECOND)
        runner.tick()
    measured = [row for row in env.races.list_time_measurements(race_id=race.id) if row.lane == 1]
    assert len(measured) >= 6
    last = measured[-RECENT_LAP_COUNT:]
    table = _laps(live, 1)
    assert table.rowCount() == RECENT_LAP_COUNT
    assert [cells(table, index) for index in range(RECENT_LAP_COUNT)] == [
        [str(index - RECENT_LAP_COUNT), format_lap_seconds(row.time_ns)]
        for index, row in enumerate(last)
    ]
    best = min(row.time_ns for row in measured)
    assert _label(live, "time-trial-session-best-1") == (
        f"Beste Runde dieses Versuchs: {format_lap_seconds(best)}"
    )
    assert _label(live, "time-trial-lane-record-1") == "Bahnrekord: 4,000 s"
    assert cells(live.board.records_table, 0)[3] == "4,000 s"
    lane_3 = [
        row.time_ns for row in env.races.list_time_measurements(race_id=race.id) if row.lane == 3
    ]
    assert cells(_laps(live, 3), _laps(live, 3).rowCount() - 1)[1] == format_lap_seconds(lane_3[-1])
    assert {cells(table, index)[1] for index in range(table.rowCount())}.isdisjoint(
        {cells(_laps(live, 3), index)[1] for index in range(_laps(live, 3).rowCount())}
    )

    page.show_results(race.id)
    assert cells(page.results.records_table, 0) == ["1", "Lisa", "Audi R8", "4,000 s"]
    assert page.results.records_table.isHidden() is False
    assert page.results.table.isHidden()
    assert ":" in cells(page.results.measurements_table, 0)[3]


def _driver(lane: int, driver: str, vehicle: str) -> ParticipantInfo:
    return ParticipantInfo(
        id=lane,
        driver_id=DriverId(lane),
        driver_label=driver,
        vehicle_id=VehicleId(lane),
        vehicle_label=vehicle,
        lane=lane,
    )


def _lap(
    identifier: int,
    *,
    race: int,
    lane: int,
    driver: str,
    vehicle: str,
    time_ns: int,
    at: int = 0,
) -> TimeMeasurementInfo:
    return TimeMeasurementInfo(
        id=identifier,
        race_id=RaceId(race),
        track_id=TrackId(1),
        driver_id=DriverId(lane),
        driver_label=driver,
        vehicle_id=VehicleId(lane),
        vehicle_label=vehicle,
        lane=lane,
        time_ns=time_ns,
        recorded_at=_ORIGIN + timedelta(seconds=at + identifier),
    )


def _label(live: LiveRaceView, name: str) -> str:
    label = live.board.findChild(QLabel, name)
    assert label is not None
    return label.text()


def _laps(live: LiveRaceView, lane: int) -> QTableWidget:
    table = live.board.findChild(QTableWidget, f"time-trial-attempt-laps-{lane}")
    assert table is not None
    return table


def _attempt_names(live: LiveRaceView) -> list[str]:
    names: list[str] = []
    for index in range(live.board.attempt_row.count()):
        item = live.board.attempt_row.itemAt(index)
        widget = None if item is None else item.widget()
        if widget is not None:
            names.append(widget.objectName())
    return names


def _cards(live: LiveRaceView) -> tuple[QFrame, QFrame]:
    first = live.board.findChild(QFrame, "time-trial-attempt-1")
    third = live.board.findChild(QFrame, "time-trial-attempt-3")
    assert isinstance(first, QFrame) and isinstance(third, QFrame)
    assert first.width() > 0 and third.width() > 0
    return first, third


def header_text(table: QTableWidget, column: int) -> str:
    item = table.horizontalHeaderItem(column)
    return "" if item is None else item.text()
