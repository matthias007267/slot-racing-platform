"""Heat plans, postponement, disqualification and the live lane board."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QFrame, QLabel
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.core.errors import ValidationError
from slot_racing.modules.races.models import Race
from slot_racing.modules.races.planning import (
    build_rotation,
    effective_lane_count,
    pairwise_cycle,
    pairwise_heats,
    pairwise_period,
    plan_remaining,
    rotation_is_complete,
)
from slot_racing.modules.races.runner import LiveRow
from slot_racing.modules.races.service import RaceService, parse_duration_minutes
from slot_racing.modules.races.types import HeatBriefing, HeatSeat, LaneChange, RaceInfo
from slot_racing.modules.races.ui.formatting import lane_gaps
from slot_racing.modules.races.ui.heat_gate import HeatGate
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.wizard import MODE, PARTICIPANTS
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page
from tests.support.start_sequence import release_start_lights


def test_rotation_gives_every_driver_every_lane_once() -> None:
    cases = (
        (2, 3),
        (2, 4),
        (5, 2),
        (3, 3),
        (1, 4),
        (4, 4),
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
        (4, 1),
        (4, 2),
        (6, 2),
        (8, 2),
    )
    for drivers, lanes in cases:
        heats = build_rotation(list(range(drivers)), lanes)
        assert rotation_is_complete(heats, list(range(drivers)), lanes)
        for heat in heats:
            assert len(heat) == min(drivers, lanes)
            assert len({lane for _driver, lane in heat}) == len(heat)


def test_two_lanes_pair_drivers_and_then_repeat() -> None:
    expected = {
        2: (
            ((1, 1), (2, 2)),
            ((2, 1), (1, 2)),
        ),
        3: (
            ((1, 1), (2, 2)),
            ((3, 1), (1, 2)),
            ((2, 1), (3, 2)),
        ),
        4: (
            ((1, 1), (2, 2)),
            ((3, 1), (4, 2)),
            ((2, 1), (1, 2)),
            ((4, 1), (3, 2)),
        ),
        5: (
            ((1, 1), (2, 2)),
            ((3, 1), (4, 2)),
            ((5, 1), (1, 2)),
            ((2, 1), (3, 2)),
            ((4, 1), (5, 2)),
        ),
        6: (
            ((1, 1), (2, 2)),
            ((3, 1), (4, 2)),
            ((5, 1), (6, 2)),
            ((2, 1), (1, 2)),
            ((4, 1), (3, 2)),
            ((6, 1), (5, 2)),
        ),
        7: (
            ((1, 1), (2, 2)),
            ((3, 1), (4, 2)),
            ((5, 1), (6, 2)),
            ((7, 1), (1, 2)),
            ((2, 1), (3, 2)),
            ((4, 1), (5, 2)),
            ((6, 1), (7, 2)),
        ),
    }
    for count, cycle in expected.items():
        drivers = list(range(1, count + 1))
        period = count if count % 2 else count // 2
        assert pairwise_period(count) == period
        assert len(cycle) == (period if count % 2 else period * 2)
        assert pairwise_cycle(drivers) == cycle
        assert build_rotation(drivers, 2) == cycle
        assert rotation_is_complete(cycle, drivers, 2)
        first = pairwise_heats(drivers, period)
        assert cycle[:period] == first
        repeated = pairwise_heats(drivers, period * 2)
        assert repeated[:period] == first
        assert repeated[period:] == first
        for heat in cycle:
            assert [lane for _driver, lane in heat] == [1, 2]
        for lane in (1, 2):
            seated = sorted(driver for heat in cycle for driver, used in heat if used == lane)
            assert seated == drivers


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
    ids = [participant.id for participant in race.participants]
    assert rotation_is_complete([heat.seats for heat in plan], ids, lanes)
    if lanes == 2:
        assert len(plan) == 2
    assert env.races.require_race(race.id).participants[0].lane is None


def test_five_drivers_on_two_lanes_are_not_cut_down_to_one_heat(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=5)
    plan = env.races.heat_plan(race.id)
    ids = [participant.id for participant in race.participants]
    assert [heat.seats for heat in plan] == list(pairwise_cycle(ids))
    assert len(plan) == 5
    assert rotation_is_complete([heat.seats for heat in plan], ids, 2)


def test_the_next_heat_continues_the_pairwise_cycle(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=6, laps=1)
    ids = [participant.id for participant in race.participants]
    _finish_heat(env, race.id)
    assert env.races.require_race(race.id).status is RaceStatus.READY
    stored = env.races.heat_plan(race.id)
    planned = [heat for heat in stored if heat.status == "planned"]
    completed = [heat for heat in stored if heat.status == "completed"]
    assert planned[0].seats == ((ids[2], 1), (ids[3], 2))
    assert [heat.seats for heat in planned] == list(pairwise_cycle(ids)[1:])
    assert rotation_is_complete(
        [heat.seats for heat in completed] + [heat.seats for heat in planned], ids, 2
    )


def test_adding_and_removing_drivers_rebuilds_the_pairs(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=4)
    ids = [participant.id for participant in race.participants]
    assert [heat.seats for heat in env.races.heat_plan(race.id)] == list(pairwise_cycle(ids))
    removed = ids[-1]
    env.races.remove_participant(race.id, removed)
    shortened = ids[:-1]
    assert [heat.seats for heat in env.races.heat_plan(race.id)] == list(pairwise_cycle(shortened))
    added, vehicle = env.pair(9)
    env.races.enroll_driver(race.id, added, vehicle)
    current = env.races.require_race(race.id)
    assert [participant.driver_id for participant in current.participants][-1] == added
    restored = [participant.id for participant in current.participants]
    assert [heat.seats for heat in env.races.heat_plan(race.id)] == list(pairwise_cycle(restored))
    assert rotation_is_complete([heat.seats for heat in env.races.heat_plan(race.id)], restored, 2)


def test_two_heats_combine_laps_and_rank_the_race(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=3, laps=1)
    _finish_heat(env, race.id)
    assert env.races.require_race(race.id).status is RaceStatus.READY
    _finish_heat(env, race.id)
    _finish_heat(env, race.id)
    stored = env.races.require_race(race.id)
    assert stored.status is RaceStatus.FINISHED
    results = env.races.get_results(race.id)
    assert [row.position for row in results] == [1, 2, 3]
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
    for index in (1, 2, 3, 4):
        driver, vehicle = env.pair(index)
        env.races.enroll_driver(race.id, driver, vehicle)
    env.races.plan_heats(race.id)
    ids = [participant.id for participant in env.races.require_race(race.id).participants]
    plan = env.races.heat_plan(race.id)
    assert len(plan) == 4
    assert rotation_is_complete([heat.seats for heat in plan], ids, 2)
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
    assert env.races.heat_plan(race.id) == []


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


def test_the_race_assistant_shows_drivers_and_heats_in_large_blocks(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    anna = env.driver("Anna")
    ben = env.driver("Ben")
    env.vehicle("Porsche", driver_id=anna.id)
    env.vehicle("Ferrari", driver_id=ben.id)
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    window.resize(1200, 800)
    window.show()
    page.new_race()
    wizard = page.wizard
    wizard.resize(1100, 760)
    QApplication.processEvents()
    assert wizard.next_button.objectName() == "wizard-next"
    assert wizard.back_button.objectName() == "wizard-back"
    assert wizard.step_label.wordWrap()
    assert wizard.step_label.font().pixelSize() >= 18

    wizard.name_edit.setText("Finale")
    assert wizard.go_next()
    assert wizard.step_label.font().pixelSize() >= 18
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    wizard.laps_spin.setValue(2)
    assert wizard.go_next()
    for driver in (anna, ben):
        wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(driver.id))
        assert wizard.add_participant()
    assert wizard.go_next()
    QApplication.processEvents()
    assert wizard.overview_label.isHidden()
    assert "Anna mit Porsche 911" in wizard.overview_label.text()
    names = [
        label
        for label in wizard.overview_board.findChildren(QLabel)
        if label.isVisible() and "Anna mit Porsche 911" in label.text()
    ]
    assert names and names[0].wordWrap() and names[0].font().pixelSize() >= 22
    titles = [
        label
        for label in wizard.overview_board.findChildren(QLabel)
        if label.property("role") == "heat-title" and label.isVisible()
    ]
    assert titles
    assert titles[0].wordWrap()
    assert titles[0].font().pixelSize() >= 28
    assert titles[0].text().startswith("Durchlauf")

    assert wizard.go_next()
    QApplication.processEvents()
    assert wizard.start_button.objectName() == "race-start"
    assert not wizard.next_button.isEnabled()
    assert wizard.ready_label.isHidden()
    assert "Bahn 1:" in wizard.ready_label.text()
    title = wizard.briefing_board.title
    assert title.isVisible() and title.wordWrap()
    assert title.font().pixelSize() >= 28
    assert title.text().startswith("Durchlauf")
    seat = wizard.briefing_board.findChild(QLabel, "heat-seat-driver-1")
    lane = wizard.briefing_board.findChild(QLabel, "heat-seat-lane-1")
    assert seat is not None and lane is not None
    assert seat.isVisible() and seat.wordWrap() and seat.font().pixelSize() >= 22
    assert lane.wordWrap() and lane.text().startswith("Bahn")


def test_the_heat_change_briefing_uses_large_blocks(qtbot: QtBot, env: Env) -> None:
    gate = HeatGate(env.runtime.translator)
    qtbot.addWidget(gate)
    gate.resize(1000, 720)
    gate.show()
    gate.show_briefing(
        HeatBriefing(
            race_id=RaceId(1),
            sequence=2,
            heat_count=2,
            seats=(HeatSeat(1, 10, "Anna"), HeatSeat(2, 11, "Ben")),
            changes=(LaneChange("Anna", 2, 1), LaneChange("Ben", 1, 2)),
            drivers=((10, "Anna"), (11, "Ben")),
        )
    )
    QApplication.processEvents()
    assert gate.title_label.isHidden()
    assert gate.body_label.isHidden()
    assert gate.body_label.wordWrap()
    assert "Spurwechsel" in gate.body_label.text()
    assert "Bahn 1: Anna" in gate.body_label.text()
    assert gate.board.title.text() == "Durchlauf 2 von 2"
    assert gate.board.title.wordWrap()
    assert gate.board.title.font().pixelSize() >= 28
    anna = gate.findChild(QLabel, "heat-seat-driver-1")
    lane = gate.findChild(QLabel, "heat-seat-lane-1")
    assert anna is not None and lane is not None
    assert anna.text() == "Anna" and anna.wordWrap() and anna.font().pixelSize() >= 22
    assert lane.text() == "Bahn 1" and lane.wordWrap()
    change = gate.findChild(QLabel, "heat-change")
    assert change is not None and change.isVisible() and change.wordWrap()
    assert "Anna" in change.text() and "Bahn 2" in change.text()
    assert change.font().pixelSize() >= 18
    assert gate.start_button.objectName() == "heat-start"
    assert gate.postpone_button.objectName() == "heat-postpone"
    assert gate.disqualify_button.objectName() == "heat-disqualify"


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


def test_active_lanes_follow_the_track_until_a_race_chooses(env: Env) -> None:
    two = env.track("Zwei", lanes=2)
    race = env.races.create_race("Alt", two.id, 4)
    assert race.lane_count == 2
    assert race.active_lanes == 2
    chosen = env.races.update_race(
        race.id, race.name, two.id, 4, set_active_lanes=True, active_lanes=1
    )
    assert chosen.active_lanes == 1
    assert chosen.lane_count == 2
    reloaded = env.races.require_race(race.id)
    assert reloaded.active_lanes == 1
    four = env.track("Vier", lanes=4)
    moved = env.races.update_race(race.id, race.name, four.id, 4)
    assert moved.lane_count == 4
    assert moved.active_lanes == 4
    kept = env.races.update_race(
        race.id, race.name, two.id, 4, set_active_lanes=True, active_lanes=1
    )
    assert kept.active_lanes == 1
    assert kept.lane_count == 2
    stored = Race(name="Schmal", target_laps=1)
    with pytest.raises(ValidationError, match=r"error\.race\.active_lanes"):
        RaceService._store_active_lanes(stored, 1, 2)
    RaceService._store_active_lanes(stored, 1, None)
    assert stored.active_lanes is None
    assert effective_lane_count(None, 1) == 1


@pytest.mark.parametrize(
    ("drivers", "active", "heats"),
    [
        (1, 1, 1),
        (1, 2, 2),
        (2, 1, 2),
        (2, 2, 2),
        (3, 2, 3),
        (4, 2, 4),
        (5, 2, 5),
        (6, 2, 6),
        (8, 2, 8),
    ],
)
def test_every_driver_covers_the_lanes_the_race_selected(
    env: Env, drivers: int, active: int, heats: int
) -> None:
    race = _enrolled(env, lanes=2, drivers=drivers, active_lanes=active)
    plan = env.races.heat_plan(race.id)
    ids = [participant.id for participant in race.participants]
    assert len(plan) == heats
    assert rotation_is_complete([heat.seats for heat in plan], ids, active)
    if active == 1:
        assert {lane for heat in plan for _driver, lane in heat.seats} == {1}


def test_postponing_one_driver_keeps_the_partner_in_the_next_heat(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=4)
    ids = [participant.id for participant in race.participants]
    held, partner = ids[0], ids[1]
    briefing = env.races.postpone_driver(race.id, held)
    assert briefing is not None
    seated = {seat.participant_id for seat in briefing.seats if seat.participant_id is not None}
    assert held not in seated
    assert partner in seated
    assert len(seated) == 2
    assert rotation_is_complete([heat.seats for heat in env.races.heat_plan(race.id)], ids, 2)
    env.races.postpone_driver(race.id, held)
    assert rotation_is_complete([heat.seats for heat in env.races.heat_plan(race.id)], ids, 2)


def test_a_finished_heat_stays_when_another_driver_is_postponed(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=4, laps=1)
    ids = [participant.id for participant in race.participants]
    _finish_heat(env, race.id)
    before = [heat for heat in env.races.heat_plan(race.id) if heat.status == "completed"]
    held = ids[2]
    env.races.postpone_driver(race.id, held)
    after = [heat for heat in env.races.heat_plan(race.id) if heat.status == "completed"]
    assert [heat.seats for heat in after] == [heat.seats for heat in before]
    assert rotation_is_complete([heat.seats for heat in env.races.heat_plan(race.id)], ids, 2)
    assert env.races.get_laps(race.id)


def test_disqualification_drops_only_the_open_lanes_of_that_driver(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=4, laps=1)
    ids = [participant.id for participant in race.participants]
    _finish_heat(env, race.id)
    removed = ids[0]
    assert not env.races.disqualify_driver(race.id, removed)
    plan = env.races.heat_plan(race.id)
    completed = [heat.seats for heat in plan if heat.status == "completed"]
    assert any(driver == removed for heat in completed for driver, _lane in heat)
    open_seats = [heat.seats for heat in plan if heat.status == "planned"]
    assert all(driver != removed for heat in open_seats for driver, _lane in heat)
    remaining = [driver for driver in ids if driver != removed]
    covered = [tuple(seat for seat in heat.seats if seat[0] in set(remaining)) for heat in plan]
    assert rotation_is_complete([heat for heat in covered if heat], remaining, 2)
    assert env.races.get_laps(race.id)


def test_a_shortened_plan_does_not_count_as_a_finished_race(env: Env) -> None:
    from sqlalchemy import select

    from slot_racing.modules.races.models import RaceHeat

    race = _enrolled(env, lanes=2, drivers=4, laps=1)
    ids = [participant.id for participant in race.participants]
    runner = env.controller.start_race(race.id)
    with env.runtime.database.session() as session:
        planned = list(
            session.scalars(
                select(RaceHeat).where(
                    RaceHeat.race_id == int(race.id), RaceHeat.status == "planned"
                )
            )
        )
        for heat in planned:
            session.delete(heat)
    for _ in range(30):
        if not runner.is_active:
            break
        env.clock.advance(1_000_000_000)
        runner.tick()
    assert not runner.is_active
    stored = env.races.require_race(race.id)
    assert stored.status is RaceStatus.READY
    plan = env.races.heat_plan(race.id)
    assert rotation_is_complete([heat.seats for heat in plan], ids, 2)


def test_a_slower_second_lane_does_not_replace_the_best_lap(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=1, laps=1, active_lanes=2)
    _finish_heat(env, race.id)
    _finish_heat(env, race.id)
    stored = env.races.require_race(race.id)
    assert stored.status is RaceStatus.FINISHED
    laps = env.races.get_laps(race.id)
    assert {lap.lane for lap in laps} == {1, 2}
    best = min(lap.lap_time_ns for lap in laps)
    assert env.races.get_results(race.id)[0].best_lap_ns == best


def test_changing_active_lanes_before_the_start_rebuilds_the_plan(env: Env) -> None:
    race = _enrolled(env, lanes=2, drivers=4)
    ids = [participant.id for participant in race.participants]
    track_id = race.track_id
    assert track_id is not None
    assert len(env.races.heat_plan(race.id)) == 4
    updated = env.races.update_race(
        race.id, race.name, track_id, race.laps, set_active_lanes=True, active_lanes=1
    )
    assert updated.active_lanes == 1
    assert [participant.id for participant in updated.participants] == ids
    plan = env.races.heat_plan(race.id)
    assert rotation_is_complete([heat.seats for heat in plan], ids, 1)
    runner = env.controller.start_race(race.id)
    with pytest.raises(ValidationError, match=r"error\.race\.not_editable"):
        env.races.update_race(
            race.id,
            race.name,
            track_id,
            race.laps,
            set_active_lanes=True,
            active_lanes=2,
        )
    runner.stop()


def test_the_wizard_offers_one_or_two_lanes_only_on_a_two_lane_track(
    qtbot: QtBot, env: Env
) -> None:
    wide = env.track("Zwei", lanes=2)
    four = env.track("Vier", lanes=4)
    anna = env.driver("Anna")
    env.vehicle("Porsche", driver_id=anna.id)
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    window.show()
    page.new_race()
    wizard = page.wizard
    wizard.name_edit.setText("Spurwahl")
    assert wizard.go_next()
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(four.id))
    assert wizard.go_next()
    assert wizard.step == MODE
    assert not wizard.active_lanes_combo.isVisible()
    wizard.go_back()
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(wide.id))
    assert wizard.go_next()
    assert wizard.active_lanes_combo.isVisible()
    assert wizard.active_lanes_combo.isEnabled()
    assert wizard.active_lanes_combo.currentData() == 2
    wizard.active_lanes_combo.setCurrentIndex(wizard.active_lanes_combo.findData(1))
    assert wizard.go_next()
    stored = env.races.require_race(wizard.race.id) if wizard.race is not None else None
    assert stored is not None
    assert stored.lane_count == 2
    assert stored.active_lanes == 1
    wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(anna.id))
    assert wizard.add_participant()
    assert wizard.go_next()
    assert "Aktive Spuren: 1" in wizard.overview_label.text()
    assert "2 Spuren" in wizard.overview_label.text()


def test_a_one_lane_race_does_not_show_the_second_lane(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    race = env.races.create_race("Finale", track.id, 3)
    env.races.update_race(race.id, race.name, track.id, 3, set_active_lanes=True, active_lanes=1)
    for index in (1, 2):
        driver, vehicle = env.pair(index)
        env.races.enroll_driver(race.id, driver, vehicle)
    env.races.plan_heats(race.id)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.refresh()
    page.table.selectRow(0)
    page.buttons["start"].click()
    live = page.live
    assert isinstance(live, LiveRaceView)
    release_start_lights(live)
    assert live.findChild(QFrame, "live-lane-1") is not None
    assert live.findChild(QFrame, "live-lane-2") is None
    briefing = env.races.heat_briefing(race.id)
    assert briefing is not None
    assert [seat.lane for seat in briefing.seats] == [1]


def _enrolled(
    env: Env, *, lanes: int, drivers: int, laps: int = 2, active_lanes: int | None = None
) -> RaceInfo:
    track = env.track(lanes=lanes)
    race = env.races.create_race("Finale", track.id, laps)
    if active_lanes is not None:
        race = env.races.update_race(
            race.id,
            race.name,
            track.id,
            laps,
            set_active_lanes=True,
            active_lanes=active_lanes,
        )
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
