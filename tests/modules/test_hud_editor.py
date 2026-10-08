"""HUD editor: the live layout, type scales, the light overlay and the default layout."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
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
    LIGHT_ASPECT,
    HudConfiguration,
    HudConfigurationStore,
    LightFrame,
    effective_light_scale,
    light_frame,
    light_pixels,
    light_scale_limits,
)
from slot_racing.modules.races.translations import TRANSLATIONS
from slot_racing.modules.races.ui.hud_editor import HudEditor, HudEditorWindow
from slot_racing.modules.races.ui.live_stage import LiveHudStage
from slot_racing.modules.races.ui.live_view import LiveRaceView
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


def test_the_light_overlay_can_be_moved_and_resized(qtbot: QtBot) -> None:
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
    assert editor.findChild(QPushButton, "hud-lights-preview") is None


def test_settings_page_opens_the_hud_editor_window(qtbot: QtBot, env: Env) -> None:
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.show()
    window.select("settings")
    body = window.findChild(QWidget, "settings-body")
    assert body is not None
    assert body.findChild(QWidget, "race-hud-editor") is None
    assert body.findChild(QWidget, "hud-live-preview") is None
    button = body.findChild(QPushButton, "hud-open-editor")
    assert button is not None
    assert button.text() == "HUD-Editor öffnen"
    assert window.findChild(QWidget, "settings-section-race_hud") is not None
    button.click()
    editor = window.findChild(HudEditor)
    assert isinstance(editor, HudEditor)
    assert body.findChild(HudEditor) is None
    assert isinstance(editor.stage, LiveHudStage)
    assert editor.stage.objectName() == "hud-live-preview"


def test_the_hud_editor_is_absent_when_races_are_disabled(qtbot: QtBot, env: Env) -> None:
    env.runtime.plugins.disable("races")
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.select("settings")
    assert window.findChild(QPushButton, "hud-open-editor") is None
    assert window.findChild(QWidget, "race-hud-editor") is None


def test_opening_the_editor_twice_reuses_one_window(qtbot: QtBot, env: Env) -> None:
    window = _main(qtbot, env)
    first = _open_editor(window)
    again = _open_editor(window)
    assert again is first
    assert len(window.findChildren(HudEditorWindow)) == 1
    assert first.isVisible()
    first.close()
    assert window.isVisible()
    assert not first.isVisible()
    reopened = _open_editor(window)
    assert reopened is first
    assert reopened.isVisible()
    assert len(window.findChildren(HudEditorWindow)) == 1


def test_the_editor_window_can_be_resized_and_maximized(qtbot: QtBot, env: Env) -> None:
    window = _main(qtbot, env)
    editor_window = _open_editor(window)
    assert editor_window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    assert not editor_window.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose)
    assert editor_window.minimumWidth() < editor_window.maximumWidth()
    editor_window.resize(1200, 760)
    QApplication.processEvents()
    narrow = editor_window.editor.stage.width()
    editor_window.resize(1600, 940)
    QApplication.processEvents()
    assert editor_window.width() == 1600
    assert editor_window.editor.stage.width() > narrow
    editor_window.showMaximized()
    QApplication.processEvents()
    assert editor_window.isMaximized()
    editor_window.close()
    stored = env.runtime.services.get(HudConfigurationStore).load_window()
    assert stored.maximized is True
    assert stored.width is not None and stored.width >= 640
    assert stored.height is not None and stored.height >= 400


def test_the_editor_stage_matches_the_live_hud_area(qtbot: QtBot, env: Env) -> None:
    window = _main(qtbot, env)
    window.resize(1400, 900)
    QApplication.processEvents()
    editor_window = _open_editor(window)
    editor_window.resize(1400, 900)
    QApplication.processEvents()
    page = window.current_page()
    editor = editor_window.editor
    sidebar = editor.findChild(QWidget, "hud-editor-sidebar")
    navigation = window.findChild(QWidget, "sidebar")
    assert sidebar is not None and navigation is not None
    assert sidebar.width() == navigation.width()
    assert editor.match_live() is True
    qtbot.waitUntil(lambda: editor.stage.width() > 200 and page.width() > 200)
    assert (editor.stage.width(), editor.stage.height()) == (page.width(), page.height())

    live = LiveRaceView(
        env.runtime.translator,
        env.controller,
        env.runtime.services.get(HudConfigurationStore),
        env.races,
        env.runtime.config,
    )
    qtbot.addWidget(live)
    live.resize(page.size())
    live.show()
    QApplication.processEvents()
    assert (live.stage.width(), live.stage.height()) == (page.width(), page.height())
    assert (editor.stage.width(), editor.stage.height()) == (
        live.stage.width(),
        live.stage.height(),
    )
    matched = editor.stage.size()
    editor.set_match_live(False)
    QApplication.processEvents()
    assert editor.stage.width() > matched.width()
    assert editor.stage.height() > matched.height()
    editor.set_match_live(True)
    QApplication.processEvents()
    assert editor.stage.size() == matched


def test_editor_changes_show_up_immediately_and_layouts_still_save(qtbot: QtBot, env: Env) -> None:
    window = _main(qtbot, env)
    editor = _open_editor(window).editor
    QApplication.processEvents()
    before = editor.stage.lanes.cards[1].driver_label.font().pixelSize()
    scale = editor.findChild(QSpinBox, "hud-font-scale")
    assert scale is not None
    scale.setValue(150)
    assert editor.stage.lanes.cards[1].driver_label.font().pixelSize() > before
    editor.add_layout("Abend")
    editor.set_font_scale(120)
    default = editor.findChild(QPushButton, "hud-set-default")
    assert default is not None and default.text() == "Als Standardlayout festlegen"
    default.click()
    store = env.runtime.services.get(HudConfigurationStore)
    assert store.load().default_layout().name == "Abend"
    assert store.load().default_layout().font_scale == 120
    assert editor.is_dirty() is False


def test_closing_a_dirty_editor_asks_to_save_discard_or_cancel(qtbot: QtBot, env: Env) -> None:
    window = _main(qtbot, env)
    editor_window = _open_editor(window)
    editor = editor_window.editor
    store = env.runtime.services.get(HudConfigurationStore)
    editor.set_font_scale(140)
    assert editor.is_dirty() is True

    _answer("hud-close-cancel")
    assert editor_window.close() is False
    assert editor_window.isVisible()
    assert window.isVisible()
    assert editor.selected_layout().font_scale == 140
    assert store.load().selected().font_scale == 100

    _answer("hud-close-discard")
    assert editor_window.close() is True
    assert not editor_window.isVisible()
    assert window.isVisible()
    assert store.load().selected().font_scale == 100
    assert editor.selected_layout().font_scale == 100

    editor_window = _open_editor(window)
    editor_window.editor.set_font_scale(145)
    _answer("hud-close-save")
    assert editor_window.close() is True
    assert store.load().selected().font_scale == 145
    assert window.isVisible()


def test_preview_lanes_fill_the_row_again_when_the_count_drops(qtbot: QtBot, env: Env) -> None:
    window = _main(qtbot, env)
    editor_window = _open_editor(window)
    editor_window.resize(1800, 960)
    QApplication.processEvents()
    editor = editor_window.editor
    lanes = editor.findChild(QSpinBox, "hud-preview-lanes")
    assert lanes is not None
    font = editor.stage.lanes.cards[1].driver_label.font().pixelSize()
    for count in (4, 2, 3, 4, 3, 2):
        lanes.setValue(count)
        QApplication.processEvents()
        _assert_preview_lanes(editor, count)
    assert editor.stage.lanes.cards[1].driver_label.font().pixelSize() == font


def test_save_and_close_stores_the_layout_and_closes_the_editor(qtbot: QtBot, env: Env) -> None:
    window = _main(qtbot, env)
    editor_window = _open_editor(window)
    editor = editor_window.editor
    button = editor.findChild(QPushButton, "hud-save-close")
    assert button is not None
    assert button.text() == "Speichern und Schließen"
    editor.set_font_scale(135)
    button.click()
    assert QApplication.activeModalWidget() is None
    assert not editor_window.isVisible()
    assert window.isVisible()
    stored = env.runtime.services.get(HudConfigurationStore).load().selected()
    assert stored.font_scale == 135


def test_a_failed_save_keeps_the_editor_open(qtbot: QtBot, env: Env) -> None:
    window = _main(qtbot, env)
    editor_window = _open_editor(window)
    editor = editor_window.editor
    editor.set_font_scale(125)

    def broken(configuration: HudConfiguration) -> HudConfiguration:
        del configuration
        raise OSError("disk full")

    editor._store.save = broken  # type: ignore[method-assign]
    _dismiss_save_error()
    button = editor.findChild(QPushButton, "hud-save-close")
    assert button is not None
    button.click()
    assert editor_window.isVisible()
    assert window.isVisible()
    assert editor.selected_layout().font_scale == 125


def test_one_slider_and_the_grip_scale_the_gantry_together(qtbot: QtBot) -> None:
    editor = _shown(qtbot)
    assert editor.findChild(QSpinBox, "hud-lights-width") is None
    assert editor.findChild(QSpinBox, "hud-lights-height") is None
    assert editor.findChild(QSpinBox, "hud-lights-x") is not None
    assert editor.findChild(QSpinBox, "hud-lights-y") is not None
    slider = editor.findChild(QSlider, "hud-lights-scale")
    readout = editor.findChild(QLabel, "hud-lights-scale-readout")
    assert isinstance(slider, QSlider)
    assert isinstance(readout, QLabel)
    assert readout.text() == "100 %"
    assert any(label.text() == "Ampelgröße" for label in editor.findChildren(QLabel))
    low, high = light_scale_limits(editor.stage.width(), editor.stage.height())
    assert slider.minimum() == low
    assert slider.maximum() == high
    assert high > 100

    surface = _surface(editor)
    cards = _card_boxes(editor)
    others = editor.selected_layout()
    before = editor.lights.width()
    slider.setValue(high)
    QApplication.processEvents()
    grown = editor.selected_layout().lights
    assert grown.scale == high
    assert readout.text() == f"{high} %"
    assert slider.value() == high
    assert editor.lights.width() > before
    assert editor.lights.lamp_rects()[0].width() > 92
    _assert_locked_aspect(editor)
    _assert_placed(editor)
    assert _surface(editor) == surface
    assert _card_boxes(editor) == cards
    assert editor.selected_layout().font_scale == others.font_scale
    assert editor.selected_layout().fields == others.fields
    assert editor.selected_layout().lanes_share == others.lanes_share

    handle = editor.handle
    corner = (handle.width() - 2, handle.height() - 2)
    _mouse(handle, QEvent.Type.MouseButtonPress, corner, (500, 400))
    _mouse(handle, QEvent.Type.MouseMove, corner, (380, 400))
    _mouse(handle, QEvent.Type.MouseButtonRelease, corner, (380, 400))
    QApplication.processEvents()
    shrunk = editor.selected_layout().lights
    assert shrunk.scale < grown.scale
    assert slider.value() == effective_light_scale(
        shrunk.scale, editor.stage.width(), editor.stage.height()
    )
    assert readout.text() == f"{slider.value()} %"
    assert abs(shrunk.width / shrunk.height - grown.width / grown.height) < 0.02
    _assert_locked_aspect(editor)
    _assert_placed(editor)
    assert _surface(editor) == surface
    assert _card_boxes(editor) == cards

    slider.setValue(shrunk.scale)
    QApplication.processEvents()
    assert editor.selected_layout().lights.scale == shrunk.scale
    assert editor.handle.geometry() == editor.lights.geometry()

    stored = editor.selected_layout().lights.scale
    for width, height in ((960, 640), (1280, 800), (1680, 980)):
        editor.resize(width, height)
        QApplication.processEvents()
        qtbot.waitUntil(lambda: editor.stage.width() > 200 and editor.stage.height() > 120)
        assert editor.selected_layout().lights.scale == stored
        shown = effective_light_scale(stored, editor.stage.width(), editor.stage.height())
        assert slider.value() == shown
        assert (
            slider.maximum() == light_scale_limits(editor.stage.width(), editor.stage.height())[1]
        )
        _assert_locked_aspect(editor)
        _assert_placed(editor)
        assert editor.lights.width() <= editor.stage.width()
        assert editor.lights.height() <= editor.stage.height()

    hidden = _surface(editor)
    hidden_cards = _card_boxes(editor)
    editor.set_light_frame(replace(editor.selected_layout().lights, visible=False))
    QApplication.processEvents()
    assert editor.lights.isHidden()
    assert _surface(editor) == hidden
    assert _card_boxes(editor) == hidden_cards


def test_a_saved_light_scale_reloads_and_matches_the_live_view(qtbot: QtBot, env: Env) -> None:
    store = env.runtime.services.get(HudConfigurationStore)
    assert isinstance(store, HudConfigurationStore)
    editor = _shown(qtbot, store)
    editor.set_light_frame(light_frame(visible=True, x=0.08, y=0.12, scale=170))
    QApplication.processEvents()
    _assert_locked_aspect(editor)
    _assert_placed(editor)
    button = editor.findChild(QPushButton, "hud-set-default")
    assert button is not None
    button.click()
    close = editor.findChild(QPushButton, "hud-save-close")
    assert close is not None and close.text() == "Speichern und Schließen"
    editor.save_and_close()
    loaded = store.load()
    assert loaded.default_layout().lights.scale == 170
    assert loaded.default_layout().font_scale == 100
    assert loaded.default_layout().lights.width == pytest.approx(0.50 * 1.70)

    live = LiveRaceView(
        env.runtime.translator, env.controller, store, env.races, env.runtime.config
    )
    qtbot.addWidget(live)
    live.resize(1500, 900)
    live.show()
    live.start_lights.show_lights(3)
    qtbot.waitUntil(lambda: live.stage.width() > 200 and live.start_lights.width() > 40)
    assert live.bound_layout().lights.scale == 170
    assert live.start_lights.lit_lights == 3
    editor_box = _stage_box(editor.lights, editor.stage, editor.preview)
    live_box = _stage_box(live.start_lights, live.stage, live)
    assert abs(editor_box[2] - live_box[2]) < 0.02
    assert abs(editor_box[0] - live_box[0]) < 0.02
    assert abs(editor_box[1] - live_box[1]) < 0.03
    assert abs(editor_box[3] - LIGHT_ASPECT) < 0.04
    assert abs(live_box[3] - LIGHT_ASPECT) < 0.04
    surface = (
        live.clock.geometry(),
        live.lanes.geometry(),
        live.ranking.geometry(),
    )
    live.start_lights.clear()
    QApplication.processEvents()
    assert live.start_lights.isHidden()
    assert (live.clock.geometry(), live.lanes.geometry(), live.ranking.geometry()) == surface


def test_the_light_overlay_stays_on_the_editor_stage(qtbot: QtBot, env: Env) -> None:
    window = _main(qtbot, env)
    editor_window = _open_editor(window)
    editor_window.resize(1400, 900)
    QApplication.processEvents()
    editor = editor_window.editor
    qtbot.waitUntil(lambda: editor.handle.width() > 80)
    surface = _surface(editor)
    editor.set_light_frame(LightFrame(True, 0.15, 0.20, 0.40, 0.22))
    QApplication.processEvents()
    _assert_placed(editor)
    assert _surface(editor) == surface
    assert editor.findChild(QPushButton, "hud-lights-preview") is None


def _assert_preview_lanes(editor: HudEditor, count: int) -> None:
    board = editor.stage.lanes
    assert set(board.cards) == set(range(1, count + 1))
    cards = [board.cards[lane] for lane in range(1, count + 1)]
    first_row_y = min(card.y() for card in cards)
    row = [card for card in cards if card.y() == first_row_y]
    right = max(card.x() + card.width() for card in row)
    assert right >= board.width() - 4
    if count == 2:
        assert board.cards[1].y() == board.cards[2].y()
        assert board.cards[1].width() > board.width() * 0.4


def _card_boxes(editor: HudEditor) -> tuple[QRect, ...]:
    return tuple(card.geometry() for card in editor.stage.lanes.cards.values())


def _main(qtbot: QtBot, env: Env) -> MainWindow:
    window = MainWindow(env.runtime)
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    window.select("settings")
    return window


def _open_editor(window: MainWindow) -> HudEditorWindow:
    button = window.findChild(QPushButton, "hud-open-editor")
    assert button is not None
    button.click()
    editor_window = window.findChild(HudEditorWindow)
    assert isinstance(editor_window, HudEditorWindow)
    return editor_window


def _dismiss_save_error() -> None:
    def click() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        assert box.objectName() == "hud-save-error"
        assert "Das HUD-Layout konnte nicht gespeichert werden." in box.text()
        box.accept()

    QTimer.singleShot(0, click)


def _answer(object_name: str) -> None:
    def click() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        button = box.findChild(QPushButton, object_name)
        assert button is not None
        assert button.text() in {"Speichern", "Verwerfen", "Abbrechen"}
        button.click()

    QTimer.singleShot(0, click)


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


def _assert_locked_aspect(editor: HudEditor) -> None:
    lights = editor.lights
    assert lights.height() == max(1, round(lights.width() / LIGHT_ASPECT))
    lamps = lights.lamp_rects()
    assert len(lamps) == 5
    for lamp in lamps:
        assert abs(lamp.width() - lamp.height()) < 0.6
        assert lamp.left() >= -0.5
        assert lamp.right() <= lights.width() + 0.5


def _stage_box(widget: QWidget, stage: QWidget, host: QWidget) -> tuple[float, float, float, float]:
    origin = stage.mapTo(host, QPoint(0, 0))
    return (
        (widget.x() - origin.x()) / stage.width(),
        (widget.y() - origin.y()) / stage.height(),
        widget.width() / stage.width(),
        widget.width() / widget.height(),
    )


def _assert_placed(editor: HudEditor) -> None:
    frame = editor.selected_layout().lights
    rect = light_pixels(frame, editor.stage.width(), editor.stage.height())
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
