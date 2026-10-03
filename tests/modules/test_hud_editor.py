"""HUD editor: visibility, numbers, drag, resize and the settings page."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QCheckBox, QDoubleSpinBox, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.app.main_window import MainWindow
from slot_racing.core.i18n import Translator
from slot_racing.core.storage import Database
from slot_racing.modules.races.hud import (
    RACE_CLOCK,
    RACE_HEADER,
    HudConfigurationStore,
    default_hud_configuration,
    to_pixels,
)
from slot_racing.modules.races.translations import TRANSLATIONS
from slot_racing.modules.races.ui.hud_editor import HudBox, HudEditor
from slot_racing.modules.races.ui.hud_stage import HudStage
from tests.modules.conftest import Env


def test_numeric_geometry_is_clamped_and_shown_in_the_preview(qtbot: QtBot) -> None:
    editor = _editor(qtbot)
    editor.preview.setFixedSize(800, 450)
    editor.set_widget_geometry(RACE_CLOCK, 0.10, 0.20, 0.30, 0.25)
    clock = editor.configuration().widget(RACE_CLOCK)
    assert clock is not None
    assert (clock.x, clock.y, clock.width, clock.height) == (0.10, 0.20, 0.30, 0.25)
    assert editor.selected_id == "live_ranking"
    editor.select(RACE_CLOCK)
    spin = editor.findChild(QDoubleSpinBox, "hud-x")
    assert spin is not None and spin.value() == 0.10
    box = editor.box(RACE_CLOCK)
    assert box is not None
    rect = to_pixels(clock, 800, 450)
    assert (box.x(), box.y(), box.width(), box.height()) == (
        rect.x,
        rect.y,
        rect.width,
        rect.height,
    )

    editor.set_widget_geometry(RACE_CLOCK, 0.95, 0.95, 0.40, 0.40)
    clamped = editor.configuration().widget(RACE_CLOCK)
    assert clamped is not None
    assert clamped.x + clamped.width <= 1.0
    assert clamped.y + clamped.height <= 1.0
    assert clamped.width == 0.40


def test_hidden_widgets_leave_the_preview_and_can_come_back(qtbot: QtBot) -> None:
    editor = _editor(qtbot)
    editor.preview.setFixedSize(800, 450)
    editor.set_visible(RACE_HEADER, False)
    box = editor.box(RACE_HEADER)
    assert box is not None
    assert not box.isVisibleTo(editor.preview)
    checkbox = editor.findChild(QCheckBox, "hud-visible-race_header")
    assert checkbox is not None and checkbox.isChecked() is False
    editor.set_visible(RACE_HEADER, True)
    assert box.isVisibleTo(editor.preview)


def test_dragging_moves_and_the_corner_resizes(qtbot: QtBot) -> None:
    editor = _editor(qtbot)
    editor.preview.setFixedSize(800, 450)
    editor.preview.relayout()
    box = editor.box(RACE_HEADER)
    assert isinstance(box, HudBox)
    assert box.width() > 40

    _mouse(box, QEvent.Type.MouseButtonPress, (20, 16), (100, 100))
    _mouse(box, QEvent.Type.MouseMove, (20, 16), (180, 145))
    _mouse(box, QEvent.Type.MouseButtonRelease, (20, 16), (180, 145))
    moved = editor.configuration().widget(RACE_HEADER)
    assert moved is not None
    assert moved.x == pytest.approx(0.12)
    assert moved.y == pytest.approx(0.12)
    assert moved.width == 0.62

    _mouse(box, QEvent.Type.MouseButtonPress, (box.width() - 2, box.height() - 2), (300, 200))
    _mouse(box, QEvent.Type.MouseMove, (box.width() - 2, box.height() - 2), (380, 245))
    _mouse(box, QEvent.Type.MouseButtonRelease, (box.width() - 2, box.height() - 2), (380, 245))
    resized = editor.configuration().widget(RACE_HEADER)
    assert resized is not None
    assert resized.width == pytest.approx(0.72)
    assert resized.height == pytest.approx(0.24)
    assert resized.x == moved.x


def test_z_order_buttons_change_the_stack(qtbot: QtBot) -> None:
    editor = _editor(qtbot)
    editor.preview.setFixedSize(800, 450)
    editor.select(RACE_CLOCK)
    before = editor.configuration().widget(RACE_CLOCK)
    assert before is not None
    editor.bring_forward()
    raised = editor.configuration().widget(RACE_CLOCK)
    assert raised is not None
    assert raised.z_index == max(item.z_index for item in editor.configuration().widgets)
    box = editor.box(RACE_CLOCK)
    assert editor.preview.children()[-1] is box
    editor.send_backward()
    lowered = editor.configuration().widget(RACE_CLOCK)
    assert lowered is not None
    assert lowered.z_index < before.z_index


def test_save_revert_and_standard_layout(qtbot: QtBot) -> None:
    store = HudConfigurationStore(_database())
    editor = _editor(qtbot, store)
    editor.set_widget_geometry(RACE_CLOCK, 0.2, 0.2, 0.2, 0.2)
    editor.save()
    reloaded = HudEditor(_translator(), store)
    qtbot.addWidget(reloaded)
    saved = reloaded.configuration().widget(RACE_CLOCK)
    assert saved is not None and saved.x == 0.2

    editor.set_widget_geometry(RACE_CLOCK, 0.05, 0.05, 0.5, 0.5)
    editor.revert()
    reverted = editor.configuration().widget(RACE_CLOCK)
    assert reverted is not None and reverted.x == 0.2

    editor.apply_standard_layout()
    standard = editor.configuration().widget(RACE_CLOCK)
    assert standard == default_hud_configuration().widget(RACE_CLOCK)
    assert store.load().widget(RACE_CLOCK) == saved

    store.reset()
    assert store.load().widget(RACE_CLOCK) == default_hud_configuration().widget(RACE_CLOCK)


def test_a_saved_layout_reaches_an_open_stage(qtbot: QtBot) -> None:
    store = HudConfigurationStore(_database())
    stage = HudStage()
    panel = QWidget()
    panel.setMinimumSize(0, 0)
    stage.bind(RACE_CLOCK, panel)
    stage.resize(640, 360)
    stage.apply(store.load())
    qtbot.addWidget(stage)
    store.add_listener(stage.apply)
    editor = _editor(qtbot, store)
    editor.set_visible(RACE_CLOCK, False)
    editor.save()
    assert not panel.isVisibleTo(stage)

    stage.resize(1280, 720)
    stage.relayout()
    editor.set_visible(RACE_CLOCK, True)
    editor.set_widget_geometry(RACE_CLOCK, 0.25, 0.5, 0.5, 0.25)
    editor.save()
    placed = editor.configuration().widget(RACE_CLOCK)
    assert placed is not None
    rect = to_pixels(placed, 1280, 720)
    assert (panel.x(), panel.y(), panel.width(), panel.height()) == (
        rect.x,
        rect.y,
        rect.width,
        rect.height,
    )


def test_settings_page_hosts_the_hud_editor(qtbot: QtBot, env: Env) -> None:
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.select("settings")
    editor = window.findChild(QWidget, "race-hud-editor")
    assert editor is not None
    assert window.findChild(QWidget, "settings-section-race_hud") is not None


def test_the_hud_editor_is_absent_when_races_are_disabled(qtbot: QtBot, env: Env) -> None:
    env.runtime.plugins.disable("races")
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.select("settings")
    assert window.findChild(QWidget, "race-hud-editor") is None


def _editor(qtbot: QtBot, store: HudConfigurationStore | None = None) -> HudEditor:
    editor = HudEditor(_translator(), store or HudConfigurationStore(_database()))
    qtbot.addWidget(editor)
    return editor


def _database() -> Database:
    database = Database.in_memory()
    database.migrate()
    return database


def _translator() -> Translator:
    translator = Translator()
    translator.add_catalog("de", TRANSLATIONS["de"])
    return translator


def _mouse(
    widget: QWidget,
    kind: QEvent.Type,
    local: tuple[int, int],
    global_pos: tuple[int, int],
) -> None:
    button = Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
    event = QMouseEvent(
        kind,
        QPointF(*local),
        QPointF(*global_pos),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)
