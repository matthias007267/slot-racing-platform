"""HUD editor: the live layout, type scales, the light overlay and the default layout."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)
from pytestqt.qtbot import QtBot

from slot_racing.app.main_window import MainWindow
from slot_racing.core.i18n import Translator
from slot_racing.core.storage import Database, Setting
from slot_racing.modules.races.hud import (
    FIELD_DRIVER,
    HUD_CONFIGURATION_KEY,
    HudConfigurationStore,
    LightFrame,
    to_pixels,
)
from slot_racing.modules.races.translations import TRANSLATIONS
from slot_racing.modules.races.ui.hud_editor import HudEditor
from slot_racing.modules.races.ui.live_stage import LiveHudStage
from tests.modules.conftest import Env


def test_font_scales_apply_in_the_preview_and_keep_their_hierarchy(qtbot: QtBot) -> None:
    editor = _shown(qtbot)
    driver = editor.stage.lanes.cards[1].driver_label
    lap = editor.stage.lanes.cards[1].lap_label
    before = driver.font().pixelSize()
    editor.set_font_scale(150)
    font_scale = editor.findChild(QSpinBox, "hud-font-scale")
    assert font_scale is not None and font_scale.value() == 150
    assert driver.font().pixelSize() > before
    assert driver.font().pixelSize() > lap.font().pixelSize()
    editor.set_field_scale(FIELD_DRIVER, 70)
    assert driver.font().pixelSize() < lap.font().pixelSize()
    assert editor.stage.lanes.cards[1].driver_label.font().pixelSize() == driver.font().pixelSize()
    assert (
        editor.stage.lanes.cards[2].driver_label.font().pixelSize()
        == editor.stage.lanes.cards[1].driver_label.font().pixelSize()
    )
    editor.set_font_scale(73)
    assert editor.selected_layout().font_scale == 75


def test_visibility_alignment_and_shares_change_the_live_surface(qtbot: QtBot) -> None:
    editor = _shown(qtbot)
    editor.set_field_visible(FIELD_DRIVER, False)
    card = editor.stage.lanes.cards[1]
    checkbox = editor.findChild(QCheckBox, "hud-visible-driver")
    assert checkbox is not None and checkbox.isChecked() is False
    assert not card.driver_label.isVisibleTo(card)
    editor.set_field_visible(FIELD_DRIVER, True)
    assert card.driver_label.isVisibleTo(card)

    editor.set_alignment("left")
    assert card.driver_label.alignment() & Qt.AlignmentFlag.AlignLeft
    editor.set_alignment("center")
    assert card.driver_label.alignment() & Qt.AlignmentFlag.AlignHCenter

    editor.set_share("lanes", 5)
    editor.set_share("ranking", 1)
    editor.set_share("clock", 4)
    editor.set_share("status", 1)
    QApplication.processEvents()
    assert editor.stage.lanes.width() > editor.stage.ranking.width()
    assert editor.stage.clock.width() > editor.stage.status.width()


def test_save_revert_factory_and_the_default_layout(qtbot: QtBot) -> None:
    store = HudConfigurationStore(_database())
    editor = _editor(qtbot, store)
    editor.set_font_scale(130)
    editor.save()
    reloaded = HudEditor(_translator(), store)
    qtbot.addWidget(reloaded)
    assert reloaded.selected_layout().font_scale == 130

    editor.set_font_scale(80)
    editor.revert()
    assert editor.selected_layout().font_scale == 130

    editor.apply_standard_layout()
    assert editor.selected_layout().font_scale == 100
    assert store.load().default_layout().font_scale == 130

    editor.add_layout("Abend")
    editor.set_font_scale(120)
    button = editor.findChild(QPushButton, "hud-set-default")
    assert button is not None
    assert button.text() == "Als Standardlayout festlegen"
    button.click()
    stored = store.load()
    assert stored.default_layout().name == "Abend"
    assert stored.default_layout().font_scale == 120
    mark = editor.findChild(QLabel, "hud-default-mark")
    assert mark is not None and mark.text() == "Standardlayout: Abend"
    combo = editor.findChild(QComboBox, "hud-layout")
    assert combo is not None and combo.currentText().endswith("[Standard]")

    editor.delete_selected()
    assert store.load().default_id == "standard"
    assert store.load().layout("standard") is not None
    assert store.load().layout("layout-2") is None
    delete = editor.findChild(QPushButton, "hud-layout-delete")
    assert delete is not None and delete.isEnabled() is False


def test_a_version_one_document_opens_without_a_crash(qtbot: QtBot) -> None:
    database = _database()
    with database.session() as session:
        session.add(
            Setting(
                key=HUD_CONFIGURATION_KEY,
                value={"version": 1, "widgets": [{"id": "race_clock", "visible": False}]},
            )
        )
    editor = _editor(qtbot, HudConfigurationStore(database))
    assert isinstance(editor.stage, LiveHudStage)
    assert editor.selected_layout().id == "standard"
    assert editor.selected_layout().font_scale == 100
    assert len(editor.stage.lanes.cards) >= 2


def test_the_preview_simulates_race_states_on_the_live_surface(qtbot: QtBot) -> None:
    editor = _editor(qtbot)
    assert isinstance(editor.stage, LiveHudStage)
    assert editor.stage.objectName() == "hud-live-preview"
    assert set(editor.stage.lanes.cards) == {1, 2}
    assert editor.stage.lanes.cards[1].driver_label.text() == "Zoe"
    assert editor.stage.lanes.cards[2].driver_label.text() == "Anna"
    status = editor.findChild(QComboBox, "hud-preview-status")
    assert status is not None
    status.setCurrentIndex(status.findData("paused"))
    assert "Pausiert" in editor.stage.status_label.text()
    assert editor.stage.lanes.cards[1].status_label.text() == "Wartet"
    status.setCurrentIndex(status.findData("finished"))
    assert editor.stage.lanes.cards[1].status_label.text() == "Fertig"
    status.setCurrentIndex(status.findData("aborted"))
    assert "Abgebrochen" in editor.stage.status_label.text()
    assert editor.stage.lanes.cards[1].status_label.text() == "Ausgeschieden"
    lanes = editor.findChild(QSpinBox, "hud-preview-lanes")
    assert lanes is not None
    lanes.setValue(4)
    assert set(editor.stage.lanes.cards) == {1, 2, 3, 4}
    assert editor.stage.lanes.cards[1].lane == 1
    assert editor.stage.lanes.cards[4].lane == 4


def test_the_light_overlay_can_be_moved_resized_and_previewed(qtbot: QtBot) -> None:
    editor = _shown(qtbot)
    editor.set_light_frame(LightFrame(True, 0.10, 0.20, 0.40, 0.25))
    QApplication.processEvents()
    _assert_placed(editor)
    before = editor.selected_layout().lights
    handle = editor.handle
    _mouse(handle, QEvent.Type.MouseButtonPress, (20, 16), (100, 100))
    _mouse(handle, QEvent.Type.MouseMove, (20, 16), (180, 150))
    _mouse(handle, QEvent.Type.MouseButtonRelease, (20, 16), (180, 150))
    moved = editor.selected_layout().lights
    assert moved.x > before.x
    assert moved.y > before.y
    _assert_placed(editor)

    corner = (handle.width() - 2, handle.height() - 2)
    _mouse(handle, QEvent.Type.MouseButtonPress, corner, (400, 300))
    _mouse(
        handle,
        QEvent.Type.MouseMove,
        (handle.width() - 2, handle.height() - 2),
        (470, 360),
    )
    _mouse(
        handle,
        QEvent.Type.MouseButtonRelease,
        (handle.width() - 2, handle.height() - 2),
        (470, 360),
    )
    resized = editor.selected_layout().lights
    assert resized.width > moved.width
    assert resized.height > moved.height
    _assert_placed(editor)

    editor.reset_lights()
    assert editor.selected_layout().lights == LightFrame()
    _assert_placed(editor)

    editor.resize(1100, 700)
    QApplication.processEvents()
    _assert_placed(editor)
    editor.resize(1500, 900)
    QApplication.processEvents()
    _assert_placed(editor)

    surface = _surface(editor)
    editor.set_light_frame(replace_visible(False))
    QApplication.processEvents()
    assert editor.lights.isHidden()
    assert _surface(editor) == surface
    editor.set_light_frame(replace_visible(True))
    QApplication.processEvents()
    assert not editor.lights.isHidden()
    assert editor.lights.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert _surface(editor) == surface

    editor.preview_start_sequence()
    assert editor.lights.lit_lights == 1
    assert editor.light_preview_started is False
    for expected in (2, 3, 4, 5):
        editor.advance_light_preview()
        assert editor.lights.lit_lights == expected
        assert _surface(editor) == surface
    editor.advance_light_preview()
    assert editor.lights.showing_go is True
    assert editor.light_preview_started is True
    assert _surface(editor) == surface
    editor.advance_light_preview()
    assert editor.lights.showing_go is False
    assert editor.light_preview_started is True


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


def _shown(qtbot: QtBot, store: HudConfigurationStore | None = None) -> HudEditor:
    editor = _editor(qtbot, store)
    editor.resize(1400, 860)
    editor.show()
    qtbot.waitUntil(lambda: editor.handle.width() > 80 and editor.stage.lanes.width() > 80)
    return editor


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


def _surface(editor: HudEditor) -> tuple[QRect, ...]:
    stage = editor.stage
    return (
        stage.clock.geometry(),
        stage.status.geometry(),
        stage.lanes.geometry(),
        stage.ranking.geometry(),
        stage.messages.geometry(),
        stage.controls.geometry(),
    )


def _assert_placed(editor: HudEditor) -> None:
    frame = editor.selected_layout().lights
    rect = to_pixels(frame.as_config(), editor.stage.width(), editor.stage.height())
    origin = editor.stage.mapTo(editor.preview, QPoint(0, 0))
    placed = QRect(origin.x() + rect.x, origin.y() + rect.y, rect.width, rect.height)
    assert editor.lights.geometry() == placed
    assert editor.handle.geometry() == placed


def replace_visible(visible: bool) -> LightFrame:
    return LightFrame(visible=visible)


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
