"""Process-wide diagnostics: logging, breadcrumbs, the session marker and bundles.

Modules call :func:`record`. They do not open log files themselves. Nothing here
imports Qt. The application installs one instance at startup and clears it on
shutdown.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import platform
import sys
import tempfile
import threading
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from slot_racing import __version__
from slot_racing.core.config import AppConfig
from slot_racing.core.diagnostics.breadcrumbs import DEFAULT_CAPACITY, BreadcrumbLog
from slot_racing.core.diagnostics.bundle import (
    FAULT_NAME,
    log_candidates,
    write_support_bundle,
)
from slot_racing.core.diagnostics.hooks import (
    DialogGate,
    install_hooks,
    restore_hooks,
    set_exception_presenter,
)
from slot_racing.core.diagnostics.marker import (
    clean_marker,
    marker_path,
    read_marker,
    running_marker,
    write_marker,
)
from slot_racing.core.diagnostics.report import config_summary, render_report
from slot_racing.core.diagnostics.sanitize import sanitize_text

logger = logging.getLogger(__name__)

MAX_LOG_BYTES = 1_048_576
LOG_BACKUPS = 5
_HANDLER_NAME = "slot-racing-file"
_STREAM_NAME = "slot-racing-stderr"
_FILTER_NAME = "slot-racing-session"

_lock = threading.Lock()
_current: Diagnostics | None = None


@dataclass(slots=True)
class Diagnostics:
    """One running session. Create it with :func:`install`."""

    session_id: str
    data_dir: Path
    log_directory: Path | None
    log_file: Path | None
    fault_file: Path | None
    previous_unclean: bool
    smoke_test: bool
    debug: bool
    max_bytes: int
    backup_count: int
    breadcrumbs: BreadcrumbLog
    started_at: str
    _marker_file: Path = field(repr=False)
    facts: dict[str, str] = field(default_factory=dict)
    crash_ids: deque[str] = field(default_factory=lambda: deque(maxlen=20))
    log_fallback: str = "none"
    _fault_handle: object | None = field(default=None, repr=False)
    _previous_level: int = field(default=logging.WARNING, repr=False)
    _previous_sys: Callable[..., object] | None = field(default=None, repr=False)
    _previous_thread: Callable[..., object] | None = field(default=None, repr=False)
    _gate: DialogGate = field(default_factory=DialogGate, repr=False)
    _faults_were_enabled: bool = False
    _closed: bool = False
    _clean: bool = False
    _bundling: bool = False

    def note_fact(self, key: str, value: object) -> None:
        self.facts[key] = sanitize_text(str(value))

    def add_breadcrumb(
        self,
        event: str,
        *,
        module: str,
        page: str = "",
        result: str = "",
        fields: Mapping[str, object] | None = None,
    ) -> None:
        item = self.breadcrumbs.add(event, module=module, page=page, result=result, fields=fields)
        logger.info("%s", item.render())

    def remember_crash(self, crash_id: str, thread_name: str) -> None:
        if crash_id:
            self.crash_ids.append(crash_id)
        self.add_breadcrumb(
            "UNCAUGHT_EXCEPTION",
            module="diagnostics",
            result="logged",
            fields={"crash_id": crash_id, "thread": thread_name},
        )

    def mark_clean(self) -> None:
        """Record a normal end. A process that never reaches this stays unclean."""
        if self._clean:
            return
        self._clean = True
        logger.info("APP_SHUTDOWN session_id=%s", self.session_id)
        self._flush()
        try:
            current = read_marker(self._marker_file)
            source = current if current is not None else running_marker(self.session_id)
            write_marker(self._marker_file, clean_marker(source))
        except OSError:
            logger.error("Could not store the clean session marker")

    def write_bundle(self, destination: Path, *, config: AppConfig | None = None) -> Path:
        """Create a support zip. Failures are logged once and re-raised."""
        if self._bundling:
            raise RuntimeError("a support bundle is already being written")
        self._bundling = True
        try:
            notes = self._write_bundle(destination, config)
            self.add_breadcrumb(
                "DIAGNOSTICS_BUNDLE",
                module="diagnostics",
                result="written",
                fields={"notes": len(notes)},
            )
            return destination
        except Exception:
            logger.exception("Could not write the support bundle")
            raise
        finally:
            self._bundling = False

    def close(self) -> None:
        """Release log files and hooks. Does not mark the session clean."""
        if self._closed:
            return
        self._closed = True
        self._flush()
        root = logging.getLogger()
        for handler in list(root.handlers):
            if handler.name in (_HANDLER_NAME, _STREAM_NAME):
                root.removeHandler(handler)
                handler.close()
        root.filters = [
            item for item in root.filters if getattr(item, "slot_racing_filter", "") != _FILTER_NAME
        ]
        root.setLevel(self._previous_level)
        if self._previous_sys is not None and self._previous_thread is not None:
            restore_hooks(self._previous_sys, self._previous_thread)
        set_exception_presenter(None)
        self._disable_faults()
        global _current
        with _lock:
            if _current is self:
                _current = None

    def _write_bundle(self, destination: Path, config: AppConfig | None) -> tuple[str, ...]:
        logs = [path for path in log_candidates(self.log_directory, self.backup_count)]
        existing = [path for path in logs if path.is_file()]
        missing = [path for path in logs[:1] if not path.is_file()]
        pre_notes = [f"log {path.name} unavailable" for path in missing]
        report = render_report(
            self.facts, previous_unclean=self.previous_unclean, log_notes=pre_notes
        )
        summary = config_summary(config) if config is not None else "Sanitized configuration\n\n"
        return write_support_bundle(
            destination,
            report_facts=report,
            breadcrumbs=self.breadcrumbs.render(),
            config_text=summary,
            logs=existing,
            fault_file=(
                self.fault_file
                if self.fault_file is not None and self.fault_file.is_file()
                else None
            ),
        )

    def _flush(self) -> None:
        for handler in logging.getLogger().handlers:
            if handler.name in (_HANDLER_NAME, _STREAM_NAME):
                handler.flush()

    def _disable_faults(self) -> None:
        if self._fault_handle is None:
            return
        faulthandler.disable()
        close = getattr(self._fault_handle, "close", None)
        self._fault_handle = None
        if callable(close):
            close()
        if self._faults_were_enabled:
            faulthandler.enable(all_threads=True)


class _SessionFilter(logging.Filter):
    """Adds the session id. The filter name stays empty so every logger is kept."""

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.slot_racing_filter = _FILTER_NAME
        self._session_id = session_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = self._session_id
        return True


def install(
    data_dir: Path,
    *,
    debug: bool = False,
    smoke_test: bool = False,
    max_bytes: int = MAX_LOG_BYTES,
    backup_count: int = LOG_BACKUPS,
    breadcrumb_capacity: int = DEFAULT_CAPACITY,
) -> Diagnostics:
    """Start a session. Logging problems degrade to stderr and memory, never a crash."""
    old = current()
    if old is not None:
        old.close()
    session_id = uuid.uuid4().hex
    marker_file = marker_path(data_dir)
    previous = _previous_unclean(marker_file)
    try:
        write_marker(marker_file, running_marker(session_id))
    except OSError:
        previous = previous
    log_directory, fallback = _prepare_log_directory(data_dir)
    service = Diagnostics(
        session_id=session_id,
        data_dir=data_dir,
        log_directory=log_directory,
        log_file=None if log_directory is None else log_directory / "slot-racing.log",
        fault_file=None,
        previous_unclean=previous,
        smoke_test=smoke_test,
        debug=debug,
        max_bytes=max_bytes,
        backup_count=backup_count,
        breadcrumbs=BreadcrumbLog(breadcrumb_capacity),
        started_at=datetime.now().isoformat(timespec="seconds"),
        log_fallback=fallback,
        _marker_file=marker_file,
    )
    service.facts.update(_base_facts(service))
    _configure_logging(service)
    service._previous_sys, service._previous_thread = install_hooks(
        session_id=session_id,
        on_crash=service.remember_crash,
        gate=service._gate,
    )
    _enable_faults(service)
    with _lock:
        global _current
        _current = service
    logger.info(
        "APP_START session_id=%s version=%s commit=%s python=%s os=%s arch=%s smoke_test=%s",
        session_id,
        service.facts["version"],
        service.facts["commit"],
        service.facts["python"],
        service.facts["os"],
        service.facts["architecture"],
        smoke_test,
    )
    return service


def current() -> Diagnostics | None:
    with _lock:
        return _current


def record(
    event: str,
    *,
    module: str,
    page: str = "",
    result: str = "",
    **fields: object,
) -> None:
    """Append one breadcrumb when diagnostics are installed. Never raises."""
    service = current()
    if service is None:
        return
    try:
        service.add_breadcrumb(event, module=module, page=page, result=result, fields=fields)
    except Exception:
        return


def note_fact(key: str, value: object) -> None:
    service = current()
    if service is None:
        return
    try:
        service.note_fact(key, value)
    except Exception:
        return


def mark_clean() -> None:
    service = current()
    if service is not None:
        service.mark_clean()


def shutdown() -> None:
    service = current()
    if service is not None:
        service.close()


def commit_id() -> str:
    """A commit baked in by the packager, or ``unknown`` when ``.git`` is absent."""
    value = os.environ.get("SLOT_RACING_COMMIT", "").strip()
    if value and all(character.isalnum() for character in value):
        return value[:40]
    return "unknown"


def _previous_unclean(path: Path) -> bool:
    try:
        stored = read_marker(path)
    except OSError:
        return False
    return stored is not None and not stored.clean


def _prepare_log_directory(data_dir: Path) -> tuple[Path | None, str]:
    preferred = data_dir / "logs"
    fallback = Path(tempfile.gettempdir()) / "SlotRacingPlatform" / "logs"
    if _writable(preferred):
        return preferred, "none"
    if _writable(fallback):
        return fallback, "temporary directory"
    return None, "stderr only"


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True


def _configure_logging(service: Diagnostics) -> None:
    root = logging.getLogger()
    service._previous_level = root.level
    level = logging.DEBUG if service.debug else logging.INFO
    root.setLevel(level)
    session_filter = _SessionFilter(service.session_id)
    root.addFilter(session_filter)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s session=%(session_id)s %(message)s"
    )
    if service.log_file is not None:
        try:
            file_handler = RotatingFileHandler(
                service.log_file,
                maxBytes=service.max_bytes,
                backupCount=service.backup_count,
                encoding="utf-8",
            )
        except OSError:
            service.log_file = None
            service.log_fallback = "stderr only"
            service.facts["log_fallback"] = service.log_fallback
        else:
            file_handler.name = _HANDLER_NAME
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(session_filter)
            root.addHandler(file_handler)
    if not any(handler.name == _STREAM_NAME for handler in root.handlers):
        stream = logging.StreamHandler(sys.stderr)
        stream.name = _STREAM_NAME
        stream.setLevel(level)
        stream.setFormatter(formatter)
        stream.addFilter(session_filter)
        root.addHandler(stream)


def _enable_faults(service: Diagnostics) -> None:
    """Capture native faults in their own file. A hard crash may not reach logging."""
    if service.log_directory is None:
        return
    path = service.log_directory / FAULT_NAME
    service._faults_were_enabled = faulthandler.is_enabled()
    try:
        if path.exists() and path.stat().st_size > service.max_bytes:
            rotated = path.with_name(FAULT_NAME + ".1")
            path.replace(rotated)
        handle = path.open("a", encoding="utf-8")
        faulthandler.enable(file=handle, all_threads=True)
    except OSError:
        return
    service.fault_file = path
    service._fault_handle = handle


def _base_facts(service: Diagnostics) -> dict[str, str]:
    directory = "" if service.log_directory is None else sanitize_text(str(service.log_directory))
    return {
        "version": __version__,
        "commit": commit_id(),
        "session_id": service.session_id,
        "smoke_test": "true" if service.smoke_test else "false",
        "started_at": service.started_at,
        "os": platform.platform(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "pyside": "unknown",
        "qt": "unknown",
        "schema_revision": "unknown",
        "plugins": "",
        "timing_source": "",
        "log_directory": directory,
        "log_level": "DEBUG" if service.debug else "INFO",
        "log_rotation": f"{service.max_bytes} bytes, {service.backup_count} backups",
        "log_fallback": service.log_fallback,
    }
