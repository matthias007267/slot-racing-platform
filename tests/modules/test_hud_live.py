"""The live HUD follows a simulated race without a camera."""

from __future__ import annotations

from dataclasses import replace

from pytestqt.qtbot import QtBot

from slot_racing.core.clock import NANOS_PER_SECOND, format_duration
from slot_racing.core.domain import RaceStatus
from slot_racing.modules.races.hud import RACE_CLOCK, HudConfigurationStore, with_widget
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

    assert live.header.isHidden()
    assert live.clock.isVisibleTo(live)
    assert live.status.isVisibleTo(live)
    assert live.ranking.isVisibleTo(live)
    assert live.status_label.text() == "● Läuft"
    assert live.messages.message_label.text() == "Rennen gestartet"
    assert live.laps_label.text() == "1 / 2"
    assert live.time_label.text() == "0:00.000"
    assert column_text(live.table, 0, "Fahrer") == "Zoe"
    assert live.last_lap.value_label.text() == "—"
    assert live.best_lap.value_label.text() == "—"

    env.clock.advance(3 * NANOS_PER_SECOND)
    live.refresh()
    assert live.time_label.text() == format_duration(runner.snapshot().elapsed_ns)
    assert live.time_label.text() == "0:03.000"

    env.clock.advance(2 * NANOS_PER_SECOND)
    live.refresh()
    assert column_text(live.table, 0, "Fahrer") == "Zoe"
    assert column_text(live.table, 0, "Runden") == "1"
    assert column_text(live.table, 0, "Platz") == "1"
    assert live.laps_label.text() == "2 / 2"
    last = column_text(live.table, 0, "Letzte Runde")
    assert last != "-"
    assert last in live.last_lap.value_label.text()
    assert "Zoe" in live.last_lap.value_label.text()
    assert last in live.best_lap.value_label.text()
    assert live.progress.progress.value() == 1

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
    assert "Zoe" in live.best_lap.value_label.text()


def test_a_saved_layout_does_not_hide_the_fixed_live_clock(qtbot: QtBot, env: Env) -> None:
    store = env.runtime.services.get(HudConfigurationStore)
    config = store.load()
    clock = config.widget(RACE_CLOCK)
    assert clock is not None
    store.save(with_widget(config, replace(clock, visible=False)))

    _ready_race(env, laps=2)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    release_start_lights(live)
    assert live.clock.isVisibleTo(live)
    assert live.header.isHidden()
    assert live.time_label.text() != ""

    shown = store.load()
    clock = shown.widget(RACE_CLOCK)
    assert clock is not None
    store.save(with_widget(shown, replace(clock, visible=True)))
    assert live.clock.isVisibleTo(live)
