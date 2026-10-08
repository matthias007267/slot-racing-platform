"""Heat plans, postponement, disqualification and the live lane board."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QFrame, QLabel
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.core.errors import ValidationError
from slot_racing.modules.races.planning import build_rotation, plan_remaining, rotation_is_complete
from slot_racing.modules.races.runner import LiveRow
from slot_racing.modules.races.service import parse_duration_minutes
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.races.ui.formatting import lane_gaps
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.wizard import MODE, PARTICIPANTS
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page
from tests.support.start_sequence import release_start_lights


def test_rotation_gives_every_driver_every_lane_once() -> None:
    for drivers, lanes in ((2, 2), (2, 3), (2, 4), (4, 2), (5, 2), (3, 3), (1, 4), (4, 4)):
        heats = build_rotation(list(range(drivers)), lanes)
        assert rotation_is_complete(heats, list(range(drivers)), lanes)
        for heat in heats:
            assert len(heat) == min(drivers, lanes)
            assert len({lane for _driver, lane in heat}) == len(heat)


def test_more_drivers_than_lanes_need_more_than_one_heat() -> None:
    heats = build_rotation([1, 2, 3, 4, 5], 2)
    assert len(heats) == 5
    assert all(len(heat) == 2 for heat in heats)


def test_fewer_drivers_than_lanes_leave_a_lane_free() -> None:
    heats = build_rotation([1, 2, 3], 4)
    assert len(heats) == 4
    assert all(len(heat) == 3 for heat in heats)


def test_a_deferred_driver_is_planned_again_and_keeps_every_lane() -> None:
    obligations = [(driver, lane) for driver in (1, 2, 3) for lane in (1, 2)]
    heats = plan_remaining(obligations, 2, deferred=frozenset({2}))
    assert heats[0]
    assert all(driver != 2 for driver, _lane in heats[0])
    assert rotation_is_complete(heats, [1, 2, 3], 2)
    assert any(driver == 2 for heat in heats[1:] for driver, _lane in heat)


def test_dropping_a_driver_keeps_the_others_on_every_lane_and_may_leave_one_free() -> None:
    obligations = [(driver, lane) for driver in (1, 2) for lane in (1, 2, 3, 4)]
    heats = plan_remaining([pair for pair in obligations if pair[0] != 2], 4)
    assert rotation_is_complete(heats, [1], 4)
    assert all(len(heat) == 1 for heat in heats)


def test_two_occupied_lanes_only_report_the_gap_to_the_leader() -> None:
    gaps = lane_gaps((_live(1, 1, 8_000), _live(2, 2, 8_200)), by_best_lap=False)
    assert gaps[1].leader_ns == 0
    assert gaps[1].next_ns is None
    assert gaps[2].leader_ns == 200
    assert gaps[2].next_ns is None


def test_more_than_two_lanes_also_report_the_gap_to_the_next_driver() -> None:
    gaps = lane_gaps(
        (_live(1, 1, 8_000), _live(2, 3, 8_300), _live(3, 2, 8_100)),
        by_best_lap=False,
    )
    assert gaps[3].leader_ns == 100
    assert gaps[3].next_ns == 100
    assert gaps[2].leader_ns == 300
    assert gaps[2].next_ns == 200


def test_duration_accepts_whole_minutes_and_rejects_anything_else() -> None:
    assert parse_duration_minutes("5") == 5
    assert parse_duration_minutes(" 10 ") == 10
    for text in ("", "0", "-1", "5,5", "2.5", "fünf", "5 min"):
        with pytest.raises(ValidationError, match="duration_invalid"):
            parse_duration_minutes(text)


@pytest.mark.parametrize("lanes", [2, 3, 4])
def test_enrolling_drivers_plans_a_heat_for_every_lane(env: Env, lanes: int) -> None:
    race = _enrolled(env, lanes=lanes, drivers=lanes)
    plan = env.races.heat_plan(race.id)
    assert rotation_is_complete(
        [heat.seats for heat in plan],
        [participant.id for participant in race.participants],
        lanes,
    )
    assert env.races.require_race(race.id).participants[0].lane is None


def test_five_drivers_on_two_lanes_are_not_cut_down_to_one_heat(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=5)
    plan = env.races.heat_plan(race.id)
    assert len(plan) == 5
    assert rotation_is_complete(
        [heat.seats for heat in plan],
        [participant.id for participant in race.participants],
        2,
    )


def test_two_heats_combine_laps_and_rank_the_race(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=2, laps=1)
    _finish_heat(env, race.id)
    assert env.races.require_race(race.id).status is RaceStatus.READY
    _finish_heat(env, race.id)
    stored = env.races.require_race(race.id)
    assert stored.status is RaceStatus.FINISHED
    results = env.races.get_results(race.id)
    assert [row.position for row in results] == [1, 2]
    assert {row.laps_completed for row in results} == {2}


def test_postponing_a_driver_keeps_them_in_a_later_heat(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=3)
    held = race.participants[0]
    briefing = env.races.postpone_driver(race.id, held.id)
    assert briefing is not None
    seated = {seat.participant_id for seat in briefing.seats if seat.participant_id is not None}
    assert held.id not in seated
    later = [
        heat
        for heat in env.races.heat_plan(race.id)
        if any(participant_id == held.id for participant_id, _lane in heat.seats)
    ]
    assert later
    assert rotation_is_complete(
        [heat.seats for heat in env.races.heat_plan(race.id)],
        [participant.id for participant in race.participants],
        2,
    )


def test_disqualification_keeps_results_and_frees_a_lane(env: Env) -> None:
    race = _enrolled(env, lanes=4, drivers=4, laps=1)
    runner = env.controller.start_race(race.id)
    for _ in range(30):
        if not runner.is_active:
            break
        env.clock.advance(1_000_000_000)
        runner.tick()
    assert env.races.require_race(race.id).status is RaceStatus.READY
    assert env.races.get_laps(race.id)
    briefing = env.races.heat_briefing(race.id)
    assert briefing is not None
    removed = briefing.drivers[-1][0]
    assert not env.races.disqualify_driver(race.id, removed)
    assert env.races.get_laps(race.id)
    again = env.races.heat_briefing(race.id)
    assert again is not None
    assert any(seat.participant_id is None for seat in again.seats)
    remaining = [item[0] for item in again.drivers]
    assert removed not in remaining
    covered = [
        tuple(pair for pair in heat.seats if pair[0] in set(remaining))
        for heat in env.races.heat_plan(race.id)
    ]
    assert rotation_is_complete([heat for heat in covered if heat], remaining, 4)


def test_a_time_trial_stores_and_uses_its_duration(env: Env) -> None:
    track = env.track(lanes=2)
    race = env.races.create_time_trial("Training", track.id, duration_minutes=1)
    assert race.duration_minutes == 1
    driver, vehicle = env.pair(1)
    env.races.add_participant(race.id, driver, vehicle, 1)
    runner = env.controller.start_race(race.id)
    env.clock.advance(59_000_000_000)
    runner.tick()
    assert runner.is_active
    env.clock.advance(1_000_000_000)
    runner.tick()
    assert runner.is_finished
    assert env.races.require_race(race.id).status is RaceStatus.FINISHED


def test_each_time_trial_heat_lasts_the_configured_minutes(env: Env) -> None:
    track = env.track(lanes=2)
    race = env.races.create_time_trial("Qualifying", track.id, duration_minutes=1)
    for index in (1, 2):
        driver, vehicle = env.pair(index)
        env.races.enroll_driver(race.id, driver, vehicle)
    env.races.plan_heats(race.id)
    runner = env.controller.start_race(race.id)
    env.clock.advance(60_000_000_000)
    runner.tick()
    assert runner.is_finished
    stored = env.races.require_race(race.id)
    assert stored.status is RaceStatus.READY
    assert stored.duration_minutes == 1
    assert env.races.has_pending_heats(race.id)


def test_a_time_trial_without_a_duration_stays_open(env: Env) -> None:
    track = env.track(lanes=2)
    race = env.races.create_time_trial("Training", track.id)
    assert race.duration_minutes is None
    driver, vehicle = env.pair(1)
    env.races.add_participant(race.id, driver, vehicle, 1)
    runner = env.controller.start_race(race.id)
    env.clock.advance(120_000_000_000)
    runner.tick()
    assert runner.is_active


def test_the_wizard_uses_a_free_text_duration_and_no_lane_picker(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    anna = env.driver("Anna")
    env.vehicle("Porsche", driver_id=anna.id)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    wizard.name_edit.setText("Qualifying")
    assert wizard.go_next()
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    assert wizard.step == MODE
    wizard.mode_combo.setCurrentIndex(wizard.mode_combo.findData("time_trial"))
    assert wizard.duration_edit.text() == "5"
    assert not wizard.duration_edit.isHidden()
    wizard.duration_edit.setText("5,5")
    assert not wizard.go_next()
    assert "Minuten" in wizard.status.text()
    wizard.duration_edit.setText("10")
    assert wizard.go_next()
    assert wizard.step == PARTICIPANTS
    assert not wizard.lane_combo.isVisible()
    wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(anna.id))
    assert wizard.add_participant()
    assert wizard.go_next()
    overview = wizard.overview_label.text()
    assert "Anna mit Porsche 911" in overview
    assert "10 Minuten" in overview
    stored = env.races.require_race(wizard.race.id) if wizard.race is not None else None
    assert stored is not None
    assert stored.mode is RaceMode.TIME_TRIAL
    assert stored.duration_minutes == 10
    assert stored.participants[0].lane is None


@pytest.mark.parametrize("lanes", [2, 3, 4])
def test_the_live_board_has_one_column_per_lane(qtbot: QtBot, env: Env, lanes: int) -> None:
    track = env.track("Heimbahn", lanes=lanes)
    race = env.races.create_race("Finale", track.id, 3)
    for lane in range(1, lanes + 1):
        driver, vehicle = env.pair(lane)
        env.races.add_participant(race.id, driver, vehicle, lane)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    assert isinstance(live, LiveRaceView)
    release_start_lights(live)
    assert not live.stage.isHidden()
    labels = [live.findChild(QFrame, f"live-lane-{lane}") for lane in range(1, lanes + 1)]
    assert all(label is not None for label in labels)
    assert live.findChild(QFrame, f"live-lane-{lanes + 1}") is None
    name = live.findChild(QLabel, "live-lane-1-driver")
    assert name is not None and name.text()
    env.clock.advance(8_000_000_000)
    assert live.runner is not None
    live.runner.tick()
    live.refresh()
    last = live.findChild(QLabel, "live-lane-1-last")
    best = live.findChild(QLabel, "live-lane-1-best")
    lap = live.findChild(QLabel, "live-lane-1-lap")
    position = live.findChild(QLabel, "live-lane-1-position")
    assert last is not None and last.text() != "-"
    assert best is not None and best.text() != "-"
    assert lap is not None and "/" in lap.text()
    assert position is not None and position.text().startswith("P")
    assert live.findChild(QLabel, "live-lane-1-gap") is None
    other = live.findChild(QLabel, "live-lane-2-position")
    assert other is not None and other.text().startswith("P")
    assert other.text() != position.text()


def test_postponing_and_disqualifying_from_the_waiting_window(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    names = ["Anna", "Ben", "Chris"]
    drivers = [env.driver(name) for name in names]
    for driver in drivers:
        env.vehicle("Porsche", driver_id=driver.id)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    wizard.name_edit.setText("Finale")
    assert wizard.go_next()
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    wizard.laps_spin.setValue(1)
    assert wizard.go_next()
    for driver in drivers:
        wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(driver.id))
        assert wizard.add_participant()
    assert wizard.go_next() and wizard.go_next()
    assert "Bahn 1:" in wizard.ready_label.text()
    wizard.start_button.click()
    live = page.live
    release_start_lights(live)
    for _ in range(80):
        env.clock.advance(100_000_000)
        live.refresh()
        if not live.heat_gate.isHidden():
            break
    assert not live.heat_gate.isHidden()
    waiting = live.heat_gate.driver_combo.currentText()
    live.heat_gate.postpone_button.click()
    assert waiting not in {
        line.split(": ", 1)[1]
        for line in live.heat_gate.body_label.text().splitlines()
        if line.startswith("Bahn")
    }
    live.confirm = lambda _text: True
    live.heat_gate.disqualify_button.click()
    assert env.races.require_race(wizard.race.id).participants  # type: ignore[union-attr]
    seated = [
        line for line in live.heat_gate.body_label.text().splitlines() if line.startswith("Bahn")
    ]
    assert any(line.endswith("frei") for line in seated) or len(seated) == 2


def _enrolled(env: Env, *, lanes: int, drivers: int, laps: int = 2) -> RaceInfo:
    track = env.track(lanes=lanes)
    race = env.races.create_race("Finale", track.id, laps)
    for index in range(1, drivers + 1):
        driver, vehicle = env.pair(index)
        env.races.enroll_driver(race.id, driver, vehicle)
    env.races.plan_heats(race.id)
    return env.races.require_race(race.id)


def _finish_heat(env: Env, race_id: RaceId) -> None:
    runner = env.controller.start_race(race_id)
    for _ in range(30):
        if not runner.is_active:
            break
        env.clock.advance(1_000_000_000)
        runner.tick()
    assert not runner.is_active


def _live(lane: int, position: int, total: int) -> LiveRow:
    return LiveRow(
        position=position,
        lane=lane,
        driver_label=f"Driver {lane}",
        vehicle_label="Car",
        start_number=None,
        current_lap=1,
        laps_completed=1,
        last_lap_ns=total,
        total_time_ns=total,
        best_lap_ns=total,
        finished=False,
        lap_times_ns=(total,),
    )
