"""Restarting an aborted race opens a new one and keeps the aborted history."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.modules.races.models import Lap, Race
from slot_racing.modules.races.types import HeatInfo, RaceInfo
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.track_planner.document import STRAIGHT_H, add_piece
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.tracks.models import Track
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
    assert restarted.name == "Finale (2)"
    assert restarted.name != aborted.name
    assert (restarted.track_id, restarted.mode, restarted.laps, restarted.repetitions) == (
        aborted.track_id,
        RaceMode.LAPS,
        8,
        1,
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


def test_a_finished_race_restarts_as_a_new_race_and_keeps_the_result(env: Env) -> None:
    race = env.races.create_race("Ziel", env.track_id(), 1)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    runner = env.controller.start_race(race.id)
    drive_until_done(env, runner)
    finished = env.races.require_race(race.id)
    assert finished.status is RaceStatus.FINISHED
    results = env.races.get_results(race.id)
    restarted = env.races.restart_aborted(race.id)
    assert restarted.id != finished.id
    assert restarted.name == "Ziel (2)"
    assert restarted.status is RaceStatus.READY
    assert env.races.get_laps(restarted.id) == []
    assert env.races.list_time_measurements(race_id=restarted.id) == []
    assert [row.laps_completed for row in env.races.get_results(restarted.id)] == [0]
    stored = env.races.require_race(race.id)
    assert stored.status is RaceStatus.FINISHED
    assert [row.laps_completed for row in env.races.get_results(race.id)] == [
        row.laps_completed for row in results
    ]


def test_an_open_or_running_race_cannot_be_restarted(env: Env) -> None:
    race = env.races.create_race("Offen", env.track_id(), 2)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    with pytest.raises(ValidationError) as caught:
        env.races.restart_aborted(race.id)
    assert key_of(caught) == "error.race.not_restartable"
    runner = env.controller.start_race(race.id)
    with pytest.raises(ValidationError) as running:
        env.races.restart_aborted(race.id)
    assert key_of(running) == "error.race.not_restartable"
    runner.stop()
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
    assert page.buttons["restart"].text() == "Rennen neu starten"

    waiting = env.races.create_race("Wartet", track.id, 3)
    page.refresh()
    _select_status(page, "Angelegt")
    assert page.buttons["restart"].isHidden()
    assert page.buttons["start"].isEnabled()

    _select_status(page, "Beendet")
    assert not page.buttons["restart"].isHidden()
    assert page.buttons["delete"].isEnabled()
    assert not page.buttons["start"].isEnabled()
    page.buttons["restart"].click()
    finished_dialog = page.restart_dialog
    assert finished_dialog is not None
    assert finished_dialog.name_edit.text() == "Ziel (2)"
    assert finished_dialog.name_edit.text() != "Ziel"
    before_cancel = [stored.id for stored in env.races.list_races()]
    finished_dialog.cancel_button.click()
    assert [stored.id for stored in env.races.list_races()] == before_cancel

    _select_status(page, "Abgebrochen")
    assert not page.buttons["restart"].isHidden()
    assert page.buttons["delete"].isEnabled()
    assert not page.buttons["start"].isEnabled()

    page.buttons["restart"].click()
    dialog = page.restart_dialog
    assert dialog is not None
    assert dialog.name_edit.text() == "Abbruch (2)"
    dialog.cancel_button.click()
    assert env.races.require_race(aborted.id).status is RaceStatus.ABORTED
    assert [stored.id for stored in env.races.list_races()] == before_cancel

    page.buttons["restart"].click()
    dialog = page.restart_dialog
    assert dialog is not None
    dialog.create_button.click()
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
    assert env.races.require_race(waiting.id).status is RaceStatus.CREATED


def test_repetitions_default_to_one_and_a_chosen_value_survives_the_dialog(
    qtbot: QtBot, env: Env
) -> None:
    track = env.track("Heimbahn")
    window, page = open_page(qtbot, env, "races")
    window.show()
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    assert wizard.repetitions_caption.text() == "Wiederholungen"
    assert "Wiederholen" not in wizard.repetitions_hint.text().split()
    assert "Wiederholungen" in wizard.repetitions_hint.text().split()
    assert wizard.repetitions_spin.value() == 1
    wizard.name_edit.setText("Abend")
    assert wizard.go_next()
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    wizard.repetitions_spin.setValue(4)
    assert wizard.go_next()
    stored = wizard.race
    assert stored is not None and stored.repetitions == 4
    wizard.repetitions_spin.setValue(9)
    wizard.begin(stored.id)
    assert wizard.repetitions_spin.value() == 4
    wizard.begin(None)
    assert wizard.repetitions_spin.value() == 1


def test_an_invalid_lane_is_reported_and_does_not_create_a_race(env: Env) -> None:
    track = env.track("Schmal", lanes=2)
    race = env.races.create_race("Eng", track.id, 2)
    first_driver, first_vehicle = env.pair(1)
    second_driver, second_vehicle = env.pair(2)
    env.races.add_participant(race.id, first_driver, first_vehicle, 1)
    env.races.add_participant(race.id, second_driver, second_vehicle, 2)
    env.controller.start_race(race.id).stop()
    with env.runtime.database.session() as session:
        stored_track = session.get(Track, int(track.id))
        assert stored_track is not None
        stored_track.lane_count = 1
    preview = env.races.restart_preview(race.id)
    invalid = [seat for seat in preview.seats if seat.issue_key is not None]
    assert [seat.lane for seat in invalid] == [2]
    assert invalid[0].issue_key == "error.race.lane_invalid"
    with pytest.raises(ValidationError) as caught:
        env.races.restart_aborted(race.id)
    assert key_of(caught) == "error.race.lane_invalid"
    assert [stored.id for stored in env.races.list_races()] == [race.id]
    with pytest.raises(ValidationError) as same_name:
        env.races.restart_aborted(race.id, name="Eng")
    assert key_of(same_name) == "error.race.restart_name"


def test_restart_copies_the_source_layout_and_drops_excluded_laps(env: Env) -> None:
    track = env.track("Plan", lanes=2)
    planner = env.runtime.services.get(TrackPlannerService)
    planner.save(add_piece(planner.load(track.id), STRAIGHT_H, 0, 0))
    race = env.races.create_race("Layout", track.id, 20, repetitions=3)
    driver_id, vehicle_id = env.pair(1)
    other_driver, other_vehicle = env.pair(2)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    env.races.add_participant(race.id, other_driver, other_vehicle, 2)
    once = env.races.plan_heats(race.id)
    runner = env.controller.start_race(race.id)
    for _ in range(130):
        env.clock.advance(STEP_NS)
        runner.tick()
    runner.stop()
    with env.runtime.database.session() as session:
        source = session.get(Race, int(race.id))
        assert source is not None
        source_layout = source.track_layout_id
        lap = session.scalar(select(Lap).where(Lap.race_id == int(race.id)))
        assert lap is not None
        lap_id = int(lap.id)
    assert source_layout is not None
    env.races.set_lap_excluded(lap_id, excluded=True)
    planner.save(add_piece(planner.load(track.id), STRAIGHT_H, 4, 0))
    assert env.tracks.current_layout_id(track.id) != source_layout
    restarted = env.races.restart_aborted(race.id)
    assert restarted.repetitions == 3
    assert restarted.name == "Layout (2)"
    fresh = env.races.heat_plan(restarted.id)
    assert [item.status for item in fresh] == ["planned"] * len(once)
    assert len(fresh) == len(once)
    with env.runtime.database.session() as session:
        copied = session.get(Race, int(restarted.id))
        marked = session.get(Lap, lap_id)
        assert copied is not None and marked is not None
        assert copied.track_layout_id == source_layout
        assert marked.statistics_excluded is True
        assert session.scalar(select(Lap).where(Lap.race_id == int(restarted.id))) is None
    assert env.races.require_race(race.id).status is RaceStatus.ABORTED


def test_an_inactive_driver_must_be_replaced_before_the_restart(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    zoe = env.driver("Zoe")
    chris = env.driver("Chris")
    porsche = env.vehicle("Porsche", driver_id=zoe.id)
    env.vehicle("BMW", driver_id=chris.id)
    race = env.races.create_race("Alt", track.id, 2)
    env.races.add_participant(race.id, zoe.id, porsche.id, 1)
    env.controller.start_race(race.id).stop()
    env.drivers.set_active(zoe.id, False)
    window, page = open_page(qtbot, env, "races")
    window.show()
    assert isinstance(page, RacesPage)
    page.refresh()
    _select_status(page, "Abgebrochen")
    page.buttons["restart"].click()
    dialog = page.restart_dialog
    assert dialog is not None
    problem = dialog.findChild(QLabel, "restart-problem-0")
    assert problem is not None
    assert "Zoe" in problem.text()
    assert "deaktiviert" in problem.text()
    dialog.create_button.click()
    assert [stored.id for stored in env.races.list_races()] == [race.id]
    assert "korrigieren" in dialog.status.text()
    combo = dialog.findChild(QComboBox, "restart-driver-0")
    assert combo is not None
    combo.setCurrentIndex(combo.findData(chris.id))
    dialog.create_button.click()
    created = [stored for stored in env.races.list_races() if stored.id != race.id]
    assert len(created) == 1
    assert created[0].name == "Alt (2)"
    assert [(row.driver_id, row.vehicle_id, row.lane) for row in created[0].participants] == [
        (chris.id, porsche.id, 1)
    ]
    assert env.races.get_laps(created[0].id) == []
    assert env.races.require_race(race.id).status is RaceStatus.ABORTED


def test_existing_races_gain_one_repetition_without_losing_their_laps() -> None:
    database = Database.in_memory()
    database.migrate("0021")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO races (name, status, mode, target_laps, timing_provider) "
            "VALUES ('Alt', 'finished', 'laps', 8, 'simulation')"
        )
    database.migrate()
    with database.engine.connect() as connection:
        stored = connection.exec_driver_sql("SELECT target_laps, repetitions FROM races").one()
    assert stored == (8, 1)
    assert database.schema_revision() == "0022"
    database.dispose()


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
