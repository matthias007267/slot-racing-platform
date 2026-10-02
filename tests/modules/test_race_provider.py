"""A race knows its timing provider by id; the factory is resolved when the race starts."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import inspect

from carrera.core.domain import RaceId, RaceStatus
from carrera.core.errors import ProviderConfigurationError, ProviderUnavailable, ValidationError
from carrera.core.storage import Database
from carrera.core.storage.database import alembic_config
from carrera.core.timing import ProviderAvailability, ProviderCapabilities, TimingSourceFactory
from carrera.modules.races.models import Race
from carrera.modules.races.service import RaceService
from tests.modules.conftest import Env
from tests.support.timing import FakeTimingFactory, FakeTimingSource


def register(env: Env, factory: TimingSourceFactory) -> None:
    env.runtime.services.register(
        TimingSourceFactory, factory, owner="test", name=factory.provider_id
    )


def ready_race(env: Env, provider: str | None = None, lanes: int = 1) -> RaceId:
    race = env.races.create_race("R", env.track_id(), 2, provider)
    for lane in range(1, lanes + 1):
        driver_id, vehicle_id = env.pair(lane)
        env.races.add_participant(race.id, driver_id, vehicle_id, lane)
    return race.id


def test_new_races_default_to_the_simulation(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 3)
    assert race.timing_provider == "simulation"
    assert env.races.default_provider_id() == "simulation"


def test_the_provider_is_stored_and_loaded_again(env: Env) -> None:
    register(env, FakeTimingFactory("fake"))
    race = env.races.create_race("R", env.track_id(), 3, "fake")
    assert env.races.require_race(race.id).timing_provider == "fake"
    assert [r.timing_provider for r in env.races.list_races()] == ["fake"]
    fresh = RaceService(
        env.runtime.database, env.drivers, env.vehicles, env.tracks
    )  # a second service instance reads the same stored value
    loaded = fresh.get_race(race.id)
    assert loaded is not None and loaded.timing_provider == "fake"


def test_update_keeps_the_provider_unless_a_new_one_is_given(env: Env) -> None:
    race = env.races.create_race("R", env.track_id(), 3, "camera")
    kept = env.races.update_race(race.id, "R2", race.track_id or env.track_id(), 4)
    assert kept.timing_provider == "camera"
    changed = env.races.update_race(race.id, "R2", race.track_id or env.track_id(), 4, "simulation")
    assert changed.timing_provider == "simulation"


def test_a_race_can_name_a_provider_that_is_not_installed_but_cannot_start(env: Env) -> None:
    race_id = ready_race(env, "raspberry_pi")
    assert env.races.require_race(race_id).timing_provider == "raspberry_pi"
    with pytest.raises(ProviderUnavailable) as error:
        env.controller.start_race(race_id)
    assert error.value.key == "error.timing_provider.unknown"
    assert env.races.require_race(race_id).status is RaceStatus.READY


@pytest.mark.parametrize("provider", [" ", "x" * 65])
def test_invalid_provider_ids_are_rejected(env: Env, provider: str) -> None:
    with pytest.raises(ValidationError):
        env.races.create_race("R", env.track_id(), 3, provider)
    assert env.races.list_races() == []


def test_configured_default_is_used_when_available_else_the_first_available(env: Env) -> None:
    register(env, FakeTimingFactory("b-fake"))
    registry = env.runtime.services.get(type(env.races._providers))
    configured = RaceService(
        env.runtime.database, env.drivers, env.vehicles, env.tracks, registry, "b-fake"
    )
    assert configured.default_provider_id() == "b-fake"
    missing = RaceService(
        env.runtime.database, env.drivers, env.vehicles, env.tracks, registry, "nope"
    )
    assert missing.default_provider_id() == "b-fake"
    without_registry = RaceService(
        env.runtime.database, env.drivers, env.vehicles, env.tracks, None, "camera"
    )
    assert without_registry.default_provider_id() == "camera"


def test_an_unavailable_provider_is_detected_before_the_start(env: Env) -> None:
    register(
        env,
        FakeTimingFactory(
            "pi", availability=ProviderAvailability.unavailable("error.timing_provider.unavailable")
        ),
    )
    race_id = ready_race(env, "pi")
    with pytest.raises(ProviderUnavailable):
        env.races.validate_startable(race_id)
    with pytest.raises(ProviderUnavailable):
        env.controller.start_race(race_id)
    assert env.controller.active is None
    assert env.races.require_race(race_id).status is RaceStatus.READY


def test_a_single_lane_provider_rejects_a_two_lane_race_before_the_start(env: Env) -> None:
    register(
        env,
        FakeTimingFactory("one", capabilities=ProviderCapabilities(supports_multiple_lanes=False)),
    )
    with pytest.raises(ProviderConfigurationError):
        env.races.validate_startable(ready_race(env, "one", lanes=2))
    env.races.validate_startable(ready_race(env, "one", lanes=1))


def test_the_race_starts_with_the_factory_of_its_provider(env: Env) -> None:
    source = FakeTimingSource("fake")
    factory = FakeTimingFactory("fake", source=source)
    register(env, factory)
    race_id = ready_race(env, "fake")
    runner = env.controller.start_race(race_id)
    assert source.calls == ["start"] and source.is_running
    spec = factory.specs[0]
    assert (spec.race_id, spec.lanes, spec.laps) == (race_id, (1,), 2)
    assert spec.track_id == env.races.require_race(race_id).track_id
    env.clock.advance(1_000_000_000)
    runner.tick()
    assert source.calls == ["start", "poll"]
    runner.stop()
    assert source.calls[-1] == "stop"
    assert env.races.require_race(race_id).status is RaceStatus.ABORTED


def test_a_factory_that_crashes_does_not_leave_a_running_race(env: Env) -> None:
    register(env, FakeTimingFactory("crash", create_error=RuntimeError("no device")))
    race_id = ready_race(env, "crash")
    with pytest.raises(ValidationError) as error:
        env.controller.start_race(race_id)
    assert error.value.key == "error.timing_provider.failed"
    assert env.controller.active is None
    assert env.races.require_race(race_id).status is RaceStatus.READY
    register(env, FakeTimingFactory("fine"))
    env.controller.start_race(ready_race(env, "fine"))


def test_migration_gives_existing_races_the_simulation_provider() -> None:
    database = Database.in_memory()
    database.migrate("0003")
    with database.engine.begin() as connection:
        for statement in (
            "INSERT INTO tracks (id, name, lane_count) VALUES (1, 'Ring', 2)",
            "INSERT INTO races (id, name, track_id, status, target_laps) "
            "VALUES (1, 'Alt', 1, 'finished', 3)",
        ):
            connection.exec_driver_sql(statement)

    database.migrate()

    with database.session() as session:
        race = session.get(Race, 1)
        assert race is not None
        assert (race.name, race.status, race.target_laps) == ("Alt", "finished", 3)
        assert race.timing_provider == "simulation"
    database.dispose()


def test_downgrade_removes_only_the_provider_column() -> None:
    database = Database.in_memory()
    database.migrate()
    with database.session() as session:
        session.add(Race(name="R", status="created", target_laps=2, timing_provider="camera"))
    with database.engine.connect() as connection:
        config = alembic_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "0003")
    columns = {column["name"] for column in inspect(database.engine).get_columns("races")}
    assert "timing_provider" not in columns and {"name", "status", "target_laps"} <= columns
    database.dispose()
