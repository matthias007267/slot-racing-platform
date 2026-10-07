"""Logging, session marker, breadcrumbs and the local support bundle."""

from __future__ import annotations

import logging
import sys
import threading
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from slot_racing.core.config import AppConfig
from slot_racing.core.diagnostics import (
    Diagnostics,
    DialogGate,
    current,
    install,
    record,
    set_exception_presenter,
)
from slot_racing.core.diagnostics.sanitize import redact, sanitize_text


@pytest.fixture
def diagnostics(tmp_path: Path) -> Iterator[object]:
    service = install(tmp_path)
    yield service
    running = current()
    if running is not None:
        running.close()


def test_a_session_log_contains_the_session_and_respects_the_level(tmp_path: Path) -> None:
    service = install(tmp_path, debug=False)
    try:
        logging.getLogger("slot_racing.test.level").debug("hidden-debug-token")
        logging.getLogger("slot_racing.test.level").warning("visible-warning-token")
        assert service.log_file is not None
        text = service.log_file.read_text(encoding="utf-8")
        assert service.session_id in text
        assert "APP_START" in text
        assert "visible-warning-token" in text
        assert "hidden-debug-token" not in text
        service.mark_clean()
        assert "APP_SHUTDOWN" in service.log_file.read_text(encoding="utf-8")
    finally:
        service.close()


def test_log_rotation_keeps_a_backup(tmp_path: Path) -> None:
    service = install(tmp_path, max_bytes=500, backup_count=2)
    try:
        channel = logging.getLogger("slot_racing.test.rotation")
        for _ in range(30):
            channel.warning("rotate-me %s", "x" * 80)
        assert service.log_directory is not None
        assert (service.log_directory / "slot-racing.log.1").is_file()
    finally:
        service.close()


def test_a_clean_shutdown_is_not_reported_as_unclean(tmp_path: Path) -> None:
    first = install(tmp_path)
    first.mark_clean()
    first.close()
    second = install(tmp_path)
    try:
        assert second.previous_unclean is False
    finally:
        second.close()


def test_a_missing_shutdown_is_an_unclean_previous_session(tmp_path: Path) -> None:
    first = install(tmp_path)
    first.close()
    second = install(tmp_path)
    try:
        assert second.previous_unclean is True
    finally:
        second.close()


def test_an_unwritable_data_directory_does_not_abort_startup(tmp_path: Path) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked", encoding="utf-8")
    service = install(blocked)
    try:
        assert service.log_fallback == "temporary directory"
        assert service.log_directory is not None
        logging.getLogger("slot_racing.test.fallback").warning("fallback-warning-token")
        assert service.log_file is not None
        assert "fallback-warning-token" in service.log_file.read_text(encoding="utf-8")
    finally:
        service.close()


def test_the_breadcrumb_ring_keeps_the_newest_entries(tmp_path: Path) -> None:
    service = install(tmp_path, breadcrumb_capacity=5)
    try:
        for index in range(12):
            record("STEP", module="test", result="ok", step=index)
        items = service.breadcrumbs.snapshot()
        assert len(items) == 5
        assert [item.event for item in items] == ["STEP"] * 5
        assert [dict(item.fields)["step"] for item in items] == ["7", "8", "9", "10", "11"]
    finally:
        service.close()


def test_secrets_and_home_paths_are_removed() -> None:
    payload = redact(
        {
            "username": "user",
            "access_token": "abc",
            "password": "secret",
            "nested": {"api_key": "123"},
        }
    )
    assert payload == {
        "username": "user",
        "access_token": "<REDACTED>",
        "password": "<REDACTED>",
        "nested": {"api_key": "<REDACTED>"},
    }
    windows = sanitize_text(r"C:\Users\Name\tracks", homes=[Path(r"C:\Users\Name")])
    assert windows == r"<USER_HOME>\tracks"
    posix = sanitize_text("/home/name/tracks", homes=[Path("/home/name")])
    assert posix == "<USER_HOME>/tracks"
    assert "secret-value" not in sanitize_text("password=secret-value")


def test_uncaught_exceptions_in_the_main_thread_and_a_worker_are_logged(tmp_path: Path) -> None:
    service, saved_sys, saved_thread = _install_without_pytest_hooks(tmp_path)
    try:
        try:
            raise RuntimeError("main-boom-marker")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())

        def boom() -> None:
            raise RuntimeError("thread-boom-marker")

        worker = threading.Thread(target=boom, name="diag-worker")
        worker.start()
        worker.join()
        assert service.log_file is not None
        text = service.log_file.read_text(encoding="utf-8")
        assert "main-boom-marker" in text
        assert "Traceback" in text
        assert "crash_id=" in text
        assert "thread-boom-marker" in text
        assert "diag-worker" in text
        assert service.crash_ids
    finally:
        _restore_hooks(service, saved_sys, saved_thread)


def test_a_failing_crash_dialog_does_not_recurse(tmp_path: Path) -> None:
    service, saved_sys, saved_thread = _install_without_pytest_hooks(tmp_path)
    calls: list[str] = []

    def present(crash_id: str) -> None:
        calls.append(crash_id)
        if len(calls) > 2:
            raise AssertionError("exception hook recursed")
        sys.excepthook(RuntimeError, RuntimeError("nested-boom"), None)

    set_exception_presenter(present)
    try:
        sys.excepthook(RuntimeError, RuntimeError("origin-boom"), None)
        sys.excepthook(RuntimeError, RuntimeError("second-boom"), None)
        assert len(calls) == 1
        assert service.log_file is not None
        text = service.log_file.read_text(encoding="utf-8")
        assert "origin-boom" in text
        assert "nested-boom" in text
        assert "second-boom" in text
    finally:
        _restore_hooks(service, saved_sys, saved_thread)


def test_the_dialog_gate_suppresses_a_second_prompt() -> None:
    gate = DialogGate(cooldown_s=30)
    assert gate.allow()
    assert gate.allow() is False
    gate.release()
    assert gate.allow() is False


def test_the_support_bundle_contains_logs_and_not_the_database(tmp_path: Path) -> None:
    service = install(tmp_path)
    try:
        home = Path.home()
        logging.getLogger("slot_racing.test.bundle").warning(
            "password=super-secret-value path=%s", home / "slot-racing-secret-leaf"
        )
        record("LIBRARY_VIEW_OPEN", module="track_planner", page="planner", result="started")
        destination = tmp_path / "bundle.zip"
        config = AppConfig(database_path=home / "private" / "slot_racing.db")
        service.write_bundle(destination, config=config)
        with zipfile.ZipFile(destination) as archive:
            names = archive.namelist()
            assert "diagnostics.txt" in names
            assert "breadcrumbs.txt" in names
            assert "config.txt" in names
            assert any(name.startswith("logs/") for name in names)
            assert not any(name.endswith((".db", ".sqlite", ".sqlite3")) for name in names)
            report = archive.read("diagnostics.txt").decode("utf-8")
            crumbs = archive.read("breadcrumbs.txt").decode("utf-8")
            config_text = archive.read("config.txt").decode("utf-8")
            logged = archive.read("logs/slot-racing.log").decode("utf-8")
        assert "LIBRARY_VIEW_OPEN" in crumbs
        assert service.session_id in report
        assert "super-secret-value" not in logged
        assert "<REDACTED>" in logged
        assert str(home) not in logged
        assert "<USER_HOME>" in logged
        assert "slot_racing.db" in config_text
        assert str(home) not in config_text
    finally:
        service.close()


def test_a_missing_log_file_still_produces_a_bundle(tmp_path: Path) -> None:
    service = install(tmp_path)
    log_file = service.log_file
    service.close()
    assert log_file is not None
    log_file.unlink()
    destination = tmp_path / "partial.zip"
    service.write_bundle(destination, config=AppConfig())
    with zipfile.ZipFile(destination) as archive:
        report = archive.read("diagnostics.txt").decode("utf-8")
        assert "breadcrumbs.txt" in archive.namelist()
    assert "slot-racing.log unavailable" in report


def test_recording_many_breadcrumbs_stays_cheap(tmp_path: Path) -> None:
    service = install(tmp_path, breadcrumb_capacity=50)
    try:
        started = time.perf_counter()
        for index in range(1000):
            record("STEP", module="bench", result="ok", step=index)
        assert time.perf_counter() - started < 1.0
        assert len(service.breadcrumbs.snapshot()) == 50
    finally:
        service.close()


def test_diagnostics_fixture_closes(diagnostics: object) -> None:
    assert current() is diagnostics


def _install_without_pytest_hooks(
    tmp_path: Path,
) -> tuple[Diagnostics, object, object]:
    """Pytest-Qt treats a call to the saved hook as a test failure."""
    saved_sys = sys.excepthook
    saved_thread = threading.excepthook
    sys.excepthook = sys.__excepthook__
    threading.excepthook = threading.__excepthook__
    return install(tmp_path), saved_sys, saved_thread


def _restore_hooks(service: Diagnostics, saved_sys: object, saved_thread: object) -> None:
    service.close()
    sys.excepthook = saved_sys  # type: ignore[assignment]
    threading.excepthook = saved_thread  # type: ignore[assignment]
