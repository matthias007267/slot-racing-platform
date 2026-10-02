"""The complete flow: master data, race configuration, simulated timing, engine, storage."""

from __future__ import annotations

from slot_racing.core.clock import NANOS_PER_SECOND
from slot_racing.core.domain import RaceStatus
from slot_racing.core.events import (
    Event,
    LapCompleted,
    RaceFinished,
    RaceStarted,
    SectorCompleted,
    SensorTriggered,
    WinnerDetermined,
)
from slot_racing.modules.drivers_vehicles.service import DriverInput, VehicleInput
from slot_racing.modules.races.runner import RaceRunner
from slot_racing.modules.tracks.service import TrackInput
from tests.modules.conftest import Env

STEP_NS = 100_000_000


def drive_until_done(env: Env, runner: RaceRunner, limit_s: int = 120) -> None:
    for _ in range(limit_s * 10):
        if runner.is_finished:
            return
        env.clock.advance(STEP_NS)
        runner.tick()
    raise AssertionError("the race did not finish")


def test_complete_race_from_setup_to_reloaded_result(env: Env) -> None:
    seen: list[Event] = []
    for event_type in (
        SensorTriggered,
        SectorCompleted,
        LapCompleted,
        RaceStarted,
        RaceFinished,
        WinnerDetermined,
    ):
        env.runtime.bus.subscribe(event_type, seen.append)

    track = env.tracks.create_track(TrackInput(name="Heimbahn", lane_count=2))
    anna = env.drivers.create_driver(DriverInput(name="Anna", start_number=1))
    ben = env.drivers.create_driver(DriverInput(name="Ben", start_number=2))
    porsche = env.vehicles.create_vehicle(
        VehicleInput(name="Porsche", model="911", driver_id=anna.id)
    )
    ferrari = env.vehicles.create_vehicle(
        VehicleInput(name="Ferrari", model="F40", driver_id=ben.id)
    )
    race = env.races.create_race("Finale", track.id, 3)
    env.races.add_participant(race.id, anna.id, porsche.id, 1)
    env.races.add_participant(race.id, ben.id, ferrari.id, 2)

    runner = env.controller.start_race(race.id)
    assert env.races.require_race(race.id).status is RaceStatus.RUNNING
    drive_until_done(env, runner)

    # The timing source only talked to the engine through events.
    sensor_events = [e for e in seen if isinstance(e, SensorTriggered)]
    assert sensor_events
    assert {e.source_id for e in sensor_events} == {"simulation"}
    assert len([e for e in seen if isinstance(e, LapCompleted)]) == 6
    assert len([e for e in seen if isinstance(e, SectorCompleted)]) == 18
    assert len([e for e in seen if isinstance(e, WinnerDetermined)]) == 1
    finished = [e for e in seen if isinstance(e, RaceFinished)]
    assert len(finished) == 1 and not finished[0].aborted

    # Everything was stored; read it back through a fresh query.
    stored = env.races.require_race(race.id)
    assert stored.status is RaceStatus.FINISHED
    assert stored.started_at is not None and stored.finished_at is not None

    results = env.races.get_results(race.id)
    assert [(r.position, r.driver_label, r.lane) for r in results] == [
        (1, "Anna", 1),
        (2, "Ben", 2),
    ]
    assert all(r.finished and r.laps_completed == 3 for r in results)
    winner, second = results
    assert winner.total_time_ns is not None and second.total_time_ns is not None
    assert winner.total_time_ns < second.total_time_ns
    assert winner.best_lap_ns is not None and winner.last_lap_ns is not None
    assert winner.best_lap_ns <= winner.last_lap_ns
    assert winner.average_lap_ns is not None
    assert abs(winner.total_time_ns - 3 * winner.average_lap_ns) < NANOS_PER_SECOND

    laps = env.races.get_laps(race.id)
    assert len(laps) == 6
    assert [lap.lap_number for lap in laps if lap.lane == 1] == [1, 2, 3]
    for lap in laps:
        assert len(lap.sector_times_ns) == 3
        assert sum(lap.sector_times_ns) == lap.lap_time_ns

    # The snapshot the UI shows agrees with the stored result.
    snapshot = runner.snapshot()
    assert snapshot.status is RaceStatus.FINISHED and not snapshot.aborted
    assert [row.driver_label for row in snapshot.rows] == [r.driver_label for r in results]
    assert [row.total_time_ns for row in snapshot.rows] == [r.total_time_ns for r in results]


def test_aborted_race_keeps_the_laps_driven_so_far(env: Env) -> None:
    race = env.races.create_race("Training", env.track_id(), 10)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    runner = env.controller.start_race(race.id)

    for _ in range(130):
        env.clock.advance(STEP_NS)
        runner.tick()
    runner.stop()

    assert env.races.require_race(race.id).status is RaceStatus.ABORTED
    (result,) = env.races.get_results(race.id)
    assert result.laps_completed == 2
    assert not result.finished
    assert result.position == 1
    assert len(env.races.get_laps(race.id)) == 2


def test_pause_freezes_the_race_time(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 2)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    runner = env.controller.start_race(race.id)

    env.clock.advance(2 * NANOS_PER_SECOND)
    runner.tick()
    runner.pause()
    frozen = runner.snapshot().elapsed_ns
    env.clock.advance(30 * NANOS_PER_SECOND)
    runner.tick()
    assert runner.snapshot().elapsed_ns == frozen
    assert runner.snapshot().status is RaceStatus.PAUSED
    runner.resume()
    env.clock.advance(NANOS_PER_SECOND)
    assert runner.snapshot().elapsed_ns == frozen + NANOS_PER_SECOND
    drive_until_done(env, runner)
    (result,) = env.races.get_results(race.id)
    assert result.laps_completed == 2


def test_a_new_race_can_follow_a_finished_one(env: Env) -> None:
    track_id = env.track_id()
    driver_id, vehicle_id = env.pair(1)
    first = env.races.create_race("Eins", track_id, 1)
    second = env.races.create_race("Zwei", track_id, 1)
    for race in (first, second):
        env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    drive_until_done(env, env.controller.start_race(first.id))
    drive_until_done(env, env.controller.start_race(second.id))
    assert env.races.require_race(first.id).status is RaceStatus.FINISHED
    assert env.races.require_race(second.id).status is RaceStatus.FINISHED
    assert len(env.races.get_laps(first.id)) == 1
    assert len(env.races.get_laps(second.id)) == 1
