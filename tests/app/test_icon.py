"""The running program shows the picture from resources/icons."""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from slot_racing.app.icon import (
    APPLICATION_ICON_NAME,
    application_icon_path,
    use_windows_taskbar_identity,
)
from slot_racing.app.main import main
from slot_racing.app.runtime import Runtime
from slot_racing.core.config import AppConfig
from tests.app.test_shell import make_window
from tests.database import migrated_database


@pytest.fixture
def runtime() -> Iterator[Runtime]:
    created = Runtime.create(AppConfig(), database=migrated_database())
    yield created
    created.shutdown()


def test_the_icon_file_is_the_picture_in_resources() -> None:
    path = application_icon_path()
    assert path is not None
    assert path.name == APPLICATION_ICON_NAME
    assert path.parent.name == "icons"
    assert path.parent.parent.name == "resources"
    assert path.is_file()


def test_the_main_window_uses_that_picture(qtbot: QtBot, runtime: Runtime) -> None:
    window = make_window(qtbot, runtime)
    path = application_icon_path()
    assert path is not None
    shown = window.windowIcon().pixmap(32, 32).toImage()
    source = QIcon(str(path)).pixmap(32, 32).toImage()
    assert not shown.isNull()
    assert shown == source


def test_startup_installs_the_icon_for_the_taskbar(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "slot_racing.app.main.use_windows_taskbar_identity",
        lambda: calls.append("identity"),
    )
    monkeypatch.setenv("SLOT_RACING_HOME", str(tmp_path))
    assert main(["--smoke-test"]) == 0
    assert calls == ["identity"]
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    assert not app.windowIcon().isNull()


def test_windows_taskbar_identity_is_skipped_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    use_windows_taskbar_identity()
