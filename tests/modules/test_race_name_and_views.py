"""Race names, Enter, and the shared HUD surfaces. No camera and no timing changes."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceMode
from slot_racing.modules.races.hud import (
    DISPLAY_ALWAYS,
    DISPLAY_HIDE,
    FIELD_BEST,
    FIELD_BEST_TIME,
    FIELD_DRIVER,
    FIELD_REMAINING_LAPS,
    FIELD_REMAINING_TIME,
    FIELD_TOTAL,
    HUD_VERSION,
    TEXT_DRIVER,
    VIEW_PRESTART,
    VIEW_RESULTS,
    FieldStyle,
    HudConfigurationStore,
    coerce,
    field_shown,
)
from slot_racing.modules.races.naming import suggest_race_name
from slot_racing.modules.races.ui.hud_editor import HudEditor
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.wizard import NAME, START, TRACK
from slot_racing.uikit.enter import bind_enter
from tests.modules.conftest import Env
from tests.modules.test_hud_editor import _database, _shown, _translator
from tests.modules.test_ui_management import open_page


def test_a_new_race_name_uses_the_local_minute_and_stays_unique() -> None:
    moment = datetime(2026, 10, 9, 9, 12)
    assert suggest_race_name([], moment) == "2026-10-09 09:12"
    assert suggest_race_name(["2026-10-09 09:12"], moment) == "2026-10-09 09:12 (2)"
    taken = ["2026-10-09 09:12", "2026-10-09 09:12 (2)"]
    assert suggest_race_name(taken, moment) == "2026-10-09 09:12 (3)"
    assert suggest_race_name([" 2026-10-09 09:12 "], moment) == "2026-10-09 09:12 (2)"


def test_a_new_wizard_race_is_prefilled_and_an_existing_name_stays(qtbot: QtBot, env: Env) -> None:
    stored = env.races.create_race("Finale", env.track("Heimbahn").id, 5)
    window, page = open_page(qtbot, env, "races")
    window.show()
    assert isinstance(page, RacesPage)
    page.new_race()
    suggested = page.wizard.name_edit.text()
    assert len(suggested) == 16
    assert suggested[4] == "-" and suggested[10] == " "
    page.wizard.begin(stored.id)
    assert page.wizard.name_edit.text() == "Finale"
    assert page.wizard.race is not None and page.wizard.race.id == stored.id


def test_enter_advances_a_valid_wizard_step_and_stops_on_an_empty_name(
    qtbot: QtBot, env: Env
) -> None:
    env.track("Heimbahn")
    window, page = open_page(qtbot, env, "races")
    window.show()
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    wizard.name_edit.clear()
    wizard.name_edit.setFocus()
    QTest.keyClick(wizard.name_edit, Qt.Key.Key_Return)
    assert wizard.step == NAME
    assert "Namen" in wizard.status.text()

    wizard.name_edit.setText("Abendrennen")
    wizard.name_edit.setFocus()
    QTest.keyClick(wizard.name_edit, Qt.Key.Key_Return)
    assert wizard.step == TRACK


def test_enter_in_a_line_saves_and_leaves_multiline_buttons_and_dialogs(qtbot: QtBot) -> None:
    root = QWidget()
    layout = QVBoxLayout(root)
    line = QLineEdit()
    notes = QPlainTextEdit()
    button = QPushButton("Schließen")
    saved: list[str] = []
    clicked: list[bool] = []
    button.clicked.connect(lambda: clicked.append(True))
    layout.addWidget(line)
    layout.addWidget(notes)
    layout.addWidget(button)
    bind_enter(root, lambda: saved.append(line.text()))
    qtbot.addWidget(root)
    root.show()
    line.setText("iVCam")
    line.setFocus()
    QTest.keyClick(line, Qt.Key.Key_Return)
    assert saved == ["iVCam"]
    notes.setFocus()
    QTest.keyClick(notes, Qt.Key.Key_Return)
    assert saved == ["iVCam"]
    assert notes.toPlainText() == "\n"
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Return)
    assert clicked == []
    assert saved == ["iVCam"]
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert clicked == [True]

    dialog = QDialog(root)
    dialog_line = QLineEdit(dialog)
    accept = QPushButton("OK", dialog)
    accept.setDefault(True)
    accepted: list[bool] = []
    accept.clicked.connect(lambda: accepted.append(True))
    dialog.show()
    dialog_line.setFocus()
    QTest.keyClick(dialog_line, Qt.Key.Key_Return)
    assert accepted == [True]
    assert saved == ["iVCam"]


def test_enter_on_the_last_wizard_step_starts_the_race(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heimbahn", lanes=2)
    driver = env.driver("Anna")
    env.vehicle("Porsche", driver_id=driver.id)
    window, page = open_page(qtbot, env, "races")
    window.show()
    assert isinstance(page, RacesPage)
    page.new_race()
    wizard = page.wizard
    wizard.name_edit.setText("Abendrennen")
    assert wizard.go_next()
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    assert wizard.go_next()
    assert wizard.go_next()
    wizard.driver_combo.setCurrentIndex(wizard.driver_combo.findData(driver.id))
    assert wizard.add_participant()
    assert wizard.go_next()
    assert wizard.go_next()
    assert wizard.step == START
    wizard.name_edit.setFocus()
    QTest.keyClick(wizard.name_edit, Qt.Key.Key_Return)
    assert page.stack.currentWidget() is page.live


def test_the_live_preview_follows_the_race_mode(qtbot: QtBot) -> None:
    editor = HudEditor(_translator(), HudConfigurationStore(_database()))
    qtbot.addWidget(editor)
    editor.show()
    assert _lane_box_hidden(editor, "live-lane-1-remaining-time")
    assert not _lane_box_hidden(editor, "live-lane-1-total")
    mode = editor.findChild(QComboBox, "hud-preview-mode")
    assert mode is not None
    mode.setCurrentIndex(mode.findData(RaceMode.TIME_TRIAL.value))
    assert not _lane_box_hidden(editor, "live-lane-1-remaining-time")
    assert _lane_box_hidden(editor, "live-lane-1-total")
    assert _lane_box_hidden(editor, "live-lane-1-remaining-laps")


def test_enter_in_the_hud_editor_saves_without_closing(qtbot: QtBot) -> None:
    editor = _shown(qtbot)
    editor.set_font_scale(130)
    editor._font.setFocus()
    QTest.keyClick(editor._font, Qt.Key.Key_Return)
    assert editor._store.load().selected().font_scale == 130
    assert editor.isVisible()
    assert editor.window().isVisible()


def test_version_two_layouts_keep_their_fields_and_gain_view_defaults() -> None:
    parsed = coerce(
        {
            "version": 2,
            "selected_id": "standard",
            "default_id": "standard",
            "layouts": [
                {
                    "id": "standard",
                    "name": "Standard",
                    "builtin": True,
                    "fields": [{"id": "driver", "visible": False, "scale": 120}],
                }
            ],
        }
    )
    assert parsed.version == HUD_VERSION
    assert parsed.selected().field("driver") == FieldStyle(False, 120)
    assert parsed.selected().field(FIELD_BEST_TIME) == FieldStyle()
    assert parsed.selected().view(VIEW_PRESTART).font_scale == 100
    assert parsed.selected().view(VIEW_RESULTS).text_scale(TEXT_DRIVER) == 100


def test_display_mode_follows_the_race_mode_unless_forced() -> None:
    auto = FieldStyle(True, 100, "auto")
    always = FieldStyle(True, 100, DISPLAY_ALWAYS)
    hidden = FieldStyle(False, 100, DISPLAY_HIDE)
    assert field_shown(auto, FIELD_TOTAL, RaceMode.LAPS, available=True) is True
    assert field_shown(auto, FIELD_TOTAL, RaceMode.TIME_TRIAL, available=True) is False
    assert field_shown(always, FIELD_TOTAL, RaceMode.TIME_TRIAL, available=True) is True
    assert field_shown(hidden, FIELD_DRIVER, RaceMode.LAPS, available=True) is False
    assert field_shown(auto, FIELD_REMAINING_TIME, RaceMode.TIME_TRIAL, available=True) is True
    assert field_shown(auto, FIELD_REMAINING_TIME, RaceMode.LAPS, available=True) is False
    assert field_shown(always, FIELD_REMAINING_TIME, RaceMode.TIME_TRIAL, available=False) is False
    assert field_shown(auto, FIELD_REMAINING_LAPS, RaceMode.LAPS, available=True) is True
    assert field_shown(auto, FIELD_DRIVER, RaceMode.TIME_TRIAL, available=True) is True
    assert field_shown(auto, FIELD_BEST, RaceMode.LAPS, available=True) is True
    assert field_shown(auto, FIELD_BEST, RaceMode.TIME_TRIAL, available=True) is True
    assert field_shown(auto, FIELD_BEST_TIME, RaceMode.LAPS, available=True) is True
    assert field_shown(auto, FIELD_BEST_TIME, RaceMode.TIME_TRIAL, available=True) is True


def test_a_view_font_is_stored_for_every_race_and_the_editor_can_switch(
    qtbot: QtBot,
) -> None:
    store = HudConfigurationStore(_database())
    editor = HudEditor(_translator(), store)
    qtbot.addWidget(editor)
    editor.show()
    editor.select_view(VIEW_PRESTART)
    view = editor.findChild(QComboBox, "hud-view")
    assert view is not None and view.currentData() == VIEW_PRESTART
    sample = editor.findChild(QWidget, "hud-sample-driver")
    assert sample is not None
    before = _font_pixels(sample)
    editor.set_text_scale(TEXT_DRIVER, 150)
    assert editor.selected_layout().view(VIEW_PRESTART).text_scale(TEXT_DRIVER) == 150
    assert _font_pixels(sample) > before
    editor.save()
    loaded = store.load().default_layout().view(VIEW_PRESTART)
    assert loaded.text_scale(TEXT_DRIVER) == 150
    assert store.load().default_layout().font_scale == 100
    editor.select_view(VIEW_RESULTS)
    assert editor.selected_layout().view(VIEW_RESULTS).text_scale(TEXT_DRIVER) == 100


def _lane_box_hidden(editor: QWidget, name: str) -> bool:
    label = editor.findChild(QWidget, name)
    parent = None if label is None else label.parentWidget()
    assert parent is not None
    return parent.isHidden()


def _font_pixels(sample: QWidget) -> int:
    """Read the size the preview stylesheet applies. The theme replaces QFont."""
    return int(sample.styleSheet().split("font-size:")[1].split("px")[0])
