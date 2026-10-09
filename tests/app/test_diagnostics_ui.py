"""Diagnostics page, navigation breadcrumbs and a Qt slot exception."""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QPushButton
from pytestqt.qtbot import QtBot

from slot_racing.app.diagnostics_ui import DiagnosticsSettings
from slot_racing.app.main_window import MainWindow
from slot_racing.app.runtime import Runtime
from slot_racing.core.config import AppConfig
from slot_racing.core.diagnostics import current, install
from tests.app.test_shell import make_window
from tests.database import migrated_database


@pytest.fixture
def runtime() -> Iterator[Runtime]:
    created = Runtime.create(AppConfig(), database=migrated_database())
    yield created
    created.shutdown()


def test_settings_show_the_session_and_write_a_bundle(
    qtbot: QtBot, runtime: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = install(tmp_path)
    try:
        window = make_window(qtbot, runtime)
        window.select("settings")
        panel = window.current_page().findChild(DiagnosticsSettings)
        assert isinstance(panel, DiagnosticsSettings)
        label = panel.findChild(QLabel, "diagnostics-session")
        assert isinstance(label, QLabel)
        assert label.text() == service.session_id
        destination = tmp_path / "exported.zip"

        def choose(*_args: object, **_kwargs: object) -> tuple[str, str]:
            return str(destination), "zip"

        monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
        panel.create_bundle()
        assert destination.is_file()
        with zipfile.ZipFile(destination) as archive:
            names = set(archive.namelist())
        assert {"diagnostics.txt", "breadcrumbs.txt", "config.txt"} <= names
        assert "Diagnosepaket erstellt" in panel.message.text()
        assert "abgestürzt" not in panel.message.text()
    finally:
        running = current()
        if running is not None:
            running.close()


def test_navigation_records_each_page_change_once(
    qtbot: QtBot, runtime: Runtime, tmp_path: Path
) -> None:
    service = install(tmp_path)
    try:
        window = make_window(qtbot, runtime)
        window.select("tracks")
        window.select("tracks")
        window.select("track_planner")
        window.select("races")
        window.select("timing")
        steps = [
            (dict(item.fields).get("from"), dict(item.fields).get("to"))
            for item in service.breadcrumbs.snapshot()
            if item.event == "NAVIGATION"
        ]
        assert ("", "dashboard") in steps or ("dashboard", "tracks") in steps
        assert ("tracks", "track_planner") in steps
        assert ("track_planner", "races") in steps
        assert ("races", "timing") in steps
        assert steps.count(("tracks", "tracks")) == 0
        assert sum(1 for _from, to in steps if to == "tracks") == 1
    finally:
        running = current()
        if running is not None:
            running.close()


def test_an_unclean_previous_session_offers_a_bundle_without_claiming_a_crash(
    qtbot: QtBot, runtime: Runtime, tmp_path: Path
) -> None:
    first = install(tmp_path)
    first.close()
    install(tmp_path)

    def dismiss() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        assert "abgestürzt" not in box.text()
        assert "nicht ordnungsgemäß" in box.text()
        ignore = next(
            button
            for button in box.buttons()
            if isinstance(button, QPushButton) and button.text() == "Ignorieren"
        )
        ignore.click()

    QTimer.singleShot(0, dismiss)
    try:
        window = MainWindow(runtime)
        qtbot.addWidget(window)
        assert window.current_id() == "dashboard"
    finally:
        running = current()
        if running is not None:
            running.close()


@pytest.mark.qt_no_exception_capture
def test_a_slot_exception_is_logged_and_one_dialog_is_closed(
    qtbot: QtBot, runtime: Runtime, tmp_path: Path
) -> None:
    service = install(tmp_path)
    window = make_window(qtbot, runtime)
    button = QPushButton(window)
    button.clicked.connect(_raise_slot)
    dialogs: list[QMessageBox] = []

    def dismiss() -> None:
        box = QApplication.activeModalWidget()
        if isinstance(box, QMessageBox):
            dialogs.append(box)
            box.reject()

    QTimer.singleShot(0, dismiss)
    try:
        button.click()
        qtbot.waitUntil(lambda: "slot-boom-marker" in _log_text(service), timeout=2000)
        assert dialogs
        assert "slot-boom-marker" in _log_text(service)
        assert service.crash_ids
    finally:
        running = current()
        if running is not None:
            running.close()


def _raise_slot() -> None:
    raise RuntimeError("slot-boom-marker")


def _log_text(service: object) -> str:
    path = getattr(service, "log_file", None)
    if not isinstance(path, Path) or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")
