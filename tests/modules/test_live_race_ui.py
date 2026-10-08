"""Live race view driven only by the simulation provider."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from slot_racing.core.clock import NANOS_PER_SECOND, format_duration
from slot_racing.core.domain import RaceStatus
from slot_racing.modules.races.ui.formatting import participant_status_key
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.results_view import ResultsView
from tests.modules.conftest import Env
from tests.modules.test_ui_management import column_text, open_page
from tests.support.start_sequence import release_start_lights


def test_participant_status_keeps_a_finisher_finished() -> None:
    assert (
        participant_status_key(finished=True, paused=False, ended=False)
        == "race.participant.finished"
    )
    assert (
        participant_status_key(finished=True, paused=True, ended=True)
        == "race.participant.finished"
    )
    assert (
        participant_status_key(finished=False, paused=True, ended=False)
        == "race.participant.waiting"
    )
    assert (
        participant_status_key(finished=False, paused=False, ended=True)
        == "race.participant.retired"
    )
    assert (
        participant_status_key(finished=False, paused=False, ended=False)
        == "race.participant.racing"
    )


def _ready_race(env: Env, laps: int) -> None:
    track = env.track("Heimbahn", lanes=2)
    zoe = env.driver("Zoe", number=7)
    anna = env.driver("Anna", number=3)
    porsche = env.vehicle("Porsche", driver_id=zoe.id)
    ferrari = env.vehicle("Ferrari", driver_id=anna.id)
    race = env.races.create_race("Finale", track.id, laps)
    env.races.add_participant(race.id, zoe.id, porsche.id, 1)
    env.races.add_participant(race.id, anna.id, ferrari.id, 2)


def test_simulated_race_updates_the_live_view_and_opens_results(qtbot: QtBot, env: Env) -> None:
    _ready_race(env, laps=2)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    assert column_text(page.table, 0, "Name") == "Finale"
    page.table.selectRow(0)
    page.buttons["start"].click()

    assert isinstance(page.current_view(), LiveRaceView)
    live = page.live
    release_start_lights(live)
    assert live.board.isHidden()
    assert not live.stage.isHidden()
    runner = live.runner
    assert runner is not None and runner.status is RaceStatus.RUNNING
    assert runner.snapshot().name == "Finale"
    assert runner.snapshot().track_name.endswith("Heimbahn")
    assert live.status_label.text().endswith("Läuft")
    assert runner.snapshot().timing_provider == "simulation"
    assert live.lanes.cards[1].lap_label.text().endswith("2")
    assert live.table.rowCount() == 2
    assert column_text(live.table, 0, "Fahrer") == "Zoe"
    assert column_text(live.table, 0, "Fahrzeug") == "Porsche 911"
    assert column_text(live.table, 0, "Startnummer") == "7"
    assert column_text(live.table, 0, "Spur") == "1"
    assert column_text(live.table, 0, "Runden") == "0"
    assert column_text(live.table, 0, "Status") == "Fährt"
    assert column_text(live.table, 1, "Fahrer") == "Anna"
    assert live.lanes.cards[1].driver_label.text() == "Zoe"
    assert live.lanes.cards[1].last_label.text() == "-"

    # One lap of the faster lane, delivered by the runner. The view redraws from the lap event
    # and does not need its timer.
    env.clock.advance(5 * NANOS_PER_SECOND)
    runner.tick()
    assert column_text(live.table, 0, "Fahrer") == "Zoe"
    assert column_text(live.table, 1, "Fahrer") == "Anna"
    assert column_text(live.table, 0, "Platz") == "1"
    assert column_text(live.table, 0, "Runden") == "1"
    assert column_text(live.table, 0, "Aktuelle Runde") == "2/2"
    assert column_text(live.table, 0, "Fortschritt") == "1/2"
    assert column_text(live.table, 1, "Runden") == "0"
    assert column_text(live.table, 1, "Fortschritt") == "0/2"
    last_lap = column_text(live.table, 0, "Letzte Runde")
    assert last_lap != "-"
    assert last_lap == column_text(live.table, 0, "Beste Runde")
    assert live.lanes.cards[1].last_label.text() == last_lap
    assert format_duration(runner.snapshot().rows[0].lap_times_ns[0]) == last_lap
    standings = [row.driver_label for row in runner.snapshot().rows]
    assert [column_text(live.table, index, "Fahrer") for index in range(2)] == standings

    live.table.selectRow(1)
    assert live.lanes.cards[2].driver_label.text() == "Anna"
    assert live.lanes.cards[2].start_label.text() == "3"
    assert live.lanes.cards[2].position_label.text() == "P2"
    assert live.lanes.cards[1].lane == 1

    runner.pause()
    assert live.status_label.text().endswith("Pausiert")
    assert column_text(live.table, 0, "Status") == "Wartet"
    assert column_text(live.table, 1, "Status") == "Wartet"
    env.clock.advance(10 * NANOS_PER_SECOND)
    runner.tick()
    assert column_text(live.table, 0, "Runden") == "1"
    assert column_text(live.table, 1, "Runden") == "0"

    assert not live.pause_button.isEnabled()
    assert live.resume_button.isEnabled()
    live.resume_button.click()
    assert live.status_label.text().endswith("Läuft")
    assert column_text(live.table, 1, "Status") == "Fährt"
    assert runner.snapshot().status is RaceStatus.RUNNING

    live.back_button.click()
    assert page.current_view() is page.list_page
    assert runner.is_active
    page.buttons["live"].click()
    assert page.current_view() is live

    for _ in range(120):
        if isinstance(page.current_view(), ResultsView):
            break
        env.clock.advance(NANOS_PER_SECOND)
        runner.tick()
    assert isinstance(page.current_view(), ResultsView)
    results = page.results
    stored = env.races.get_results(runner.race.id)
    assert [row.start_number for row in stored] == [7, 3]
    assert results.table.rowCount() == 2
    assert column_text(results.table, 0, "Platz") == "1"
    assert column_text(results.table, 0, "Fahrer") == "Zoe"
    assert column_text(results.table, 0, "Fahrzeug") == "Porsche 911"
    assert column_text(results.table, 0, "Startnummer") == "7"
    assert column_text(results.table, 0, "Runden") == "2"
    assert column_text(results.table, 0, "Status") == "Fertig"
    assert column_text(results.table, 0, "Gesamtzeit") != "-"
    assert column_text(results.table, 0, "Beste Runde") != "-"
    assert column_text(results.table, 1, "Fahrer") == "Anna"
    assert column_text(results.table, 1, "Startnummer") == "3"
    assert [column_text(results.table, index, "Platz") for index in range(2)] == [
        str(row.position) for row in stored
    ]

    results.back_button.click()
    assert page.current_view() is page.list_page
    assert cells_status(page) == ["Finale", "Heimbahn", "Beendet"]


def test_aborting_the_live_race_opens_the_stored_result(qtbot: QtBot, env: Env) -> None:
    _ready_race(env, laps=2)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    release_start_lights(live)
    assert live.runner is not None and live.runner.is_active

    live.confirm = lambda _text: False
    live.stop_button.click()
    assert live.runner.is_active
    assert live.status_label.text().endswith("Läuft")

    live.confirm = lambda _text: True
    live.stop_button.click()
    assert isinstance(page.current_view(), ResultsView)
    assert "Abgebrochen" in page.results.summary.text()
    assert column_text(page.results.table, 0, "Status") == "Ausgeschieden"
    assert column_text(page.results.table, 0, "Runden") == "0"
    assert column_text(page.results.table, 0, "Startnummer") == "7"
    assert env.races.require_race(live.runner.race.id).status is RaceStatus.ABORTED

    page.results.back_button.click()
    assert cells_status(page) == ["Finale", "Heimbahn", "Abgebrochen"]


def cells_status(page: RacesPage) -> list[str]:
    return [column_text(page.table, 0, header) for header in ("Name", "Strecke", "Status")]
