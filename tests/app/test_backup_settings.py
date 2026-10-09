"""Restoring a backup asks before it replaces the stored data."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton
from pytestqt.qtbot import QtBot

from slot_racing.app.backup_settings import BackupSettings
from slot_racing.app.runtime import Runtime
from slot_racing.core.config import AppConfig
from tests.app.test_shell import make_window
from tests.database import migrated_database


@pytest.fixture
def runtime() -> Iterator[Runtime]:
    runtime = Runtime.create(AppConfig(), database=migrated_database())
    yield runtime
    runtime.shutdown()


def test_restore_warns_before_replacing_data(qtbot: QtBot, runtime: Runtime) -> None:
    window = make_window(qtbot, runtime)
    window.show()
    window.select("settings")
    page = window.current_page()
    widget = page.findChild(BackupSettings)
    create = page.findChild(QPushButton, "backup-create")
    restore = page.findChild(QPushButton, "backup-restore")
    title = page.findChild(QLabel, "settings-section-backup")
    assert widget is not None
    assert create is not None and create.text() == "Backup erstellen"
    assert restore is not None and restore.text() == "Backup wiederherstellen"
    assert title is not None and title.text() == "Datensicherung"

    def cancel() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        assert (
            box.text()
            == "Beim Wiederherstellen werden die aktuell gespeicherten Daten durch den Stand "
            "des Backups ersetzt."
        )
        button = box.findChild(QPushButton, "backup-restore-cancel")
        assert button is not None
        button.click()

    QTimer.singleShot(0, cancel)
    widget.restore_from(Path("missing.slbackup"))
    assert widget.message.text() == ""
