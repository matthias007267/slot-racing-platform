"""A single failing part must not take the race flow down."""

from __future__ import annotations

import pytest

from carrera.core.clock import NANOS_PER_SECOND
from carrera.core.domain import RaceId, RaceStatus
from carrera.core.errors import ValidationError
from carrera.core.timing import (
    SensorSink,
    TimingSessionSpec,
    TimingSource,
    TimingSourceFactory,
)
from carrera.modules.races.runner import RaceController
from tests.modules.conftest import Env


class BrokenSource(TimingSource):
    @property
    def source_id(self) -> str:
        return "broken"

    @property
    def is_running(self) -> bool:
        return False

    def start(self, sink: SensorSink) -> None:
        raise RuntimeError("device unplugged")

    def stop(self) -> None:
        pass


class BrokenFactory(TimingSourceFactory):
    @property
    def name(self) -> str:
        return "broken"

    def create_source(self, spec: TimingSessionSpec) -> TimingSource:
        return BrokenSource()


def ready_race(env: Env) -> RaceId:
    race = env.races.create_race("R", env.track_id(), 2)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    return race.id


def test_race_without_timing_module_reports_a_clear_error(env: Env) -> None:
    race_id = ready_race(env)
    env.runtime.plugins.disable("timing")
    with pytest.raises(ValidationError) as caught:
        env.controller.start_race(race_id)
    assert caught.value.key == "error.race.no_timing_source"
    assert env.races.require_race(race_id).status is RaceStatus.READY
    env.runtime.plugins.enable("timing")
    env.controller.start_race(race_id)


def test_unknown_preferred_timing_source_is_reported(env: Env) -> None:
    race_id = ready_race(env)
    controller = RaceController(
        env.races,
        env.runtime.bus,
        env.clock,
        factories=lambda: env.runtime.services.find_all(TimingSourceFactory),
        preferred_source="camera",
    )
    with pytest.raises(ValidationError) as caught:
        controller.start_race(race_id)
    assert caught.value.key == "error.race.timing_source_missing"


def test_failing_timing_source_does_not_stop_the_race(env: Env) -> None:
    race_id = ready_race(env)
    controller = RaceController(
        env.races, env.runtime.bus, env.clock, factories=lambda: [BrokenFactory()]
    )
    runner = controller.start_race(race_id)
    snapshot = runner.snapshot()
    assert snapshot.status is RaceStatus.RUNNING
    assert any("device unplugged" in message for message in snapshot.source_errors)
    env.clock.advance(NANOS_PER_SECOND)
    runner.tick()
    runner.stop()
    assert env.races.require_race(race_id).status is RaceStatus.ABORTED


def test_storage_failure_during_the_race_is_reported_not_fatal(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    race_id = ready_race(env)

    def broken_record_lap(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(env.races, "record_lap", broken_record_lap)
    runner = env.controller.start_race(race_id)
    for _ in range(80):
        env.clock.advance(100_000_000)
        runner.tick()
    snapshot = runner.snapshot()
    assert snapshot.status is RaceStatus.RUNNING
    assert snapshot.rows[0].laps_completed >= 1
    assert any("disk full" in message for message in snapshot.source_errors)
    runner.stop()
    assert env.races.require_race(race_id).status is RaceStatus.ABORTED


def test_a_second_race_cannot_start_while_one_runs(env: Env) -> None:
    first = ready_race(env)
    second = ready_race(env)
    env.controller.start_race(first)
    with pytest.raises(ValidationError) as caught:
        env.controller.start_race(second)
    assert caught.value.key == "error.race.already_running"
    assert env.races.require_race(second).status is RaceStatus.READY
