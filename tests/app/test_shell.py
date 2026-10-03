from collections.abc import Iterator
from typing import ClassVar

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QWidget,
)
from pytestqt.qtbot import QtBot

from slot_racing.app.main import main
from slot_racing.app.main_window import MainWindow
from slot_racing.app.runtime import Runtime
from slot_racing.core.config import AppConfig, load_config
from slot_racing.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from slot_racing.core.storage import Database


@pytest.fixture
def runtime() -> Iterator[Runtime]:
    runtime = Runtime.create(AppConfig(), database=Database.in_memory())
    yield runtime
    runtime.shutdown()


def make_window(qtbot: QtBot, runtime: Runtime) -> MainWindow:
    window = MainWindow(runtime)
    qtbot.addWidget(window)
    return window


def page_texts(window: MainWindow) -> str:
    page = window.current_page()
    return " ".join(label.text() for label in page.findChildren(QLabel))


def test_navigation_is_built_from_enabled_modules(qtbot: QtBot, runtime: Runtime) -> None:
    window = make_window(qtbot, runtime)
    assert window.navigation_ids() == [
        "dashboard",
        "drivers",
        "vehicles",
        "tracks",
        "races",
        "timing",
        "statistics",
        "track_planner",
        "settings",
    ]
    assert window.navigation_titles() == [
        "Dashboard",
        "Fahrer",
        "Fahrzeuge",
        "Strecken",
        "Rennen",
        "Zeitmessung",
        "Statistiken",
        "Streckenplaner",
        "Einstellungen",
    ]
    assert window.windowTitle() == "Slot-Racing Platform"
    assert window.current_id() == "dashboard"


def test_modules_without_a_page_show_a_placeholder(qtbot: QtBot, runtime: Runtime) -> None:
    window = make_window(qtbot, runtime)
    window.select("statistics")
    assert "noch nicht implementiert" in page_texts(window)


def test_navigation_follows_module_activation(qtbot: QtBot, runtime: Runtime) -> None:
    window = make_window(qtbot, runtime)
    window.select("statistics")
    runtime.plugins.disable("statistics")
    assert "statistics" not in window.navigation_ids()
    assert window.current_id() == "dashboard"
    runtime.plugins.enable("statistics")
    assert "statistics" in window.navigation_ids()


def test_dashboard_uses_real_master_data(qtbot: QtBot, runtime: Runtime) -> None:
    from slot_racing.modules.drivers_vehicles.service import DriverInput, DriverService
    from slot_racing.modules.races.ui.races_page import RacesPage

    runtime.services.get(DriverService).create_driver(DriverInput(name="Anna"))
    window = make_window(qtbot, runtime)
    assert window.findChild(QWidget, "sidebar") is not None
    assert window.findChild(QWidget, "shell-header") is not None
    title = window.findChild(QLabel, "shell-title")
    assert title is not None and title.text() == "Dashboard"
    metric = window.findChild(QLabel, "metric-drivers")
    assert metric is not None and metric.text() == "1"
    assert "registrierte Fahrer" in page_texts(window)
    new_race = window.findChild(QPushButton, "dashboard-action-races")
    assert new_race is not None
    new_race.click()
    assert window.current_id() == "races"
    page = window.current_page()
    assert isinstance(page, RacesPage)
    assert page.current_view() is page.wizard


def test_dashboard_lists_active_modules(qtbot: QtBot, runtime: Runtime) -> None:
    window = make_window(qtbot, runtime)
    assert "Statistiken" in page_texts(window)
    runtime.plugins.disable("statistics")
    assert "Statistiken" not in page_texts(window)


def test_settings_page_toggles_modules(qtbot: QtBot, runtime: Runtime) -> None:
    window = make_window(qtbot, runtime)
    window.select("settings")
    page = window.current_page()
    checkbox = page.findChildren(QCheckBox)[0]
    assert checkbox.isChecked()
    names = [c.text() for c in page.findChildren(QCheckBox)]
    stats = next(c for c in page.findChildren(QCheckBox) if c.text().startswith("Statistiken"))
    stats.setChecked(False)
    assert "statistics" not in window.navigation_ids()
    assert runtime.config.plugin_overrides["statistics"] is False
    assert any("Kamera" in name for name in names)


def test_core_stays_usable_when_all_modules_are_disabled(qtbot: QtBot, runtime: Runtime) -> None:
    window = make_window(qtbot, runtime)
    for name in runtime.plugins.plugin_names():
        runtime.plugins.disable(name)
    assert window.navigation_ids() == ["dashboard", "settings"]
    assert "Keine Module aktiv" in page_texts(window)


class _PagePlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(name="pages", version="1", title="Pages")

    def activate(self, context: PluginContext) -> None:
        def explode() -> object:
            raise RuntimeError("page exploded")

        context.add_navigation(
            NavigationItem(id="boom", title_key="nav.boom", page_factory=explode)
        )
        context.add_navigation(
            NavigationItem(id="wrong", title_key="nav.wrong", page_factory=lambda: "not a widget")
        )
        context.add_navigation(
            NavigationItem(
                id="fine", title_key="nav.fine", page_factory=lambda: QLabel("custom page")
            )
        )


def test_broken_pages_are_isolated(qtbot: QtBot) -> None:
    runtime = Runtime.create(AppConfig(), database=Database.in_memory(), plugins=[_PagePlugin()])
    try:
        window = make_window(qtbot, runtime)
        window.select("boom")
        assert "page exploded" in page_texts(window)
        window.select("wrong")
        assert "QWidget" in page_texts(window)
        window.select("fine")
        current = window.current_page()
        assert isinstance(current, QWidget)
        assert isinstance(current, QLabel) and current.text() == "custom page"
        window.select("dashboard")
        assert window.current_id() == "dashboard"
    finally:
        runtime.shutdown()


def test_closing_the_window_detaches_it_from_the_runtime(qtbot: QtBot, runtime: Runtime) -> None:
    window = make_window(qtbot, runtime)
    window.close()
    runtime.plugins.disable("statistics")
    runtime.plugins.enable("statistics")


def _click_when_shown(object_name: str) -> None:
    def click() -> None:
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            QTimer.singleShot(0, click)
            return
        button = box.findChild(QPushButton, object_name)
        if button is None:
            QTimer.singleShot(0, click)
            return
        button.click()

    QTimer.singleShot(0, click)


def _button_text_spare(button: QPushButton) -> int:
    option = QStyleOptionButton()
    button.initStyleOption(option)
    room = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
    return room.width() - button.fontMetrics().horizontalAdvance(button.text())


def test_quit_dialog_keeps_the_question_and_buttons_fully_visible(
    qtbot: QtBot, runtime: Runtime
) -> None:
    window = make_window(qtbot, runtime)
    window.show()
    spare: dict[str, int] = {}

    def inspect() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        label = box.findChild(QLabel, "qt_msgbox_label")
        assert label is not None
        assert label.text() == "Möchtest du das Programm wirklich beenden?"
        spare["question"] = label.width() - label.fontMetrics().horizontalAdvance(label.text())
        for name in ("quit-confirm", "quit-cancel"):
            button = box.findChild(QPushButton, name)
            assert button is not None
            spare[name] = _button_text_spare(button)
        box.reject()

    QTimer.singleShot(0, inspect)
    quit_button = window.findChild(QPushButton, "app-quit")
    assert quit_button is not None
    assert _button_text_spare(quit_button) >= 8
    quit_button.click()
    assert spare["question"] >= 8
    assert spare["quit-confirm"] >= 8
    assert spare["quit-cancel"] >= 8


def test_quit_button_sits_above_settings_and_cancel_keeps_the_app_open(
    qtbot: QtBot, runtime: Runtime
) -> None:
    window = make_window(qtbot, runtime)
    window.show()
    quit_button = window.findChild(QPushButton, "app-quit")
    settings = window.findChild(QPushButton, "nav-settings")
    assert quit_button is not None and settings is not None
    assert quit_button.text() == "Beenden"
    qtbot.waitUntil(lambda: settings.y() > quit_button.y())

    def cancel() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        assert box.text() == "Möchtest du das Programm wirklich beenden?"
        button = box.findChild(QPushButton, "quit-cancel")
        assert button is not None
        button.click()

    QTimer.singleShot(0, cancel)
    quit_button.click()
    assert window.isVisible()


def test_quit_saves_the_open_hud_and_closes(
    qtbot: QtBot, runtime: Runtime, tmp_path: object
) -> None:
    from pathlib import Path

    from slot_racing.modules.races.hud import HudConfigurationStore
    from slot_racing.modules.races.ui.hud_editor import HudEditor

    runtime.config_path = Path(str(tmp_path)) / "config.json"
    window = make_window(qtbot, runtime)
    window.show()
    window.select("settings")
    editor = window.findChild(HudEditor)
    assert editor is not None
    editor.set_widget_geometry("race_clock", 0.25, 0.25, 0.25, 0.25)
    _click_when_shown("quit-confirm")
    confirm_quit = window.findChild(QPushButton, "app-quit")
    assert confirm_quit is not None
    confirm_quit.click()
    assert not window.isVisible()
    saved = HudConfigurationStore(runtime.database).load().widget("race_clock")
    assert saved is not None and saved.x == 0.25
    assert runtime.config_path.is_file()


def test_quit_stays_open_when_saving_fails(
    qtbot: QtBot, runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window(qtbot, runtime)
    window.show()

    def broken() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(window, "_save_persistent_state", broken)

    def answer() -> None:
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            QTimer.singleShot(0, answer)
            return
        confirm = box.findChild(QPushButton, "quit-confirm")
        if confirm is not None:
            confirm.click()
            QTimer.singleShot(0, answer)
            return
        assert "disk full" in box.text()
        box.accept()

    QTimer.singleShot(0, answer)
    confirm_quit = window.findChild(QPushButton, "app-quit")
    assert confirm_quit is not None
    confirm_quit.click()
    assert window.isVisible()


def test_the_window_restores_and_stores_its_size(
    qtbot: QtBot, runtime: Runtime, tmp_path: object
) -> None:
    from pathlib import Path

    runtime.config_path = Path(str(tmp_path)) / "config.json"
    runtime.config.window_width = 1200
    runtime.config.window_height = 800
    runtime.config.window_x = 40
    runtime.config.window_y = 30
    window = make_window(qtbot, runtime)
    assert (window.width(), window.height(), window.x(), window.y()) == (1200, 800, 40, 30)

    window.show()
    window.resize(1024, 768)
    window.move(15, 25)
    qtbot.waitUntil(lambda: (window.width(), window.height()) == (1024, 768))
    window.close()
    stored = load_config(runtime.config_path)
    assert (stored.window_width, stored.window_height) == (1024, 768)
    assert (stored.window_x, stored.window_y) == (15, 25)

    runtime.config.window_width = 10
    runtime.config.window_height = 10
    fallback = make_window(qtbot, runtime)
    assert (fallback.width(), fallback.height()) == (1100, 700)


def test_application_starts_and_exits(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setenv("SLOT_RACING_HOME", str(tmp_path))
    assert main(["--smoke-test"]) == 0
