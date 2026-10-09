"""Storing timing setups: round trip, restart, deletion, migration of existing data."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, select

from slot_racing.core.domain import (
    TimingLayout,
    TimingPosition,
    TimingPositionType,
    TimingSensor,
    TimingSetup,
    TrackId,
    default_timing_setup,
)
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage import Database
from slot_racing.core.storage.database import alembic_config
from slot_racing.modules.timing.models import TimingConfiguration
from slot_racing.modules.timing.models import TimingPosition as PositionRow
from slot_racing.modules.timing.models import TimingSensor as SensorRow
from slot_racing.modules.timing.service import TimingSetupManager
from slot_racing.modules.tracks.service import TrackInput, TrackService
from tests.database import migrated_database
from tests.modules.conftest import Env


def custom_setup() -> TimingSetup:
    layout = TimingLayout(
        (
            TimingPosition("sf", TimingPositionType.START_FINISH, 1, "Ziel"),
            TimingPosition("kurve", TimingPositionType.SECTOR, 2),
            TimingPosition("gerade", TimingPositionType.SECTOR, 3, "Lange Gerade"),
            TimingPosition("spitzkehre", TimingPositionType.SECTOR, 4),
        )
    )
    return TimingSetup(
        layout,
        (
            TimingSensor("S-A", "sf", "Ziel-Sensor", "GPIO17"),
            TimingSensor("S-B", "kurve", None, "GPIO 5"),
            TimingSensor("S-C", "gerade", active=False),
            TimingSensor("S-D", "spitzkehre", hardware_id="usb-0"),
        ),
    )


@pytest.fixture
def manager(env: Env) -> TimingSetupManager:
    return TimingSetupManager(env.runtime.services.get(Database))


def test_a_track_without_configuration_has_no_stored_setup(
    env: Env, manager: TimingSetupManager
) -> None:
    assert manager.get_setup(env.track_id()) is None


def test_save_and_load_keeps_positions_sensors_and_assignments(
    env: Env, manager: TimingSetupManager
) -> None:
    track_id = env.track_id()
    manager.save_setup(track_id, custom_setup())
    assert manager.get_setup(track_id) == custom_setup()


def test_saving_again_replaces_the_previous_setup(env: Env, manager: TimingSetupManager) -> None:
    track_id = env.track_id()
    manager.save_setup(track_id, custom_setup())
    manager.save_setup(track_id, default_timing_setup())
    assert manager.get_setup(track_id) == default_timing_setup()
    database = env.runtime.services.get(Database)
    with database.session() as session:
        assert len(session.scalars(select(PositionRow)).all()) == 3
        assert len(session.scalars(select(SensorRow)).all()) == 3
        assert len(session.scalars(select(TimingConfiguration)).all()) == 1


def test_setups_of_different_tracks_are_independent(env: Env, manager: TimingSetupManager) -> None:
    first, second = env.track_id(), env.track_id()
    manager.save_setup(first, custom_setup())
    manager.save_setup(second, default_timing_setup())
    assert manager.get_setup(first) == custom_setup()
    assert manager.get_setup(second) == default_timing_setup()


def test_clear_removes_the_setup(env: Env, manager: TimingSetupManager) -> None:
    track_id = env.track_id()
    manager.save_setup(track_id, custom_setup())
    manager.clear_setup(track_id)
    assert manager.get_setup(track_id) is None
    manager.clear_setup(env.track_id())


def test_saving_for_an_unknown_track_is_a_validation_error(manager: TimingSetupManager) -> None:
    with pytest.raises(ValidationError) as error:
        manager.save_setup(TrackId(999), default_timing_setup())
    assert error.value.key == "error.timing.track_unknown"


def test_deleting_a_track_removes_its_setup(env: Env, manager: TimingSetupManager) -> None:
    track_id = env.track_id()
    manager.save_setup(track_id, custom_setup())
    env.tracks.delete_track(track_id)
    database = env.runtime.services.get(Database)
    with database.session() as session:
        assert session.scalars(select(PositionRow)).all() == []
        assert session.scalars(select(SensorRow)).all() == []


def test_setup_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "slot_racing.db"
    first = Database.from_path(path)
    first.migrate()
    track = TrackService(first).create_track(TrackInput(name="Ring", lane_count=2))
    TimingSetupManager(first).save_setup(track.id, custom_setup())
    first.dispose()

    second = Database.from_path(path)
    second.migrate()
    assert TimingSetupManager(second).get_setup(track.id) == custom_setup()
    second.dispose()


def test_migration_keeps_existing_tracks_and_sensors() -> None:
    database = Database.in_memory()
    database.migrate("0002")
    with database.engine.begin() as connection:
        for statement in (
            "INSERT INTO tracks (id, name, lane_count) VALUES (1, 'Ring', 2)",
            "INSERT INTO timing_configurations "
            "(id, track_id, name, source_type, is_default, settings) "
            "VALUES (1, 1, 'Alt', 'simulation', 1, '{}')",
            "INSERT INTO timing_sensors (id, configuration_id, sensor_id, role, sequence_index, "
            "settings) VALUES (1, 1, 'sf', 'START_FINISH', 1, '{}')",
        ):
            connection.exec_driver_sql(statement)

    database.migrate()

    manager = TimingSetupManager(database)
    assert manager.get_setup(TrackId(1)) is None
    with database.session() as session:
        sensor = session.get(SensorRow, 1)
        assert sensor is not None
        assert sensor.sensor_id == "sf" and sensor.is_active and sensor.position_id is None
        assert session.get(TimingConfiguration, 1) is not None
    manager.save_setup(TrackId(1), default_timing_setup())
    assert manager.get_setup(TrackId(1)) == default_timing_setup()
    database.dispose()


def test_downgrade_removes_only_the_layout_tables() -> None:
    database = migrated_database()
    with database.engine.connect() as connection:
        config = alembic_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "0002")
    tables = set(inspect(database.engine).get_table_names())
    assert "timing_positions" not in tables
    assert {"tracks", "timing_configurations", "timing_sensors"} <= tables
    database.dispose()
