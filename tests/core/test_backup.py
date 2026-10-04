"""Backups contain the stored application data and restore it without a partial write."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import slot_racing.core.backup as backup_module
from slot_racing import __version__
from slot_racing.app.runtime import Runtime
from slot_racing.core.backup import (
    FORMAT_VERSION,
    REQUIRED_TABLES,
    BackupError,
    create_backup,
    os_replace,
    restore_backup,
    run_scheduled_backup,
    validate_backup,
)
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import ParticipantResult
from slot_racing.core.storage import Database
from slot_racing.modules.drivers_vehicles.service import (
    DriverInput,
    DriverService,
    VehicleInput,
    VehicleService,
)
from slot_racing.modules.races.service import RaceService
from slot_racing.modules.tracks.service import TrackInput, TrackService


@pytest.fixture
def stored(tmp_path: Path) -> Iterator[Runtime]:
    config = AppConfig(
        database_path=tmp_path / "slot_racing.db",
        backup_directory=tmp_path / "backups",
        language="de",
    )
    runtime = Runtime.create(config, config_path=tmp_path / "config.json")
    yield runtime
    runtime.shutdown()


def test_a_backup_of_ordinary_data_is_one_valid_file(stored: Runtime, tmp_path: Path) -> None:
    _seed(stored)
    path = create_backup(stored.database, stored.config, tmp_path / "backups")
    assert path.name.startswith("slot-racing-backup-")
    assert path.suffix == ".slbackup"
    manifest = validate_backup(path)
    assert manifest.format_version == FORMAT_VERSION
    assert manifest.app_version == __version__
    assert manifest.schema_revision
    assert "database" in manifest.contents
    assert "config" in manifest.contents
    with zipfile.ZipFile(path) as archive:
        payload = json.loads(archive.read("manifest.json"))
        config = json.loads(archive.read("config.json"))
    assert payload["format"] == "slot-racing-backup"
    assert payload["format_version"] == 1
    assert payload["app_version"] == __version__
    assert payload["created_at"]
    assert payload["schema_revision"] == manifest.schema_revision
    assert "database_path" not in config


def test_backup_and_restore_keep_drivers_tracks_races_and_times(
    stored: Runtime, tmp_path: Path
) -> None:
    original = _seed(stored)
    archive = create_backup(stored.database, stored.config, tmp_path / "backups")
    _assert_archive_contains_seed(archive, original)

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = AppConfig(
        database_path=other_dir / "slot_racing.db",
        backup_directory=other_dir / "backups",
        language="en",
    )
    runtime = Runtime.create(other, config_path=other_dir / "config.json")
    try:
        runtime.services.get(DriverService).create_driver(DriverInput(name="Lisa"))
        safety = restore_backup(
            archive,
            runtime.database,
            runtime.config,
            runtime.config_path,
            backup_directory=other.resolved_backup_directory(),
        )
        assert safety.name.endswith("-safety.slbackup")
        assert validate_backup(safety).schema_revision
        _assert_seed(runtime, original)
        assert runtime.config.language == "de"
        assert runtime.config.database_path == other.database_path
        assert runtime.database.file_path() == other.database_path
        runtime.database.migrate()
        _assert_seed(runtime, original)
    finally:
        runtime.shutdown()


def test_an_invalid_file_is_rejected_and_keeps_the_database(
    stored: Runtime, tmp_path: Path
) -> None:
    _seed(stored)
    bogus = tmp_path / "notes.slbackup"
    bogus.write_text("not a backup", encoding="utf-8")
    before = _names(stored)
    with pytest.raises(BackupError) as caught:
        restore_backup(
            bogus,
            stored.database,
            stored.config,
            stored.config_path,
            backup_directory=tmp_path / "backups",
        )
    assert caught.value.key == "error.backup.invalid"
    assert _names(stored) == before
    assert list((tmp_path / "backups").glob("*-safety.slbackup")) == []


def test_an_unsupported_backup_version_is_rejected(stored: Runtime, tmp_path: Path) -> None:
    _seed(stored)
    archive = create_backup(stored.database, stored.config, tmp_path / "backups")
    newer = tmp_path / "newer.slbackup"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(newer, "w") as target:
        payload = json.loads(source.read("manifest.json"))
        payload["format_version"] = 99
        target.writestr("manifest.json", json.dumps(payload))
        target.writestr("database.sqlite", source.read("database.sqlite"))
        target.writestr("config.json", source.read("config.json"))
    before = _names(stored)
    with pytest.raises(BackupError) as caught:
        restore_backup(
            newer,
            stored.database,
            stored.config,
            stored.config_path,
            backup_directory=tmp_path / "backups",
        )
    assert caught.value.key == "error.backup.unsupported_version"
    assert _names(stored) == before


def test_a_backup_missing_required_tables_is_rejected(stored: Runtime, tmp_path: Path) -> None:
    _seed(stored)
    archive = create_backup(stored.database, stored.config, tmp_path / "backups")
    broken = tmp_path / "broken.slbackup"
    with zipfile.ZipFile(archive) as source:
        database = source.read("database.sqlite")
        manifest = source.read("manifest.json")
        config = source.read("config.json")
    raw = tmp_path / "broken.sqlite"
    raw.write_bytes(database)
    with sqlite3.connect(raw) as connection:
        connection.execute("DROP TABLE time_measurements")
    with zipfile.ZipFile(broken, "w") as target:
        target.writestr("manifest.json", manifest)
        target.write(raw, "database.sqlite")
        target.writestr("config.json", config)
    before = _names(stored)
    with pytest.raises(BackupError) as caught:
        restore_backup(
            broken,
            stored.database,
            stored.config,
            stored.config_path,
            backup_directory=tmp_path / "safety",
        )
    assert caught.value.key == "error.backup.missing_tables"
    assert _names(stored) == before


def test_a_failed_replace_keeps_the_current_database(
    stored: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(stored)
    archive = create_backup(stored.database, stored.config, tmp_path / "backups")
    calls = {"count": 0}
    real = os_replace

    def fail_second(source: Path, destination: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("disk full")
        real(source, destination)

    monkeypatch.setattr(backup_module, "os_replace", fail_second)
    before = _names(stored)
    with pytest.raises(BackupError) as caught:
        restore_backup(
            archive,
            stored.database,
            stored.config,
            stored.config_path,
            backup_directory=tmp_path / "backups",
        )
    assert caught.value.key == "error.backup.failed"
    assert _names(stored) == before
    safety = list((tmp_path / "backups").glob("*-safety.slbackup"))
    assert len(safety) == 1
    assert validate_backup(safety[0]).format_version == FORMAT_VERSION


def test_assets_next_to_the_database_roundtrip(stored: Runtime, tmp_path: Path) -> None:
    database_path = stored.database.file_path()
    assert database_path is not None
    asset = database_path.parent / "assets" / "planner" / "note.txt"
    asset.parent.mkdir(parents=True)
    asset.write_text("layout", encoding="utf-8")
    archive = create_backup(stored.database, stored.config, tmp_path / "backups")
    asset.write_text("changed", encoding="utf-8")
    extra = asset.parent / "other.txt"
    extra.write_text("later", encoding="utf-8")
    restore_backup(
        archive,
        stored.database,
        stored.config,
        stored.config_path,
        backup_directory=tmp_path / "backups",
    )
    assert asset.read_text(encoding="utf-8") == "layout"
    assert not extra.exists()


def test_required_tables_are_the_tables_of_a_migrated_database(tmp_path: Path) -> None:
    database = Database.from_path(tmp_path / "fresh.sqlite")
    database.migrate()
    with sqlite3.connect(tmp_path / "fresh.sqlite") as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    database.dispose()
    assert {row[0] for row in rows} >= REQUIRED_TABLES


def test_automatic_backups_are_real_backups_and_keep_a_limit(
    stored: Runtime, tmp_path: Path
) -> None:
    stored.config.backup_schedule = "daily"
    stored.config.backup_keep = 1
    directory = tmp_path / "backups"
    first_day = datetime(2026, 10, 4, 8, 0, tzinfo=UTC)
    created = run_scheduled_backup(
        stored.database, stored.config, reason="startup", moment=first_day
    )
    assert created is not None
    assert validate_backup(created).format_version == FORMAT_VERSION
    assert (
        run_scheduled_backup(stored.database, stored.config, reason="startup", moment=first_day)
        is None
    )
    skipped = run_scheduled_backup(
        stored.database, stored.config, reason="shutdown", moment=first_day
    )
    assert skipped is None
    second_day = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    newer = run_scheduled_backup(
        stored.database, stored.config, reason="startup", moment=second_day
    )
    assert newer is not None
    remaining = sorted(directory.glob("*-auto.slbackup"))
    assert remaining == [newer]
    stored.config.backup_schedule = "on_exit"
    exited = run_scheduled_backup(
        stored.database, stored.config, reason="shutdown", moment=second_day
    )
    assert exited is not None
    assert validate_backup(exited).contents


def test_exit_backup_runs_from_the_runtime(tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    config = AppConfig(
        database_path=tmp_path / "slot_racing.db",
        backup_directory=directory,
        backup_schedule="on_exit",
    )
    runtime = Runtime.create(config, config_path=tmp_path / "config.json")
    runtime.shutdown()
    files = list(directory.glob("*-auto.slbackup"))
    assert len(files) == 1
    assert validate_backup(files[0]).format_version == FORMAT_VERSION


def test_the_backup_folder_does_not_move_the_database(tmp_path: Path) -> None:
    database_path = tmp_path / "slot_racing.db"
    config = AppConfig(database_path=database_path)
    chosen = tmp_path / "elsewhere"
    config.backup_directory = chosen
    assert config.resolved_database_path() == database_path
    assert config.resolved_backup_directory() == chosen


def _seed(runtime: Runtime) -> dict[str, object]:
    drivers = runtime.services.get(DriverService)
    vehicles = runtime.services.get(VehicleService)
    tracks = runtime.services.get(TrackService)
    races = runtime.services.get(RaceService)
    driver = drivers.create_driver(DriverInput(name="Max", start_number=7))
    vehicle = vehicles.create_vehicle(
        VehicleInput(
            name="Porsche 911",
            model="911",
            manufacturer="Porsche",
            driver_id=driver.id,
            is_favorite=True,
            start_number=7,
        )
    )
    track = tracks.create_track(TrackInput(name="Meine Strecke", lane_count=3))
    trial = races.create_time_trial("Training", track.id)
    races.add_participant(trial.id, driver.id, vehicle.id, 1)
    races.record_lap(trial.id, 1, 1, 8_421_000_000, 8_421_000_000, {1: 4_000_000_000})
    cup = races.create_race("Sonntag", track.id, 5)
    races.add_participant(cup.id, driver.id, vehicle.id, 2)
    races.record_lap(cup.id, 2, 1, 9_000_000_000, 9_000_000_000, {})
    races.record_finished(
        cup.id,
        [
            ParticipantResult(
                driver_id=driver.id,
                lane=2,
                position=1,
                laps_completed=1,
                finished=False,
                total_time_ns=9_000_000_000,
                best_lap_ns=9_000_000_000,
            )
        ],
        aborted=False,
    )
    return {
        "driver": driver.name,
        "start_number": driver.start_number,
        "vehicle": vehicle.label,
        "favorite": vehicle.is_favorite,
        "track": track.name,
        "lanes": track.lane_count,
        "trial": trial.name,
        "cup": cup.name,
    }


def _assert_seed(runtime: Runtime, original: dict[str, object]) -> None:
    drivers = runtime.services.get(DriverService)
    vehicles = runtime.services.get(VehicleService)
    tracks = runtime.services.get(TrackService)
    races = runtime.services.get(RaceService)
    driver = next(item for item in drivers.list_drivers() if item.name == "Max")
    vehicle = next(item for item in vehicles.list_vehicles() if item.is_favorite)
    track = next(item for item in tracks.list_tracks() if item.name == "Meine Strecke")
    trial = next(item for item in races.list_races() if item.name == "Training")
    cup = next(item for item in races.list_races() if item.name == "Sonntag")
    measurement = races.list_time_measurements(track_id=track.id)[0]
    results = races.get_results(cup.id)
    laps = races.get_laps(cup.id)
    assert driver.start_number == original["start_number"] == 7
    assert vehicle.label == original["vehicle"]
    assert vehicle.is_favorite is True
    assert vehicle.driver_id == driver.id
    assert track.lane_count == original["lanes"] == 3
    assert measurement.lane == 1
    assert measurement.time_ns == 8_421_000_000
    assert measurement.driver_id == driver.id
    assert measurement.vehicle_id == vehicle.id
    assert results[0].lane == 2
    assert results[0].best_lap_ns == 9_000_000_000
    assert results[0].position == 1
    assert laps[0].lap_time_ns == 9_000_000_000
    assert laps[0].lane == 2
    assert trial.track_id == track.id
    assert cup.track_id == track.id


def _assert_archive_contains_seed(path: Path, original: dict[str, object]) -> None:
    with zipfile.ZipFile(path) as archive:
        database = archive.extract("database.sqlite", path.parent / "peek")
    with sqlite3.connect(database) as connection:
        driver = connection.execute("SELECT name, start_number FROM drivers").fetchone()
        lanes = connection.execute("SELECT lane_count FROM tracks").fetchone()
        favorite = connection.execute("SELECT is_favorite FROM vehicles").fetchone()
        times = connection.execute("SELECT COUNT(*) FROM time_measurements").fetchone()
        lap_count = connection.execute("SELECT COUNT(*) FROM laps").fetchone()
    assert driver is not None and driver[0] == original["driver"] and driver[1] == 7
    assert lanes == (original["lanes"],)
    assert favorite == (1,)
    assert times == (1,)
    assert lap_count == (2,)


def _names(runtime: Runtime) -> list[str]:
    return [driver.name for driver in runtime.services.get(DriverService).list_drivers()]
