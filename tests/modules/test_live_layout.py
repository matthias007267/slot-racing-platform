"""Fixed live-race layout: lane cards, clock, status and a compact ranking."""

from __future__ import annotations

import itertools

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.app.main_window import MainWindow
from slot_racing.core.clock import NANOS_PER_SECOND, format_duration
from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.modules.races.runner import LiveRow, RaceSnapshot
from slot_racing.modules.races.ui.lane_card import LaneCardBoard
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.results_view import ResultsView
from tests.modules.conftest import Env
from tests.modules.test_hud_editor import _translator
from tests.modules.test_live_race_ui import _ready_race
from tests.modules.test_ui_management import column_text, open_page
from tests.support.start_sequence import release_start_lights

_LONG_DRIVER = "Maximilian Alexander von Hohenstein"
_LONG_VEHICLE = "Porsche 911 Carrera RS Langstreckenversion"


def test_lane_cards_show_every_lane_for_both_modes(qtbot: QtBot) -> None:
    board = LaneCardBoard(_translator())
    qtbot.addWidget(board)
    board.resize(900, 400)
    board.show()
    rows = (
        _row(1, 2, "Zoe", "Porsche 911", 7, last=None, best=None, total=None),
        _row(2, 1, "Anna", "Ferrari 488", 3, last=4_200_000_000, best=4_100_000_000),
    )
    board.show_snapshot(_snapshot(rows, laps=8), lane_count=3, mode=RaceMode.LAPS)
    identities = {lane: card for lane, card in board.cards.items()}
    assert set(board.cards) == {1, 2, 3}
    zoe = board.cards[1]
    assert zoe.lane_label.text() == "Bahn 1"
    assert zoe.position_label.text() == "P2"
    assert zoe.driver_label.text() == "Zoe"
    assert zoe.vehicle_label.text() == "Porsche 911"
    assert zoe.start_label.text() == "7"
    assert zoe.lap_label.text() == "1 / 8"
    assert zoe.last_label.text() == "-"
    assert zoe.best_label.text() == "-"
    assert zoe.total_label.text() == "-"
    assert zoe.total_label.isVisibleTo(zoe)
    assert zoe.status_label.text() == "Fährt"
    free = board.cards[3]
    assert free.driver_label.text() == "frei"
    assert free.last_label.text() == "-"
    assert free.position_label.text() == "—"
    board.show_snapshot(
        _snapshot(rows, laps=8, status=RaceStatus.PAUSED), lane_count=3, mode=RaceMode.LAPS
    )
    assert board.cards[1] is identities[1]
    assert board.cards[1].status_label.text() == "Wartet"
    board.show_snapshot(_snapshot(rows, laps=0), lane_count=2, mode=RaceMode.TIME_TRIAL)
    assert board.cards[1] is identities[1]
    assert 3 not in board.cards
    assert not board.cards[1].total_label.isVisibleTo(board.cards[1])
    assert board.cards[1].position_label.text() == "P2"
    assert set(board.cards) == {1, 2}


@pytest.mark.parametrize("attempt", [1, 2])
def test_two_lanes_stay_side_by_side_at_several_sizes(qtbot: QtBot, env: Env, attempt: int) -> None:
    del attempt
    window, live = _shown(qtbot, env, laps=2, width=1280, height=800)
    _assert_frame(live)
    left, right = live.lanes.cards[1], live.lanes.cards[2]
    assert left.driver_label.text() == "Zoe"
    assert right.driver_label.text() == "Anna"
    assert left.last_label.text() == "-"
    assert left.best_label.text() == "-"
    assert left.total_label.isVisibleTo(left)
    assert left.position_label.font().pixelSize() >= left.driver_label.font().pixelSize()
    assert left.driver_label.font().pixelSize() > left.vehicle_label.font().pixelSize()
    identities = {lane: id(card) for lane, card in live.lanes.cards.items()}
    for width, height in ((640, 420), (1600, 900), (900, 520)):
        window.resize(width, height)
        QApplication.processEvents()
        _assert_frame(live)
        _assert_pair(live)
        if width >= 1100:
            assert not live.table.isColumnHidden(2)
        content = live.findChild(QWidget, "live-layout")
        assert content is not None
        assert content.width() <= live.stage.viewport().width() + 1
    assert {lane: id(card) for lane, card in live.lanes.cards.items()} == identities


def test_four_lanes_wrap_without_reordering_the_cards(qtbot: QtBot, env: Env) -> None:
    window, live = _shown(qtbot, env, laps=3, lanes=4, width=1600, height=900)
    identities = {lane: id(card) for lane, card in live.lanes.cards.items()}
    assert _row_count(live) == 1
    _assert_lane_order(live)
    window.resize(760, 720)
    QApplication.processEvents()
    assert _row_count(live) >= 2
    _assert_lane_order(live)
    assert {lane: id(card) for lane, card in live.lanes.cards.items()} == identities
    content = live.findChild(QWidget, "live-layout")
    assert content is not None
    assert content.width() <= live.stage.viewport().width() + 1
    assert live.stage.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_cards_follow_the_race_without_being_rebuilt(qtbot: QtBot, env: Env) -> None:
    _ready_race(env, laps=2)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    assert isinstance(live, LiveRaceView)
    release_start_lights(live)
    runner = live.runner
    assert runner is not None
    identities = {lane: id(card) for lane, card in live.lanes.cards.items()}
    zoe = live.lanes.cards[1]
    anna = live.lanes.cards[2]
    assert zoe.position_label.text() == "P1"
    assert zoe.status_label.text() == "Fährt"
    assert zoe.last_label.text() == "-"

    env.clock.advance(5 * NANOS_PER_SECOND)
    live.refresh()
    assert {lane: id(card) for lane, card in live.lanes.cards.items()} == identities
    snapshot = runner.snapshot()
    for row in snapshot.rows:
        card = live.lanes.cards[row.lane]
        assert card.position_label.text() == f"P{row.position}"
        assert card.driver_label.text() == row.driver_label
        assert card.last_label.text() == format_duration(row.last_lap_ns)
        assert card.best_label.text() == format_duration(row.best_lap_ns)
        assert card.total_label.text() == format_duration(row.total_time_ns)
    assert zoe.last_label.text() != "-"
    assert column_text(live.table, 0, "Platz") == "1"
    assert column_text(live.table, 0, "Fahrer") == snapshot.rows[0].driver_label
    assert not live.table.isColumnHidden(1)
    assert not live.table.isColumnHidden(5)

    frozen = live.time_label.text()
    live.pause_button.click()
    assert runner.snapshot().status is RaceStatus.PAUSED
    assert live.status_label.text() == "● Pausiert"
    assert live.status.property("tone") == "warn"
    assert zoe.status_label.text() == "Wartet"
    assert anna.status_label.text() == "Wartet"
    assert not live.pause_button.isEnabled()
    assert live.resume_button.isEnabled()
    env.clock.advance(4 * NANOS_PER_SECOND)
    live.refresh()
    assert live.time_label.text() == frozen
    assert {lane: id(card) for lane, card in live.lanes.cards.items()} == identities

    live.resume_button.click()
    assert live.status_label.text() == "● Läuft"
    assert live.status.property("tone") == "ok"
    assert live.pause_button.isEnabled()
    for _ in range(120):
        if isinstance(page.current_view(), ResultsView):
            break
        env.clock.advance(NANOS_PER_SECOND)
        live.refresh()
    assert isinstance(page.current_view(), ResultsView)
    assert live.status_label.text() == "● Beendet"
    assert live.status_label.property("tone") == "info"
    assert zoe.status_label.text() == "Fertig"
    assert live.results_button.isEnabled()
    assert live.back_button.isEnabled()
    assert {lane: id(card) for lane, card in live.lanes.cards.items()} == identities


def test_long_names_remain_fully_readable(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    first = env.driver(_LONG_DRIVER, number=12)
    second = env.driver("Anna", number=4)
    porsche = env.vehicle(_LONG_VEHICLE, driver_id=first.id)
    ferrari = env.vehicle("Ferrari", driver_id=second.id)
    race = env.races.create_race("Lang", track.id, 2)
    env.races.add_participant(race.id, first.id, porsche.id, 1)
    env.races.add_participant(race.id, second.id, ferrari.id, 2)
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    assert isinstance(live, LiveRaceView)
    release_start_lights(live)
    window.resize(1280, 800)
    window.show()
    QApplication.processEvents()
    card = live.lanes.cards[1]
    assert card.driver_label.text() == _LONG_DRIVER
    assert card.vehicle_label.text().startswith(_LONG_VEHICLE)
    _assert_wrapped(card.driver_label)
    _assert_wrapped(card.vehicle_label)
    assert card.driver_label.geometry().right() <= card.width() + 1
    assert card.vehicle_label.geometry().right() <= card.width() + 1


def test_an_empty_lane_stays_in_place(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    zoe = env.driver("Zoe", number=7)
    porsche = env.vehicle("Porsche", driver_id=zoe.id)
    race = env.races.create_race("Solo", track.id, 2)
    env.races.add_participant(race.id, zoe.id, porsche.id, 1)
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    assert isinstance(live, LiveRaceView)
    release_start_lights(live)
    window.resize(1100, 700)
    window.show()
    QApplication.processEvents()
    assert live.lanes.cards[1].driver_label.text() == "Zoe"
    assert live.lanes.cards[2].driver_label.text() == "frei"
    assert live.lanes.cards[2].last_label.text() == "-"
    _assert_pair(live)


def test_a_time_trial_keeps_its_board_and_controls(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    max_driver = env.driver("Max", number=11)
    anna = env.driver("Anna", number=5)
    porsche = env.vehicle("Porsche", driver_id=max_driver.id)
    ferrari = env.vehicle("Ferrari", driver_id=anna.id)
    race = env.races.create_time_trial("Heute", track.id)
    env.races.add_participant(race.id, max_driver.id, porsche.id, 1)
    env.races.add_participant(race.id, anna.id, ferrari.id, 2)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    assert isinstance(live, LiveRaceView)
    release_start_lights(live)
    assert live.stage.isHidden()
    assert not live.board.isHidden()
    assert not live.lanes.cards[1].total_label.isVisibleTo(live.lanes.cards[1])
    assert live.lanes.cards[1].driver_label.text() == "Max"
    assert live.lanes.cards[2].driver_label.text() == "Anna"
    live.board.pause_button.click()
    runner = live.runner
    assert runner is not None and runner.snapshot().status is RaceStatus.PAUSED
    live.board.resume_button.click()
    assert runner.snapshot().status is RaceStatus.RUNNING
    live.confirm = lambda _text: True
    live.board.stop_button.click()
    assert runner.snapshot().status is RaceStatus.FINISHED
    assert live.board.results_button.isEnabled()
    assert live.board.back_button.isEnabled()


def test_abort_results_and_back_still_use_the_race_controls(qtbot: QtBot, env: Env) -> None:
    window, live = _shown(qtbot, env, laps=4, width=1100, height=700)
    page = window.current_page()
    assert isinstance(page, RacesPage)
    live.confirm = lambda _text: False
    live.stop_button.click()
    runner = live.runner
    assert runner is not None and runner.snapshot().status is RaceStatus.RUNNING
    live.confirm = lambda _text: True
    live.stop_button.click()
    assert runner.snapshot().status is RaceStatus.FINISHED
    assert live.status_label.text() == "● Abgebrochen"
    assert live.status_label.property("tone") == "error"
    assert live.results_button.isEnabled()
    live.results_button.click()
    assert isinstance(page.current_view(), ResultsView)
    live.back_button.click()
    assert not isinstance(page.current_view(), LiveRaceView)


def _shown(
    qtbot: QtBot,
    env: Env,
    *,
    laps: int,
    width: int,
    height: int,
    lanes: int = 2,
) -> tuple[MainWindow, LiveRaceView]:
    if lanes == 2 and laps == 2:
        _ready_race(env, laps=laps)
    else:
        track = env.track("Heimbahn", lanes=lanes)
        race = env.races.create_race("Finale", track.id, laps)
        for lane in range(1, lanes + 1):
            driver, vehicle = env.pair(lane)
            env.races.add_participant(race.id, driver, vehicle, lane)
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    assert isinstance(live, LiveRaceView)
    release_start_lights(live)
    window.resize(width, height)
    window.show()
    QApplication.processEvents()
    return window, live


def _assert_frame(live: LiveRaceView) -> None:
    assert live.header.isHidden()
    assert live.progress.isHidden()
    assert live.highlight.isHidden()
    assert live.last_lap.isHidden()
    assert live.best_lap.isHidden()
    assert not live.stage.isHidden()
    assert abs(live.clock.y() - live.status.y()) <= 4
    assert live.clock.x() < live.status.x()
    assert abs(live.clock.width() - live.status.width()) <= 12
    assert live.clock.y() < live.lanes.y()
    assert live.ranking.x() > live.lanes.geometry().right() - 4
    assert live.ranking.y() + 8 >= live.lanes.y()
    assert live.messages.y() > live.lanes.y()
    assert live.controls.y() > live.lanes.y()
    assert live.controls.x() > live.messages.x()
    assert live.pause_button.isEnabled()
    assert live.stage.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def _assert_pair(live: LiveRaceView) -> None:
    left, right = live.lanes.cards[1], live.lanes.cards[2]
    assert left.lane == 1 and right.lane == 2
    assert abs(left.y() - right.y()) <= 2
    assert left.x() < right.x()
    assert abs(left.width() - right.width()) <= 4
    assert left.width() > 40
    assert not left.geometry().intersects(right.geometry())


def _assert_lane_order(live: LiveRaceView) -> None:
    cards = [live.lanes.cards[lane] for lane in range(1, len(live.lanes.cards) + 1)]
    for earlier, later in itertools.pairwise(cards):
        assert (earlier.y(), earlier.x()) < (later.y(), later.x())


def _row_count(live: LiveRaceView) -> int:
    return len({card.geometry().y() for card in live.lanes.cards.values()})


def _assert_wrapped(label: QLabel) -> None:
    assert label.width() > 20
    assert label.height() > 0
    metrics = QFontMetrics(label.font())
    bounds = metrics.boundingRect(
        0, 0, label.width(), 4_000, int(Qt.TextFlag.TextWordWrap), label.text()
    )
    assert bounds.height() <= label.height() + 2


def _row(
    lane: int,
    position: int,
    driver: str,
    vehicle: str,
    number: int,
    *,
    last: int | None,
    best: int | None,
    total: int | None = 9_000_000_000,
) -> LiveRow:
    return LiveRow(
        position=position,
        lane=lane,
        driver_label=driver,
        vehicle_label=vehicle,
        start_number=number,
        current_lap=1,
        laps_completed=0,
        last_lap_ns=last,
        total_time_ns=total,
        best_lap_ns=best,
        finished=False,
        lap_times_ns=(),
    )


def _snapshot(
    rows: tuple[LiveRow, ...],
    *,
    laps: int,
    status: RaceStatus = RaceStatus.RUNNING,
) -> RaceSnapshot:
    return RaceSnapshot(
        race_id=RaceId(1),
        name="Finale",
        track_name="Heimbahn",
        timing_provider="simulation",
        status=status,
        aborted=False,
        laps=laps,
        elapsed_ns=0,
        rows=rows,
        source_errors=(),
    )
