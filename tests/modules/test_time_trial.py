"""Time trials keep every measured time, and the lane stays part of that result."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.core.catalog import DriverInfo, VehicleInfo
from slot_racing.core.clock import NANOS_PER_SECOND as S
from slot_racing.core.clock import ManualClock
from slot_racing.core.domain import (
    DriverId,
    Participant,
    RaceId,
    RaceMode,
    RaceStatus,
    TimingLayout,
    TimingSetup,
    TrackId,
)
from slot_racing.core.errors import ValidationError
from slot_racing.core.events import Event, EventBus, RaceFinished, WinnerDetermined
from slot_racing.modules.drivers_vehicles.service import VehicleInput
from slot_racing.modules.races.engine import RaceConfig, RaceEngine
from slot_racing.modules.races.models import RaceParticipant
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.wizard import PARTICIPANTS
from slot_racing.modules.timing.simulation import SimulatedLane, SimulationTimingProvider
from tests.modules.conftest import Env
from tests.modules.test_races import key_of
from tests.modules.test_ui_management import cells, open_page

LAYOUT = TimingLayout.from_position_ids(["sf"])
SETUP = TimingSetup.for_layout(LAYOUT)


def test_a_time_trial_can_be_created_without_a_lap_target(env: Env) -> None:
    track = env.track("Heimbahn", lanes=4)
    race = env.races.create_time_trial("Training", track.id)
    assert race.mode is RaceMode.TIME_TRIAL
    assert race.laps == 0
    assert race.status is RaceStatus.CREATED
    assert race.track_id == track.id
    lap_race = env.races.create_race("Sonntag", track.id, 8)
    assert lap_race.mode is RaceMode.LAPS
    assert lap_race.laps == 8


def test_driver_vehicle_and_lane_are_stored_on_the_participant(env: Env) -> None:
    race = env.races.create_time_trial("Training", env.track_id(lanes=3))
    driver = env.driver("Max")
    vehicle = env.vehicle("Porsche", driver_id=driver.id)
    added = env.races.add_participant(race.id, driver.id, vehicle.id, 3)
    stored = env.races.require_race(race.id)
    assert [(item.driver_id, item.vehicle_id, item.lane) for item in stored.participants] == [
        (driver.id, vehicle.id, 3)
    ]
    assert added.lane == 3


def test_a_measured_time_keeps_driver_vehicle_lane_and_moment(env: Env) -> None:
    race, driver, vehicle = _trial_with_driver(env, lane=2)
    _measure(env, race.id, lane=2, lap=1, time_ns=8_421_000_000)
    (row,) = env.races.list_time_measurements(race_id=race.id)
    assert row.driver_id == driver.id
    assert row.vehicle_id == vehicle.id
    assert row.lane == 2
    assert row.time_ns == 8_421_000_000
    assert row.recorded_at is not None


def test_the_lane_stays_on_the_measurement_when_the_participant_changes(env: Env) -> None:
    race, _, _ = _trial_with_driver(env, lane=1)
    _measure(env, race.id, lane=1, lap=1, time_ns=8_200_000_000)
    with env.runtime.database.session() as session:
        participant = session.scalars(select(RaceParticipant)).one()
        participant.lane = 4
    (row,) = env.races.list_time_measurements(race_id=race.id)
    assert row.lane == 1
    assert env.races.require_race(race.id).participants[0].lane == 4


def test_the_same_driver_can_race_again(env: Env) -> None:
    track = env.track(lanes=2)
    driver = env.driver("Max")
    first_car = env.vehicle("Porsche", driver_id=driver.id)
    second_car = env.vehicle("Ferrari")
    first = env.races.create_time_trial("Erster", track.id)
    env.races.add_participant(first.id, driver.id, first_car.id, 1)
    _measure(env, first.id, lane=1, lap=1, time_ns=8_500_000_000)
    second = env.races.create_time_trial("Zweiter", track.id)
    env.races.add_participant(second.id, driver.id, second_car.id, 1)
    _measure(env, second.id, lane=1, lap=1, time_ns=8_100_000_000)
    history = env.races.list_time_measurements(track_id=track.id)
    assert [row.time_ns for row in history] == [8_500_000_000, 8_100_000_000]
    assert {row.vehicle_id for row in history} == {first_car.id, second_car.id}


def test_the_same_driver_and_vehicle_can_repeat_the_same_lane(env: Env) -> None:
    race, driver, vehicle = _trial_with_driver(env, lane=3)
    _measure(env, race.id, lane=3, lap=1, time_ns=8_342_000_000)
    _measure(env, race.id, lane=3, lap=2, time_ns=8_281_000_000)
    track_id = env.races.require_race(race.id).track_id
    assert track_id is not None
    again = env.races.create_time_trial("Nochmal", track_id)
    env.races.add_participant(again.id, driver.id, vehicle.id, 3)
    _measure(env, again.id, lane=3, lap=1, time_ns=8_400_000_000)
    history = env.races.list_time_measurements()
    assert [(row.driver_id, row.vehicle_id, row.lane) for row in history] == [
        (driver.id, vehicle.id, 3),
        (driver.id, vehicle.id, 3),
        (driver.id, vehicle.id, 3),
    ]


def test_several_drivers_can_use_the_same_vehicle(env: Env) -> None:
    track = env.track(lanes=2)
    race = env.races.create_time_trial("Geteilt", track.id)
    max_ = env.driver("Max")
    tom = env.driver("Tom")
    porsche = env.vehicle("Porsche")
    env.races.add_participant(race.id, max_.id, porsche.id, 1)
    env.races.add_participant(race.id, tom.id, porsche.id, 2)
    stored = env.races.require_race(race.id)
    assert [item.vehicle_id for item in stored.participants] == [porsche.id, porsche.id]
    assert len(env.vehicles.list_vehicles()) == 1
    _measure(env, race.id, lane=1, lap=1, time_ns=8_421_000_000)
    _measure(env, race.id, lane=2, lap=1, time_ns=8_512_000_000)
    rows = env.races.list_time_measurements(race_id=race.id)
    assert {(row.driver_label, row.lane, row.vehicle_id) for row in rows} == {
        ("Max", 1, porsche.id),
        ("Tom", 2, porsche.id),
    }


def test_two_participants_cannot_share_a_lane_in_one_time_trial(env: Env) -> None:
    race = env.races.create_time_trial("Training", env.track_id(lanes=2))
    first = env.driver("Max")
    second = env.driver("Tom")
    car = env.vehicle("Porsche")
    env.races.add_participant(race.id, first.id, car.id, 1)
    with pytest.raises(ValidationError) as caught:
        env.races.add_participant(race.id, second.id, car.id, 1)
    assert key_of(caught) == "error.race.lane_taken"
    assert len(env.races.require_race(race.id).participants) == 1


def test_best_times_stay_separated_by_lane(env: Env) -> None:
    track = env.track(lanes=3)
    max_ = env.driver("Max")
    tom = env.driver("Tom")
    alex = env.driver("Alex")
    porsche = env.vehicle("Porsche")
    ferrari = env.vehicle("Ferrari")
    _run(env, track.id, "A", max_, porsche, 3, 8_347_000_000)
    _run(env, track.id, "B", max_, porsche, 1, 9_000_000_000)
    _run(env, track.id, "C", tom, ferrari, 3, 8_281_000_000)
    _run(env, track.id, "D", alex, porsche, 3, 8_412_000_000)
    lane_3 = env.races.driver_bests_on_lane(3, track_id=track.id)
    assert [(row.driver_label, row.time_ns, row.lane) for row in lane_3] == [
        ("Tom", 8_281_000_000, 3),
        ("Max", 8_347_000_000, 3),
        ("Alex", 8_412_000_000, 3),
    ]
    lane_1 = env.races.driver_bests_on_lane(1, track_id=track.id)
    assert [(row.driver_label, row.time_ns) for row in lane_1] == [("Max", 9_000_000_000)]
    personal = env.races.personal_best(max_.id, porsche.id, 3, track_id=track.id)
    assert personal is not None and personal.time_ns == 8_347_000_000
    other_lane = env.races.personal_best(max_.id, porsche.id, 1, track_id=track.id)
    assert other_lane is not None and other_lane.time_ns == 9_000_000_000
    on_car = env.races.vehicle_bests_on_lane(porsche.id, 3, track_id=track.id)
    assert [row.driver_label for row in on_car] == ["Max", "Alex"]
    assert on_car[0].time_ns == 8_347_000_000


def test_a_faster_time_updates_the_best_and_a_slower_one_does_not(env: Env) -> None:
    race, driver, vehicle = _trial_with_driver(env, lane=3)
    track_id = env.races.require_race(race.id).track_id
    assert track_id is not None
    _measure(env, race.id, lane=3, lap=1, time_ns=8_342_000_000)
    _measure(env, race.id, lane=3, lap=2, time_ns=8_281_000_000)
    best = env.races.personal_best(driver.id, vehicle.id, 3, track_id=track_id)
    assert best is not None and best.time_ns == 8_281_000_000
    _measure(env, race.id, lane=3, lap=3, time_ns=8_400_000_000)
    best_after = env.races.personal_best(driver.id, vehicle.id, 3, track_id=track_id)
    assert best_after is not None and best_after.time_ns == 8_281_000_000
    assert best_after.measurement_id == best.measurement_id
    history = env.races.list_time_measurements(race_id=race.id)
    assert [row.time_ns for row in history] == [8_342_000_000, 8_281_000_000, 8_400_000_000]
    overall = env.races.overall_bests(track_id=track_id)
    assert [(row.driver_label, row.vehicle_label, row.lane, row.time_ns) for row in overall] == [
        ("Max", "Porsche 911", 3, 8_281_000_000)
    ]


def test_best_times_of_different_tracks_stay_apart(env: Env) -> None:
    home = env.track("Heim", lanes=2)
    away = env.track("Auswärts", lanes=2)
    driver = env.driver("Max")
    vehicle = env.vehicle("Porsche")
    _run(env, home.id, "Heimlauf", driver, vehicle, 1, 8_000_000_000)
    _run(env, away.id, "Auswärtslauf", driver, vehicle, 1, 7_000_000_000)
    home_best = env.races.personal_best(driver.id, vehicle.id, 1, track_id=home.id)
    away_best = env.races.personal_best(driver.id, vehicle.id, 1, track_id=away.id)
    assert home_best is not None and home_best.time_ns == 8_000_000_000
    assert away_best is not None and away_best.time_ns == 7_000_000_000


def test_a_time_trial_does_not_finish_after_a_number_of_laps() -> None:
    clock = ManualClock()
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(Event, events.append)
    participants = (Participant(DriverId(1), 1), Participant(DriverId(2), 2))
    config = RaceConfig(
        RaceId(4),
        0,
        participants,
        LAYOUT,
        mode=RaceMode.TIME_TRIAL,
    )
    sim = SimulationTimingProvider(
        clock,
        SETUP,
        [SimulatedLane(1, (10 * S, 8 * S)), SimulatedLane(2, (9 * S,))],
        laps=2,
    )
    engine = RaceEngine(config, bus, clock, [sim])
    engine.start()
    sim.run_to_end()
    assert engine.status.value == "running"
    assert not any(isinstance(event, WinnerDetermined) for event in events)
    assert [result.laps_completed for result in engine.results()] == [2, 2]
    engine.stop()
    assert engine.status is RaceStatus.FINISHED
    finished = [event for event in events if isinstance(event, RaceFinished)]
    assert len(finished) == 1 and not finished[0].aborted
    assert [
        (row.lane, row.position, row.best_lap_ns, row.finished) for row in finished[0].results
    ] == [
        (1, 1, 8 * S, True),
        (2, 2, 9 * S, True),
    ]
    assert sum(isinstance(event, WinnerDetermined) for event in events) == 1


def test_running_a_time_trial_stores_each_lap_until_it_is_stopped(env: Env) -> None:
    race, _, _ = _trial_with_driver(env, lane=1)
    runner = env.controller.start_race(race.id)
    for _ in range(300):
        if len(env.races.list_time_measurements(race_id=race.id)) >= 2:
            break
        env.clock.advance(100_000_000)
        runner.tick()
    assert runner.is_active
    assert env.races.require_race(race.id).status is RaceStatus.RUNNING
    measured = env.races.list_time_measurements(race_id=race.id)
    assert len(measured) >= 2
    assert {row.lane for row in measured} == {1}
    runner.stop()
    stored = env.races.require_race(race.id)
    assert stored.status is RaceStatus.FINISHED
    assert len(env.races.list_time_measurements(race_id=race.id)) == len(measured)


def test_the_wizard_starts_a_time_trial_and_keeps_favorites(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    anna = env.driver("Anna")
    ben = env.driver("Ben")
    env.vehicle("Porsche", driver_id=anna.id)
    favorite = env.vehicles.create_vehicle(
        VehicleInput(name="Alfa", model="Giulia", driver_id=anna.id, is_favorite=True)
    )
    spare = env.vehicle("Ferrari", driver_id=ben.id)
    _, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    wizard.name_edit.setText("Qualifying")
    assert wizard.go_next()
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    wizard.mode_combo.setCurrentIndex(wizard.mode_combo.findData("time_trial"))
    assert wizard.laps_spin.isHidden()
    assert wizard.go_next()
    assert wizard.step == PARTICIPANTS
    wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(anna.id))
    assert wizard.vehicle_combo.currentData() == favorite.id
    wizard.vehicle_combo.setCurrentIndex(wizard.vehicle_combo.findData(spare.id))
    wizard.lane_combo.setCurrentIndex(wizard.lane_combo.findData(1))
    assert wizard.add_participant()
    wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(ben.id))
    wizard.vehicle_combo.setCurrentIndex(wizard.vehicle_combo.findData(spare.id))
    wizard.lane_combo.setCurrentIndex(wizard.lane_combo.findData(2))
    assert wizard.add_participant()
    assert wizard.go_next()
    assert "Zeitrennen" in wizard.overview_label.text()
    assert "Rundenrennen über" not in wizard.overview_label.text()
    stored = env.races.require_race(wizard.race.id) if wizard.race is not None else None
    assert stored is not None and stored.mode is RaceMode.TIME_TRIAL
    assert [item.vehicle_id for item in stored.participants] == [spare.id, spare.id]
    page.show_results(stored.id)
    assert page.results.measurements_table.rowCount() == 0
    assert not page.results.table.isVisible()
    _measure(env, stored.id, lane=1, lap=1, time_ns=8_421_000_000)
    page.results.show_race(stored.id)
    assert cells(page.results.measurements_table, 0)[:4] == ["Anna", "Ferrari 911", "1", "0:08.421"]
    assert page.results.summary.text().endswith("Spur 1")


def _trial_with_driver(env: Env, lane: int) -> tuple[RaceInfo, DriverInfo, VehicleInfo]:
    track = env.track(lanes=max(lane, 2))
    race = env.races.create_time_trial("Training", track.id)
    driver = env.driver("Max")
    vehicle = env.vehicle("Porsche", driver_id=driver.id)
    env.races.add_participant(race.id, driver.id, vehicle.id, lane)
    return race, driver, vehicle


def _measure(env: Env, race_id: RaceId, *, lane: int, lap: int, time_ns: int) -> None:
    env.races.record_lap(race_id, lane, lap, time_ns, time_ns, {})


def _run(
    env: Env,
    track_id: TrackId,
    name: str,
    driver: DriverInfo,
    vehicle: VehicleInfo,
    lane: int,
    time_ns: int,
) -> None:
    race = env.races.create_time_trial(name, track_id)
    env.races.add_participant(race.id, driver.id, vehicle.id, lane)
    _measure(env, race.id, lane=lane, lap=1, time_ns=time_ns)
