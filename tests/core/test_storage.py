from collections.abc import Iterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from carrera.core.storage import Base, Database, PluginRecord, Setting, import_plugin_models
from carrera.modules.drivers_vehicles.models import Driver, Vehicle
from carrera.modules.races.models import Lap, Race, RaceParticipant, Sector
from carrera.modules.timing.models import TimingConfiguration, TimingSensor
from carrera.modules.tracks.models import Track, TrackLayout


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database.in_memory()
    database.migrate()
    yield database
    database.dispose()


def test_migration_creates_all_domain_tables(db: Database) -> None:
    tables = set(inspect(db.engine).get_table_names())
    assert {
        "drivers",
        "vehicles",
        "tracks",
        "track_layouts",
        "races",
        "race_participants",
        "laps",
        "sectors",
        "timing_configurations",
        "timing_sensors",
        "plugins",
        "settings",
        "alembic_version",
    } <= tables


def test_migrations_match_models(db: Database) -> None:
    import_plugin_models()
    with db.engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_models_of_all_plugins_are_registered() -> None:
    imported = import_plugin_models()
    assert "carrera.modules.races.models" in imported
    assert {"drivers", "races", "laps", "timing_sensors"} <= set(Base.metadata.tables)


def test_store_and_query_a_complete_race(db: Database) -> None:
    with db.session() as session:
        driver = Driver(name="Alice")
        vehicle = Vehicle(name="Ferrari", scale="1:32")
        track = Track(name="Home", lane_count=2)
        session.add_all([driver, vehicle, track])
        session.flush()
        session.add(TrackLayout(track_id=track.id, name="Oval", data={"pieces": []}))
        race = Race(name="Final", track_id=track.id, target_laps=3)
        session.add(race)
        session.flush()
        participant = RaceParticipant(
            race_id=race.id, driver_id=driver.id, vehicle_id=vehicle.id, lane=1
        )
        session.add(participant)
        session.flush()
        lap = Lap(
            race_id=race.id,
            participant_id=participant.id,
            lap_number=1,
            lap_time_ns=12_345_678_901,
            race_time_ns=12_345_678_901,
        )
        session.add(lap)
        session.flush()
        session.add(Sector(lap_id=lap.id, sector_number=1, sector_time_ns=4_000_000_000))
        configuration = TimingConfiguration(name="Sim", source_type="simulation", is_default=True)
        session.add(configuration)
        session.flush()
        session.add(
            TimingSensor(
                configuration_id=configuration.id,
                sensor_id="sf",
                role="START_FINISH",
                sequence_index=0,
            )
        )
        session.add_all(
            [PluginRecord(name="races", enabled=True), Setting(key="a", value={"x": 1})]
        )

    with db.session() as session:
        stored = session.scalars(select(Lap)).one()
        assert stored.lap_time_ns == 12_345_678_901
        assert session.get(Setting, "a") is not None
        assert session.scalars(select(Race)).one().status == "created"
        assert session.scalars(select(TrackLayout)).one().data == {"pieces": []}


def test_foreign_keys_are_enforced(db: Database) -> None:
    with pytest.raises(IntegrityError), db.session() as session:
        session.add(RaceParticipant(race_id=999, driver_id=999, lane=1))


def test_a_lane_can_only_be_used_once_per_race(db: Database) -> None:
    with pytest.raises(IntegrityError), db.session() as session:
        drivers = [Driver(name="A"), Driver(name="B")]
        race = Race(name="R")
        session.add_all([*drivers, race])
        session.flush()
        session.add(RaceParticipant(race_id=race.id, driver_id=drivers[0].id, lane=1))
        session.add(RaceParticipant(race_id=race.id, driver_id=drivers[1].id, lane=1))
        session.flush()


def test_session_rolls_back_on_error(db: Database) -> None:
    with pytest.raises(RuntimeError), db.session() as session:
        session.add(Driver(name="Ghost"))
        session.flush()
        raise RuntimeError("abort")
    with db.session() as session:
        assert session.scalars(select(Driver)).all() == []


def test_file_database_persists(tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "nested" / "carrera.db"
    first = Database.from_path(path)
    first.migrate()
    with first.session() as session:
        session.add(Driver(name="Persistent"))
    first.dispose()

    second = Database.from_path(path)
    second.migrate()
    with second.session() as session:
        assert session.scalars(select(Driver.name)).all() == ["Persistent"]
    second.dispose()
