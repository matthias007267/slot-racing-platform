"""Local backups of the application database and settings.

A backup is one ``.slbackup`` file: a zip archive with a manifest, the SQLite database and the
application settings. Track images that the user picked from outside the data folder stay
references inside the database. Files the application itself stores under ``assets`` next to the
database (for a later track planner, for example) are included when that folder exists.

Nothing here copies the database a second time into another table. Restoring replaces the current
files only after the archive has been checked and a safety copy of the current data exists.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from alembic.script import ScriptDirectory
from sqlalchemy import Connection

from slot_racing import __version__
from slot_racing.core.config import AppConfig, save_config
from slot_racing.core.diagnostics import record
from slot_racing.core.errors import ValidationError
from slot_racing.core.storage.database import Database, alembic_config

logger = logging.getLogger(__name__)

FORMAT_ID = "slot-racing-backup"
FORMAT_VERSION = 1
BACKUP_SUFFIX = ".slbackup"

_DATABASE_MEMBER = "database.sqlite"
_MANIFEST_MEMBER = "manifest.json"
_CONFIG_MEMBER = "config.json"
_ASSET_PREFIX = "assets/"

BackupKind = Literal["manual", "auto", "safety"]
ScheduleReason = Literal["startup", "shutdown"]

# Tables a current database must contain. A backup at the current schema revision is rejected
# when one of them is missing, so a damaged file cannot be applied halfway.
REQUIRED_TABLES = frozenset(
    {
        "alembic_version",
        "drivers",
        "vehicles",
        "tracks",
        "track_layouts",
        "track_part_definitions",
        "track_part_connectors",
        "track_plan_instances",
        "track_part_stock",
        "timing_configurations",
        "timing_positions",
        "timing_sensors",
        "races",
        "race_participants",
        "race_heats",
        "race_heat_entries",
        "laps",
        "sectors",
        "time_measurements",
        "plugins",
        "settings",
    }
)


class BackupError(ValidationError):
    """A backup could not be created or restored. The current database is left in place."""


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Description stored beside the database inside the archive."""

    format_version: int
    app_version: str
    created_at: str
    schema_revision: str
    contents: tuple[str, ...]

    def to_json(self) -> str:
        payload = {
            "format": FORMAT_ID,
            "format_version": self.format_version,
            "app_version": self.app_version,
            "created_at": self.created_at,
            "schema_revision": self.schema_revision,
            "contents": list(self.contents),
        }
        return json.dumps(payload, indent=2)


@dataclass(frozen=True, slots=True)
class OpenedBackup:
    manifest: BackupManifest
    database_file: Path
    config_payload: dict[str, object] | None
    assets_dir: Path | None


def create_backup(
    database: Database,
    config: AppConfig,
    directory: Path,
    *,
    kind: BackupKind = "manual",
    moment: datetime | None = None,
) -> Path:
    """Write one backup file into ``directory`` and return its path.

    The database file location is not changed. Automatic backups are pruned to
    ``config.backup_keep``; manual backups and safety copies stay.
    """
    directory.mkdir(parents=True, exist_ok=True)
    record("BACKUP_START", module="backup", result="started", kind=kind)
    created = _utc(moment)
    destination = _unique_path(directory, _filename(created, kind))
    temporary = destination.with_suffix(destination.suffix + ".partial")
    try:
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder) / _DATABASE_MEMBER
            _snapshot_database(database, snapshot)
            _require_intact(snapshot)
            revision = _schema_revision(snapshot)
            assets = _asset_files(database)
            contents = ["database", "config"]
            if assets:
                contents.append("assets")
            manifest = BackupManifest(
                format_version=FORMAT_VERSION,
                app_version=__version__,
                created_at=created.isoformat(),
                schema_revision=revision,
                contents=tuple(contents),
            )
            _write_archive(temporary, manifest, snapshot, _config_payload(config), assets)
        temporary.replace(destination)
        validate_backup(destination)
    except Exception as error:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        record("BACKUP_FAILED", module="backup", result=type(error).__name__, kind=kind)
        raise
    if kind == "auto":
        _prune_automatic(directory, config.backup_keep)
    record("BACKUP_COMPLETE", module="backup", result="written", kind=kind)
    return destination


def validate_backup(path: Path) -> BackupManifest:
    """Read ``path`` and reject anything that is not a supported, intact backup."""
    with open_backup(path) as opened:
        return opened.manifest


@contextmanager
def open_backup(path: Path) -> Iterator[OpenedBackup]:
    """Validate ``path`` and expose the extracted database until the caller is done."""
    folder = Path(tempfile.mkdtemp(prefix="slot-racing-backup-"))
    try:
        yield _extract_and_check(path, folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def restore_backup(
    archive: Path,
    database: Database,
    config: AppConfig,
    config_path: Path | None,
    *,
    backup_directory: Path,
    before_swap: Callable[[], None] | None = None,
) -> Path:
    """Replace the current database with ``archive``.

    The archive is checked first. A safety copy of the current data is written next. Only then
    is the database file exchanged. A failure rolls the previous file back.
    """
    record("RESTORE_START", module="backup", result="started")
    target = database.file_path()
    if target is None:
        record("RESTORE_FAILED", module="backup", result="no_database_file")
        raise BackupError("error.backup.no_database_file")
    try:
        with open_backup(archive) as opened:
            safety = _safety_copy(database, config, backup_directory)
            incoming = target.with_name(target.name + ".incoming")
            try:
                _prepare_incoming(opened.database_file, incoming)
                if before_swap is not None:
                    before_swap()
                _swap_in(database, target, incoming, opened, config, config_path)
            except BackupError:
                incoming.unlink(missing_ok=True)
                raise
            except Exception as error:
                incoming.unlink(missing_ok=True)
                raise BackupError("error.backup.failed") from error
            finally:
                incoming.unlink(missing_ok=True)
    except Exception as error:
        record("RESTORE_FAILED", module="backup", result=type(error).__name__)
        raise
    record("RESTORE_COMPLETE", module="backup", result="restored")
    return safety


def run_scheduled_backup(
    database: Database,
    config: AppConfig,
    *,
    reason: ScheduleReason,
    moment: datetime | None = None,
) -> Path | None:
    """Create an automatic backup when the schedule says so. Failures are not hidden."""
    schedule = config.backup_schedule
    if schedule == "off":
        return None
    if reason == "startup" and schedule != "daily":
        return None
    if reason == "shutdown" and schedule != "on_exit":
        return None
    if database.file_path() is None:
        return None
    created = _utc(moment)
    directory = config.resolved_backup_directory()
    if schedule == "daily" and _has_auto_backup_on(directory, created.date().isoformat()):
        return None
    return create_backup(database, config, directory, kind="auto", moment=created)


def _safety_copy(database: Database, config: AppConfig, directory: Path) -> Path:
    try:
        return create_backup(database, config, directory, kind="safety")
    except BackupError:
        raise
    except Exception as error:
        raise BackupError("error.backup.failed") from error


def _prepare_incoming(source: Path, incoming: Path) -> None:
    """Copy the backup database aside and migrate it before it replaces the live file."""
    incoming.unlink(missing_ok=True)
    shutil.copy2(source, incoming)
    prepared = Database.from_path(incoming)
    try:
        prepared.migrate()
        _checkpoint(prepared)
        _require_tables(incoming, REQUIRED_TABLES)
        _require_intact(incoming)
    except BackupError:
        raise
    except Exception as error:
        raise BackupError("error.backup.corrupt") from error
    finally:
        prepared.dispose()
        _remove_sidecars(incoming)


def _swap_in(
    database: Database,
    target: Path,
    incoming: Path,
    opened: OpenedBackup,
    config: AppConfig,
    config_path: Path | None,
) -> None:
    previous = target.with_name(target.name + ".previous")
    previous_config = config.model_copy(deep=True)
    assets_root = target.parent / "assets"
    assets_previous = target.parent / "assets.previous"
    _checkpoint(database)
    database.dispose()
    _remove_sidecars(target)
    moved = False
    try:
        if previous.exists():
            previous.unlink()
        os_replace(target, previous)
        moved = True
        os_replace(incoming, target)
        _remove_sidecars(target)
        database.reopen()
        _require_intact(target)
        _require_tables(target, REQUIRED_TABLES)
        _swap_assets(opened.assets_dir, assets_root, assets_previous)
        if opened.config_payload is not None:
            _apply_config(config, opened.config_payload)
            if config_path is not None:
                save_config(config, config_path)
    except Exception:
        if moved:
            _restore_previous(database, target, previous, assets_root, assets_previous)
        else:
            database.reopen()
        _copy_config(previous_config, config)
        if config_path is not None:
            with suppress(OSError):
                save_config(config, config_path)
        raise
    else:
        previous.unlink(missing_ok=True)
        if assets_previous.exists():
            shutil.rmtree(assets_previous, ignore_errors=True)


def _restore_previous(
    database: Database,
    target: Path,
    previous: Path,
    assets_root: Path,
    assets_previous: Path,
) -> None:
    database.dispose()
    if target.exists():
        target.unlink()
    if previous.exists():
        os_replace(previous, target)
    _remove_sidecars(target)
    if assets_root.exists():
        shutil.rmtree(assets_root, ignore_errors=True)
    if assets_previous.exists():
        os_replace(assets_previous, assets_root)
    database.reopen()


def _swap_assets(source: Path | None, root: Path, previous: Path) -> None:
    if previous.exists():
        shutil.rmtree(previous)
    if root.exists():
        os_replace(root, previous)
    if source is None:
        return
    shutil.copytree(source, root)


def os_replace(source: Path, destination: Path) -> None:
    """Replace ``destination`` with ``source``. Tests can patch this."""
    source.replace(destination)


def _apply_config(config: AppConfig, payload: dict[str, object]) -> None:
    """Copy restored settings onto ``config`` and keep the current database path."""
    restored = AppConfig.model_validate(payload)
    database_path = config.database_path
    for name in type(config).model_fields:
        if name == "database_path":
            continue
        setattr(config, name, getattr(restored, name))
    config.database_path = database_path


def _copy_config(source: AppConfig, target: AppConfig) -> None:
    for name in type(target).model_fields:
        setattr(target, name, getattr(source, name))


def _extract_and_check(path: Path, folder: Path) -> OpenedBackup:
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise BackupError("error.backup.invalid")
    try:
        with zipfile.ZipFile(path) as archive:
            _require_safe_names(archive)
            manifest = _read_manifest(archive)
            database_member = archive.getinfo(_DATABASE_MEMBER)
            archive.extract(database_member, folder)
            config_payload = _read_config_member(archive)
            assets_dir = _extract_assets(archive, folder)
    except BackupError:
        raise
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeError, ValueError) as error:
        raise BackupError("error.backup.invalid") from error
    database_file = folder / _DATABASE_MEMBER
    try:
        _require_intact(database_file)
        revision = _schema_revision(database_file)
    except BackupError:
        raise
    except Exception as error:
        raise BackupError("error.backup.corrupt") from error
    if revision != manifest.schema_revision:
        raise BackupError("error.backup.corrupt")
    if revision not in _known_revisions():
        raise BackupError("error.backup.unsupported_version")
    if revision == _schema_head():
        _require_tables(database_file, REQUIRED_TABLES)
    return OpenedBackup(
        manifest=manifest,
        database_file=database_file,
        config_payload=config_payload,
        assets_dir=assets_dir,
    )


def _read_manifest(archive: zipfile.ZipFile) -> BackupManifest:
    try:
        raw = archive.read(_MANIFEST_MEMBER)
    except KeyError as error:
        raise BackupError("error.backup.invalid") from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BackupError("error.backup.invalid") from error
    if not isinstance(payload, dict) or payload.get("format") != FORMAT_ID:
        raise BackupError("error.backup.invalid")
    version = payload.get("format_version")
    if version != FORMAT_VERSION:
        raise BackupError("error.backup.unsupported_version")
    app_version = payload.get("app_version")
    created_at = payload.get("created_at")
    schema_revision = payload.get("schema_revision")
    contents = payload.get("contents")
    if (
        not isinstance(app_version, str)
        or not app_version
        or not isinstance(created_at, str)
        or not created_at
        or not isinstance(schema_revision, str)
        or not schema_revision
        or not isinstance(contents, list)
        or "database" not in contents
        or any(not isinstance(item, str) for item in contents)
    ):
        raise BackupError("error.backup.invalid")
    return BackupManifest(
        format_version=FORMAT_VERSION,
        app_version=app_version,
        created_at=created_at,
        schema_revision=schema_revision,
        contents=tuple(cast(list[str], contents)),
    )


def _read_config_member(archive: zipfile.ZipFile) -> dict[str, object] | None:
    if _CONFIG_MEMBER not in archive.namelist():
        return None
    payload = json.loads(archive.read(_CONFIG_MEMBER).decode("utf-8"))
    if not isinstance(payload, dict):
        raise BackupError("error.backup.invalid")
    return cast(dict[str, object], payload)


def _extract_assets(archive: zipfile.ZipFile, folder: Path) -> Path | None:
    names = [name for name in archive.namelist() if name.startswith(_ASSET_PREFIX)]
    if not names:
        return None
    destination = folder / "assets"
    for name in names:
        info = archive.getinfo(name)
        if info.is_dir():
            continue
        archive.extract(info, folder)
    return destination if destination.exists() else None


def _require_safe_names(archive: zipfile.ZipFile) -> None:
    for info in archive.infolist():
        name = info.filename
        if name.endswith("/"):
            name = name[:-1]
        parts = Path(name).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise BackupError("error.backup.invalid")
        allowed = name in {_MANIFEST_MEMBER, _DATABASE_MEMBER, _CONFIG_MEMBER}
        if not allowed and not name.startswith(_ASSET_PREFIX):
            raise BackupError("error.backup.invalid")


def _write_archive(
    path: Path,
    manifest: BackupManifest,
    database_file: Path,
    config_payload: dict[str, object],
    assets: list[tuple[str, Path]],
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_MANIFEST_MEMBER, manifest.to_json())
        archive.write(database_file, _DATABASE_MEMBER)
        archive.writestr(_CONFIG_MEMBER, json.dumps(config_payload, indent=2, ensure_ascii=False))
        for relative, source in assets:
            archive.write(source, f"{_ASSET_PREFIX}{relative}")


def _config_payload(config: AppConfig) -> dict[str, object]:
    """Settings worth restoring. The database path stays a property of this installation."""
    payload = config.model_dump(mode="json", exclude={"database_path"})
    return cast(dict[str, object], payload)


def _snapshot_database(database: Database, destination: Path) -> None:
    connection = database.engine.connect()
    try:
        raw = _sqlite_connection(connection)
        with _connect(destination) as target:
            raw.backup(target)
    finally:
        connection.close()


def _sqlite_connection(connection: Connection) -> sqlite3.Connection:
    raw = connection.connection
    candidate = getattr(raw, "driver_connection", raw)
    if isinstance(candidate, sqlite3.Connection):
        return candidate
    raise BackupError("error.backup.no_database_file")


def _checkpoint(database: Database) -> None:
    with database.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")


def _require_intact(path: Path) -> None:
    try:
        with _connect(path) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as error:
        raise BackupError("error.backup.corrupt") from error
    if row is None or row[0] != "ok":
        raise BackupError("error.backup.corrupt")


def _require_tables(path: Path, required: frozenset[str]) -> None:
    with _connect(path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    present = {row[0] for row in rows}
    if not required <= present:
        raise BackupError("error.backup.missing_tables")


def _schema_revision(path: Path) -> str:
    try:
        with _connect(path) as connection:
            rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except sqlite3.DatabaseError as error:
        raise BackupError("error.backup.corrupt") from error
    if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0]:
        raise BackupError("error.backup.corrupt")
    return rows[0][0]


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite file and close it before the caller can delete or replace it.

    ``with sqlite3.connect(...)`` only commits. On Windows an unclosed handle blocks removal
    of the file and of the temporary directory that contains it.
    """
    connection = sqlite3.connect(path)
    try:
        yield connection
    finally:
        connection.close()


def _known_revisions() -> frozenset[str]:
    script = ScriptDirectory.from_config(alembic_config())
    return frozenset(revision.revision for revision in script.walk_revisions())


def _schema_head() -> str:
    script = ScriptDirectory.from_config(alembic_config())
    head = script.get_current_head()
    if head is None:
        raise BackupError("error.backup.corrupt")
    return head


def _asset_files(database: Database) -> list[tuple[str, Path]]:
    """Files stored by the application next to the database, under ``assets``."""
    database_path = database.file_path()
    if database_path is None:
        return []
    root = database_path.parent / "assets"
    if not root.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if ".." in relative.split("/"):
            continue
        found.append((relative, path))
    return found


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        sidecar.unlink(missing_ok=True)


def _filename(moment: datetime, kind: BackupKind) -> str:
    stamp = moment.strftime("%Y-%m-%dT%H%M%SZ")
    suffix = "" if kind == "manual" else f"-{kind}"
    return f"slot-racing-backup-{stamp}{suffix}{BACKUP_SUFFIX}"


def _unique_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    for index in range(2, 100):
        alternative = directory / f"{stem}-{index}{BACKUP_SUFFIX}"
        if not alternative.exists():
            return alternative
    raise BackupError("error.backup.failed")


def _has_auto_backup_on(directory: Path, day: str) -> bool:
    if not directory.is_dir():
        return False
    return any(directory.glob(f"slot-racing-backup-{day}T*-auto{BACKUP_SUFFIX}"))


def _prune_automatic(directory: Path, keep: int) -> None:
    files = sorted(directory.glob(f"slot-racing-backup-*-auto{BACKUP_SUFFIX}"))
    for path in files[: max(0, len(files) - keep)]:
        path.unlink(missing_ok=True)


def _utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
