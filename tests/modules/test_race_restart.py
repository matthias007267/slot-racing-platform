"""Restarting an aborted race opens a new one and keeps the aborted history."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.core.errors import ValidationError
from slot_racing.modules.races.types import HeatInfo, RaceInfo
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.uikit.widgets import ID_ROLE
from tests.modules.conftest import Env
from tests.modules.test_race_flow import drive_until_done
from tests.modules.test_races import key_of
from tests.modules.test_ui_management import column_text, open_page
from tests.support.start_sequence import release_start_lights

STEP_NS = 100_000_000


def test_restart_copies_the_race_and_leaves_the_aborted_results(env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    zoe = env.driver("Zoe", number=7)
    anna = env.driver("Anna", number=3)
    porsche = env.vehicle("Porsche", driver_id=zoe.id)
    ferrari = env.vehicle("Ferrari", driver_id=anna.id)
    race = env.races.create_race("Finale", track.id, 8, "simulation")
    env.races.add_participant(race.id, zoe.id, porsche.id, 1)
    env.races.add_participant(race.id, anna.id, ferrari.id, 2)
    runner = env.controller.start_race(race.id)
    for _ in range(130):
        env.clock.advance(STEP_NS)
        runner.tick()
    runner.stop()

    aborted = env.races.require_race(race.id)
    assert aborted.status is RaceStatus.ABORTED
    aborted_results = env.races.get_results(race.id)
    assert any(row.laps_completed > 0 for row in aborted_results)
    assert env.races.get_laps(race.id)

    restarted = env.races.restart_aborted(race.id)
    assert restarted.id != aborted.id
    assert restarted.status is RaceStatus.READY
    assert restarted.started_at is None and restarted.finished_at is None
    assert (restarted.name, restarted.track_id, restarted.mode, restarted.laps) == (
        aborted.name,
        aborted.track_id,
        RaceMode.LAPS,
        8,
    )
    assert restarted.timing_provider == "simulation"
    assert restarted.duration_minutes is None
    copied = [
        (row.driver_id, row.vehicle_id, row.lane, row.disqualified)
        for row in restarted.participants
    ]
    previous = [(row.driver_id, row.vehicle_id, row.lane, False) for row in aborted.participants]
    assert copied == previous
    assert env.races.get_laps(restarted.id) == []
    fresh = env.races.get_results(restarted.id)
    assert [row.laps_completed for row in fresh] == [0, 0]
    assert all(row.total_time_ns is None and row.best_lap_ns is None for row in fresh)
    assert env.races.list_time_measurements(race_id=restarted.id) == []

    stored = env.races.require_race(race.id)
    assert stored.status is RaceStatus.ABORTED
    assert stored.id == aborted.id
    assert [row.laps_completed for row in env.races.get_results(race.id)] == [
        row.laps_completed for row in aborted_results
    ]

    started = env.controller.start_race(restarted.id)
    assert env.races.require_race(restarted.id).status is RaceStatus.RUNNING
    started.stop()
    assert env.races.require_race(restarted.id).status is RaceStatus.ABORTED
    assert env.races.require_race(race.id).status is RaceStatus.ABORTED


def test_restart_keeps_a_time_trial_and_builds_a_fresh_heat_plan(env: Env) -> None:
    track = env.track("Oval", lanes=2)
    trial = env.races.create_time_trial("Zeit", track.id, "simulation", duration_minutes=12)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(trial.id, driver_id, vehicle_id, 2)
    runner = env.controller.start_race(trial.id)
    for _ in range(130):
        env.clock.advance(STEP_NS)
        runner.tick()
    env.races.abort_race(trial.id)
    runner.close()
    env.controller.active = None
    assert env.races.list_time_measurements(race_id=trial.id)
    restarted = env.races.restart_aborted(trial.id)
    assert restarted.mode is RaceMode.TIME_TRIAL
    assert restarted.duration_minutes == 12
    assert restarted.laps == 0
    assert restarted.timing_provider == "simulation"
    assert [(row.driver_id, row.lane) for row in restarted.participants] == [(driver_id, 2)]
    assert env.races.list_time_measurements(race_id=restarted.id) == []
    assert env.races.require_race(trial.id).status is RaceStatus.ABORTED

    heat = env.races.create_race("Läufe", track.id, 4, "simulation")
    first_driver, first_vehicle = env.pair(2)
    second_driver, second_vehicle = env.pair(3)
    env.races.add_participant(heat.id, first_driver, first_vehicle, 1)
    env.races.add_participant(heat.id, second_driver, second_vehicle, 2)
    ready = env.races.require_race(heat.id)
    before = env.races.plan_heats(heat.id)
    runner = env.controller.start_race(heat.id)
    runner.stop()
    assert "completed" in [item.status for item in env.races.heat_plan(heat.id)]
    again = env.races.restart_aborted(heat.id)
    fresh = env.races.heat_plan(again.id)
    assert [item.status for item in fresh] == ["planned"] * len(before)
    assert _seat_labels(ready, before) == _seat_labels(again, fresh)
    assert all(row.lane is None for row in again.participants)
    assert all(row.laps_completed == 0 for row in env.races.get_results(again.id))
    started = env.controller.start_race(again.id)
    assert env.races.require_race(again.id).status is RaceStatus.RUNNING
    started.stop()
    assert env.races.require_race(heat.id).status is RaceStatus.ABORTED


def test_a_finished_race_cannot_be_restarted(env: Env) -> None:
    race = env.races.create_race("Ziel", env.track_id(), 1)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    runner = env.controller.start_race(race.id)
    drive_until_done(env, runner)
    assert env.races.require_race(race.id).status is RaceStatus.FINISHED
    with pytest.raises(ValidationError) as caught:
        env.races.restart_aborted(race.id)
    assert key_of(caught) == "error.race.not_restartable"
    assert [stored.id for stored in env.races.list_races()] == [race.id]


def test_restart_button_is_only_offered_for_an_aborted_race(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    zoe = env.driver("Zoe", number=7)
    anna = env.driver("Anna", number=3)
    porsche = env.vehicle("Porsche", driver_id=zoe.id)
    ferrari = env.vehicle("Ferrari", driver_id=anna.id)
    finished = env.races.create_race("Ziel", track.id, 1)
    env.races.add_participant(finished.id, zoe.id, porsche.id, 1)
    env.races.add_participant(finished.id, anna.id, ferrari.id, 2)
    drive_until_done(env, env.controller.start_race(finished.id))

    aborted = env.races.create_race("Abbruch", track.id, 6, "simulation")
    env.races.add_participant(aborted.id, zoe.id, porsche.id, 1)
    env.races.add_participant(aborted.id, anna.id, ferrari.id, 2)
    env.controller.start_race(aborted.id).stop()
    original_laps = env.races.get_laps(aborted.id)

    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    page.refresh()
    assert [page.buttons[key].objectName() for key in page.buttons][2:5] == [
        "races-start",
        "races-restart",
        "races-live",
    ]
    assert page.buttons["restart"].text() == "Rennen Neustart"

    _select_status(page, "Beendet")
    assert page.buttons["restart"].isHidden()
    assert page.buttons["delete"].isEnabled()
    assert not page.buttons["start"].isEnabled()

    _select_status(page, "Abgebrochen")
    assert page.buttons["restart"].isVisible()
    assert page.buttons["delete"].isEnabled()
    assert not page.buttons["start"].isEnabled()

    page.buttons["restart"].click()
    assert env.races.require_race(aborted.id).status is RaceStatus.ABORTED
    assert env.races.get_laps(aborted.id) == original_laps
    selected = page.table.item(page.table.currentRow(), 0)
    assert selected is not None
    selected_id = selected.data(ID_ROLE)
    assert isinstance(selected_id, int)
    restarted = env.races.require_race(RaceId(selected_id))
    assert restarted.id != aborted.id
    assert restarted.status is RaceStatus.READY
    assert restarted.mode is RaceMode.LAPS
    assert restarted.laps == 6
    assert restarted.timing_provider == "simulation"
    assert [(row.driver_id, row.vehicle_id, row.lane) for row in restarted.participants] == [
        (zoe.id, porsche.id, 1),
        (anna.id, ferrari.id, 2),
    ]
    assert env.races.get_laps(restarted.id) == []
    assert page.buttons["restart"].isHidden()

    page.buttons["start"].click()
    assert isinstance(page.current_view(), LiveRaceView)
    live = page.live
    release_start_lights(live)
    assert live.runner is not None and live.runner.status is RaceStatus.RUNNING
    assert live.runner.race.id == restarted.id
    live.runner.stop()


def _select_status(page: RacesPage, status: str) -> None:
    for row in range(page.table.rowCount()):
        if column_text(page.table, row, "Status") == status:
            page.table.selectRow(row)
            return
    raise AssertionError(status)


def _seat_labels(race: RaceInfo, plan: list[HeatInfo]) -> list[tuple[tuple[str, int], ...]]:
    names = {participant.id: participant.driver_label for participant in race.participants}
    return [
        tuple(sorted((names[participant_id], lane) for participant_id, lane in heat.seats))
        for heat in plan
    ]
