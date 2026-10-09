from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from slot_racing.core.storage import Base, Database, PluginRecord, Setting, import_plugin_models
from slot_racing.core.storage.database import alembic_config
from slot_racing.modules.drivers_vehicles.models import Driver, Vehicle
from slot_racing.modules.races.models import Lap, Race, RaceParticipant, Sector
from slot_racing.modules.timing.models import TimingConfiguration, TimingSensor
from slot_racing.modules.tracks.models import Track, TrackLayout
from tests.database import migrated_database


@pytest.fixture
def db() -> Iterator[Database]:
    database = migrated_database()
    yield database
    database.dispose()


def test_copied_databases_do_not_share_rows() -> None:
    first = migrated_database()
    with first.session() as session:
        session.add(Driver(name="Only here"))
    second = migrated_database()
    with second.session() as session:
        assert session.scalars(select(Driver)).all() == []
    first.migrate()
    with first.session() as session:
        assert session.scalars(select(Driver.name)).one() == "Only here"
    first.dispose()
    second.dispose()


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
        "time_measurements",
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
    assert "slot_racing.modules.races.models" in imported
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
    path = tmp_path / "nested" / "slot_racing.db"
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


def test_upgrade_preserves_existing_data() -> None:
    database = Database.in_memory()
    database.migrate("0001")
    with database.engine.begin() as connection:
        for statement in (
            "INSERT INTO drivers (id, name, nickname, is_active) VALUES (1, 'Anna', 'Anni', 1)",
            "INSERT INTO vehicles (id, name, manufacturer) VALUES (1, 'Porsche', 'ExampleBrand')",
            "INSERT INTO tracks (id, name, lane_count) VALUES (1, 'Ring', 2)",
            "INSERT INTO races (id, name, track_id, status, target_laps) "
            "VALUES (1, 'Alt', 1, 'finished', 3)",
            "INSERT INTO race_participants (id, race_id, driver_id, vehicle_id, lane, "
            "final_position) VALUES (1, 1, 1, 1, 1, 1)",
            "INSERT INTO laps (id, race_id, participant_id, lap_number, lap_time_ns, "
            "race_time_ns) VALUES (1, 1, 1, 1, 5000000000, 5000000000)",
            "INSERT INTO sectors (id, lap_id, sector_number, sector_time_ns) "
            "VALUES (1, 1, 1, 2500000000)",
        ):
            connection.exec_driver_sql(statement)

    database.migrate()

    with database.session() as session:
        driver = session.get(Driver, 1)
        assert driver is not None
        assert (driver.name, driver.display_name, driver.is_active) == ("Anna", "Anni", True)
        assert driver.created_at is not None
        vehicle = session.get(Vehicle, 1)
        assert vehicle is not None and vehicle.manufacturer == "ExampleBrand" and vehicle.is_active
        track = session.get(Track, 1)
        assert track is not None and track.is_active and track.lane_count == 2
        race = session.get(Race, 1)
        assert race is not None and race.status == "finished" and race.track_id == 1
        assert race.mode == "laps"
        participant = session.get(RaceParticipant, 1)
        assert participant is not None and participant.final_position == 1
        assert participant.laps_completed == 0 and not participant.finished
        lap = session.get(Lap, 1)
        assert lap is not None and lap.lap_time_ns == 5_000_000_000
        sector = session.get(Sector, 1)
        assert sector is not None and sector.sector_time_ns == 2_500_000_000
    database.dispose()


def test_downgrade_restores_the_previous_schema() -> None:
    database = Database.in_memory()
    database.migrate()
    with database.session() as session:
        session.add(Driver(name="Anna", display_name="Anni", start_number=3))
    config = alembic_config()
    with database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0001")
    columns = {column["name"] for column in inspect(database.engine).get_columns("drivers")}
    assert "nickname" in columns and "display_name" not in columns
    with database.engine.connect() as connection:
        nickname = connection.exec_driver_sql("SELECT nickname FROM drivers").scalar_one()
    assert nickname == "Anni"
    database.dispose()
