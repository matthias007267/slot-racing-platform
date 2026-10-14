"""Mouse editing of HUD elements: selection, the 128 by 72 grid, and resize."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtWidgets import QApplication, QSpinBox, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceId, RaceStatus
from slot_racing.modules.races.hud import (
    GRID_X,
    GRID_Y,
    VIEW_BETWEEN,
    VIEW_LIVE,
    BetweenRanking,
    HudConfigurationStore,
    HudWidgetConfig,
    region_frame,
    snap_rect,
    to_pixels,
)
from slot_racing.modules.races.hud_scene import HudViewport, build_hud_scene
from slot_racing.modules.races.types import HeatBriefing
from slot_racing.modules.races.ui.hud_editor import HudEditor, PlacementHandle
from slot_racing.modules.races.ui.live_stage import LiveHudStage
from slot_racing.modules.races.ui.live_view import LiveRaceView
from tests.database import migrated_database
from tests.modules.conftest import Env
from tests.modules.test_hud_editor import _mouse, _shown
from tests.modules.test_hud_scene import _presentation, _snapshot


def test_live_areas_are_selected_moved_and_resized_on_the_grid(qtbot: QtBot) -> None:
    editor = _shown(qtbot)
    canvas = editor.stage.canvas
    grid = editor.findChild(QWidget, "hud-grid")
    assert grid is not None
    assert grid.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert editor.stage.table.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert editor.stage.controls.pause_button.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents
    )

    clock_before = editor.stage.clock.geometry()
    _drag(editor.region_handle("clock"), 96, 48)
    assert editor.selected_element() == "clock"
    assert editor.stage.clock.geometry() != clock_before
    _assert_region(editor, "clock")

    _resize(editor.region_handle("ranking"), 80, 36)
    assert editor.selected_element() == "ranking"
    _assert_region(editor, "ranking")

    _drag(editor.region_handle("controls"), 40, -24)
    _assert_region(editor, "controls")
    assert editor.region_handle("clock").isVisible()
    assert canvas.width() > 80

    editor.set_share("lanes", 5)
    assert editor.selected_layout().regions == ()
    assert editor.stage.clock.x() == 0
    assert editor.stage.lanes.width() > editor.stage.ranking.width()


def test_the_between_ranking_follows_the_mouse_and_the_number_fields(
    qtbot: QtBot, env: Env
) -> None:
    store = HudConfigurationStore(migrated_database())
    editor = _shown(qtbot, store)
    editor.select_view(VIEW_BETWEEN)
    qtbot.waitUntil(lambda: editor.between.surface.width() > 200)
    editor.set_between_ranking(
        replace(editor.selected_layout().between_ranking, x=0.08, y=0.10, width=0.28, height=0.34)
    )
    surface = editor.between.surface
    handle = editor.findChild(PlacementHandle, "hud-ranking-handle")
    assert isinstance(handle, PlacementHandle)
    assert handle.isVisible()
    assert editor.between.ranking.table.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents
    )
    before = editor.selected_layout().between_ranking
    _drag(handle, 70, 36)
    assert editor.selected_element() == "between_ranking"
    moved = editor.selected_layout().between_ranking
    assert (moved.x, moved.y) != pytest.approx((before.x, before.y))
    _assert_on_grid(moved.x, moved.y, moved.width, moved.height)
    _assert_ranking(editor, surface)
    _assert_spins(editor, moved)

    _resize(handle, 64, 28)
    resized = editor.selected_layout().between_ranking
    assert resized.width != pytest.approx(moved.width)
    assert resized.height != pytest.approx(moved.height)
    assert resized.x == pytest.approx(moved.x)
    assert resized.y == pytest.approx(moved.y)
    _assert_on_grid(resized.x, resized.y, resized.width, resized.height)
    _assert_ranking(editor, surface)
    _assert_spins(editor, resized)

    x_spin = editor.findChild(QSpinBox, "hud-ranking-x")
    width_spin = editor.findChild(QSpinBox, "hud-ranking-width")
    assert isinstance(x_spin, QSpinBox)
    assert isinstance(width_spin, QSpinBox)
    x_target = 4 if x_spin.value() != 4 else 22
    width_target = 36 if width_spin.value() != 36 else 48
    x_spin.setValue(x_target)
    width_spin.setValue(width_target)
    QApplication.processEvents()
    edited = editor.selected_layout().between_ranking
    assert edited.x == pytest.approx(x_target / 100)
    assert edited.width == pytest.approx(width_target / 100)
    _assert_ranking(editor, surface)
    assert x_spin.value() == round(edited.x * 100)
    assert width_spin.value() == round(edited.width * 100)

    editor.save()
    reloaded = _shown(qtbot, store)
    reloaded.select_view(VIEW_BETWEEN)
    qtbot.waitUntil(lambda: reloaded.between.surface.width() > 200)
    assert reloaded.selected_layout().between_ranking == edited
    _assert_ranking(reloaded, reloaded.between.surface)

    live = LiveRaceView(env.runtime.translator, env.controller, store=store, service=env.races)
    qtbot.addWidget(live)
    live.resize(1280, 860)
    live.show()
    live.heat_gate.show()
    live.heat_gate.surface.resize(surface.size())
    live.heat_gate.show_standings(_snapshot(RaceStatus.RUNNING), edited)
    QApplication.processEvents()
    gate = live.heat_gate.surface
    expected = between_pixels(edited, gate.width(), gate.height())
    assert live.heat_gate.ranking.geometry() == expected
    assert not live.heat_gate.ranking.table.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents
    )
    assert not live.stage.table.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert not live.stage.controls.pause_button.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents
    )

    scene = build_hud_scene(
        reloaded.selected_layout(),
        _snapshot(RaceStatus.RUNNING),
        HudViewport(640, 360),
        _presentation(view=VIEW_BETWEEN, briefing=_briefing()),
    )
    panel = scene.panel("ranking")
    rect = to_pixels(edited.as_config(), 640, 360)
    assert (panel.x, panel.y, panel.width, panel.height) == (
        rect.x,
        rect.y,
        rect.width,
        rect.height,
    )


def test_a_saved_live_placement_matches_the_race_view_and_the_scene(qtbot: QtBot, env: Env) -> None:
    store = HudConfigurationStore(migrated_database())
    editor = _shown(qtbot, store)
    _drag(editor.region_handle("status"), 30, 20)
    _resize(editor.region_handle("messages"), 48, 20)
    placed = editor.selected_layout()
    assert placed.regions
    editor.save()

    live = LiveRaceView(env.runtime.translator, env.controller, store=store, service=env.races)
    qtbot.addWidget(live)
    live.resize(1400, 860)
    live.show()
    qtbot.waitUntil(lambda: live.stage.canvas.width() > 200 and live.stage.clock.width() > 20)
    _assert_stage(live.stage, placed)
    _assert_stage(editor.stage, placed)

    scene = build_hud_scene(
        placed,
        _snapshot(RaceStatus.RUNNING),
        HudViewport(live.stage.canvas.width(), live.stage.canvas.height()),
        _presentation(view=VIEW_LIVE),
    )
    for region_id in ("clock", "status", "lanes", "ranking", "messages", "controls"):
        frame = region_frame(placed, region_id)
        assert frame is not None
        rect = to_pixels(frame, live.stage.canvas.width(), live.stage.canvas.height())
        panel = scene.panel(region_id)
        assert (panel.x, panel.y, panel.width, panel.height) == (
            rect.x,
            rect.y,
            rect.width,
            rect.height,
        )


def test_the_start_gantry_still_moves_and_snaps_to_the_same_grid(qtbot: QtBot) -> None:
    editor = _shown(qtbot)
    handle = editor.handle
    _mouse(handle, QEvent.Type.MouseButtonPress, (20, 16), (100, 100))
    assert editor.selected_element() == "start_lights"
    _mouse(handle, QEvent.Type.MouseMove, (20, 16), (190, 160))
    _mouse(handle, QEvent.Type.MouseButtonRelease, (20, 16), (190, 160))
    moved = editor.selected_layout().lights
    assert moved.x * GRID_X == pytest.approx(round(moved.x * GRID_X))
    assert moved.y * GRID_Y == pytest.approx(round(moved.y * GRID_Y))
    x_spin = editor.findChild(QSpinBox, "hud-lights-x")
    y_spin = editor.findChild(QSpinBox, "hud-lights-y")
    assert isinstance(x_spin, QSpinBox)
    assert isinstance(y_spin, QSpinBox)
    origin = editor.stage.mapTo(editor.preview, editor.stage.rect().topLeft())
    assert x_spin.value() == round((editor.lights.x() - origin.x()) / editor.stage.width() * 100)
    assert y_spin.value() == round((editor.lights.y() - origin.y()) / editor.stage.height() * 100)


def _drag(handle: PlacementHandle, dx: int, dy: int) -> None:
    _mouse(handle, QEvent.Type.MouseButtonPress, (12, 12), (100, 100))
    _mouse(handle, QEvent.Type.MouseMove, (12, 12), (100 + dx, 100 + dy))
    _mouse(handle, QEvent.Type.MouseButtonRelease, (12, 12), (100 + dx, 100 + dy))
    QApplication.processEvents()


def _resize(handle: PlacementHandle, dx: int, dy: int) -> None:
    corner = (handle.width() - 2, handle.height() - 2)
    _mouse(handle, QEvent.Type.MouseButtonPress, corner, (400, 300))
    _mouse(handle, QEvent.Type.MouseMove, corner, (400 + dx, 300 + dy))
    _mouse(handle, QEvent.Type.MouseButtonRelease, corner, (400 + dx, 300 + dy))
    QApplication.processEvents()


def _assert_region(editor: HudEditor, region_id: str) -> None:
    frame = region_frame(editor.selected_layout(), region_id)
    assert frame is not None
    assert frame.x * GRID_X == pytest.approx(round(frame.x * GRID_X))
    assert frame.y * GRID_Y == pytest.approx(round(frame.y * GRID_Y))
    assert frame.width * GRID_X == pytest.approx(round(frame.width * GRID_X))
    assert frame.height * GRID_Y == pytest.approx(round(frame.height * GRID_Y))
    widget = editor.stage.panels()[region_id]
    rect = to_pixels(frame, editor.stage.canvas.width(), editor.stage.canvas.height())
    assert widget.geometry() == QRect(rect.x, rect.y, rect.width, rect.height)
    assert editor.region_handle(region_id).geometry() == widget.geometry()


def _assert_ranking(editor: HudEditor, surface: QWidget) -> None:
    frame = editor.selected_layout().between_ranking
    rect = to_pixels(frame.as_config(), surface.width(), surface.height())
    assert editor.between.ranking.geometry() == QRect(rect.x, rect.y, rect.width, rect.height)
    handle = editor.findChild(PlacementHandle, "hud-ranking-handle")
    assert handle is not None
    assert handle.geometry() == editor.between.ranking.geometry()


def _assert_spins(editor: HudEditor, frame: BetweenRanking) -> None:
    for axis, value in (
        ("x", frame.x),
        ("y", frame.y),
        ("width", frame.width),
        ("height", frame.height),
    ):
        spin = editor.findChild(QSpinBox, f"hud-ranking-{axis}")
        assert isinstance(spin, QSpinBox)
        assert spin.value() == round(value * 100)


def _assert_on_grid(x: float, y: float, width: float, height: float) -> None:
    assert snap_rect(x, y, width, height) == pytest.approx((x, y, width, height))


def _assert_stage(stage: LiveHudStage, layout: object) -> None:
    from slot_racing.modules.races.hud import HudLayout

    assert isinstance(layout, HudLayout)
    for region_id, widget in stage.panels().items():
        frame = region_frame(layout, region_id)
        assert frame is not None
        rect = to_pixels(
            HudWidgetConfig(frame.id, True, frame.x, frame.y, frame.width, frame.height),
            stage.canvas.width(),
            stage.canvas.height(),
        )
        assert widget.geometry() == QRect(rect.x, rect.y, rect.width, rect.height)


def between_pixels(frame: BetweenRanking, width: int, height: int) -> QRect:
    rect = to_pixels(frame.as_config(), width, height)
    return QRect(rect.x, rect.y, rect.width, rect.height)


def _briefing() -> HeatBriefing:
    return HeatBriefing(RaceId(1), 2, 5, (), (), ())
