"""The live HUD follows a simulated race without a camera."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from slot_racing.core.clock import NANOS_PER_SECOND, format_duration
from slot_racing.core.domain import RaceStatus
from slot_racing.modules.races.hud import (
    HudConfigurationStore,
    light_pixels,
    mark_default,
    replace_layout,
)
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.results_view import ResultsView
from tests.modules.conftest import Env
from tests.modules.test_live_race_ui import _ready_race
from tests.modules.test_ui_management import column_text, open_page
from tests.support.start_sequence import release_start_lights


def test_the_hud_follows_a_simulated_race(qtbot: QtBot, env: Env) -> None:
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
    assert runner is not None and runner.status is RaceStatus.RUNNING

    assert not hasattr(live, "header")
    assert live.clock.isVisibleTo(live)
    assert live.status.isVisibleTo(live)
    assert live.ranking.isVisibleTo(live)
    assert live.status_label.text() == "● Läuft"
    assert live.messages.message_label.text() == "Rennen gestartet"
    assert live.lanes.cards[1].lap_label.text() == "1 / 2"
    assert live.time_label.text() == "0:00.000"
    assert column_text(live.table, 0, "Fahrer") == "Zoe"
    assert live.lanes.cards[1].last_label.text() == "-"
    assert live.lanes.cards[1].best_label.text() == "-"

    env.clock.advance(3 * NANOS_PER_SECOND)
    live.refresh()
    assert live.time_label.text() == format_duration(runner.snapshot().elapsed_ns)
    assert live.time_label.text() == "0:03.000"

    env.clock.advance(2 * NANOS_PER_SECOND)
    live.refresh()
    assert column_text(live.table, 0, "Fahrer") == "Zoe"
    assert column_text(live.table, 0, "Runden") == "1"
    assert column_text(live.table, 0, "Platz") == "1"
    assert live.lanes.cards[1].lap_label.text() == "2 / 2"
    last = column_text(live.table, 0, "Letzte Runde")
    assert last != "-"
    assert live.lanes.cards[1].last_label.text() == last
    assert live.lanes.cards[1].driver_label.text() == "Zoe"
    assert live.lanes.cards[1].best_label.text() == last

    frozen = live.time_label.text()
    live.pause_button.click()
    assert live.status_label.text() == "● Pausiert"
    assert live.messages.message_label.text() == "Rennen pausiert"
    assert not live.pause_button.isEnabled()
    assert live.resume_button.isEnabled()
    assert runner.snapshot().status is RaceStatus.PAUSED
    env.clock.advance(5 * NANOS_PER_SECOND)
    live.refresh()
    assert live.time_label.text() == frozen
    assert column_text(live.table, 0, "Runden") == "1"

    live.resume_button.click()
    assert live.status_label.text() == "● Läuft"
    assert live.messages.message_label.text() == "Rennen fortgesetzt"
    env.clock.advance(NANOS_PER_SECOND)
    live.refresh()
    assert live.time_label.text() == "0:06.000"

    for _ in range(120):
        if isinstance(page.current_view(), ResultsView):
            break
        env.clock.advance(NANOS_PER_SECOND)
        live.refresh()
    assert isinstance(page.current_view(), ResultsView)
    assert live.messages.message_label.text() == "Rennen beendet"
    assert live.status_label.text() == "● Beendet"
    assert live.lanes.cards[1].best_label.text() != "-"
    assert live.lanes.cards[1].driver_label.text() == "Zoe"


def test_a_running_race_keeps_the_layout_it_opened_with(qtbot: QtBot, env: Env) -> None:
    store = env.runtime.services.get(HudConfigurationStore)
    _ready_race(env, laps=2)
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    release_start_lights(live)
    window.resize(1400, 860)
    window.show()
    QApplication.processEvents()
    assert live.clock.isVisibleTo(live)
    assert live.bound_layout().font_scale == 100
    opened = live.lanes.cards[1].driver_label.font().pixelSize()
    surface = _surface(live)

    current = store.load()
    layout = replace(current.default_layout(), font_scale=140, lanes_share=5, ranking_share=1)
    store.save(mark_default(replace_layout(current, layout), layout.id))
    live.refresh()
    QApplication.processEvents()
    assert live.bound_layout().font_scale == 100
    assert live.lanes.cards[1].driver_label.font().pixelSize() == opened
    assert _surface(live) == surface

    other = LiveRaceView(
        env.runtime.translator, env.controller, store, env.races, env.runtime.config
    )
    qtbot.addWidget(other)
    other.resize(1400, 860)
    other.show()
    runner = live.runner
    assert runner is not None
    other.show_runner(runner)
    QApplication.processEvents()
    assert other.bound_layout().font_scale == 140
    assert live.bound_layout().font_scale == 100


def test_showing_the_start_lights_does_not_move_the_hud(qtbot: QtBot, env: Env) -> None:
    _ready_race(env, laps=2)
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    release_start_lights(live)
    window.resize(1400, 860)
    window.show()
    QApplication.processEvents()
    surface = _surface(live)
    live.start_lights.show_lights(4)
    live.refresh()
    QApplication.processEvents()
    assert live.start_lights.isVisible()
    assert live.start_lights.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert _surface(live) == surface
    _assert_lights(live)

    window.resize(1100, 700)
    QApplication.processEvents()
    live.refresh()
    _assert_lights(live)
    resized = _surface(live)
    live.start_lights.clear()
    live.refresh()
    QApplication.processEvents()
    assert live.start_lights.isHidden()
    assert _surface(live) == resized


def _surface(live: LiveRaceView) -> tuple[object, ...]:
    cards = tuple((lane, card.geometry()) for lane, card in sorted(live.lanes.cards.items()))
    return (
        live.clock.geometry(),
        live.status.geometry(),
        live.lanes.geometry(),
        live.ranking.geometry(),
        live.messages.geometry(),
        live.controls.geometry(),
        live.stage.geometry(),
        live.board.geometry(),
        cards,
    )


def _assert_lights(live: LiveRaceView) -> None:
    target = live.board if live.stage.isHidden() and not live.board.isHidden() else live.stage
    rect = light_pixels(live.bound_layout().lights, target.width(), target.height())
    origin = target.mapTo(live, QPoint(0, 0))
    geometry = live.start_lights.geometry()
    assert geometry.x() == origin.x() + rect.x
    assert geometry.y() == origin.y() + rect.y
    assert geometry.width() == rect.width
    assert geometry.height() == rect.height
