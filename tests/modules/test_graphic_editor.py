"""Mouse drawing, saving and placing a part from the element editor."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QDialog, QPushButton
from pytestqt.qtbot import QtBot

from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.editing import span_end, span_start
from slot_racing.modules.track_planner.parts import (
    PATH_SLOT,
    SPAN_ARC,
    SPAN_BEZIER,
    join_pose,
    rails_can_join,
    same_geometry,
)
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.track_planner.ui.part_editor import PartDialog
from tests.modules.conftest import Env
from tests.modules.test_graphic_session import draw_bezier, draw_curve, draw_straight
from tests.modules.test_track_parts import _library_row, _planner, _select
from tests.modules.test_ui_management import open_page

_PLATE = ((0.0, -80.0), (220.0, -80.0), (220.0, 80.0), (0.0, 80.0))


def _editor(qtbot: QtBot, env: Env, record: object = None) -> PartDialog:
    dialog = PartDialog(env.runtime.translator, _planner(env), record)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    dialog.resize(1200, 800)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.graphic.canvas.width() > 300)
    dialog.graphic.canvas.fit_rect(-40.0, -120.0, 280.0, 140.0)
    return dialog


def _click(qtbot: QtBot, dialog: PartDialog, x_mm: float, y_mm: float) -> None:
    canvas = dialog.graphic.canvas
    pos = canvas.widget_point(x_mm, y_mm)
    assert canvas.rect().contains(pos)
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=pos)  # type: ignore[no-untyped-call]


def _move(dialog: PartDialog, x_mm: float, y_mm: float) -> None:
    canvas = dialog.graphic.canvas
    pos = canvas.widget_point(x_mm, y_mm)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos),
        QPointF(canvas.mapToGlobal(pos)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert QApplication.sendEvent(canvas, event)


def _drag(
    qtbot: QtBot, dialog: PartDialog, start: tuple[float, float], end: tuple[float, float]
) -> None:
    canvas = dialog.graphic.canvas
    origin = canvas.widget_point(*start)
    target = canvas.widget_point(*end)
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=origin)  # type: ignore[no-untyped-call]
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(target),
        QPointF(canvas.mapToGlobal(target)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert QApplication.sendEvent(canvas, event)
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=target)  # type: ignore[no-untyped-call]


def _close(dialog: PartDialog) -> None:
    dialog._dismissed = True
    dialog.close()


def test_clicks_draw_a_plate_with_a_preview_that_is_not_stored(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    dialog.graphic.session.snap_grid = False
    dialog.graphic.session.snap_points = False
    dialog.findChild(QPushButton, "graphic-plate").click()  # type: ignore[union-attr]
    _click(qtbot, dialog, 0.0, 0.0)
    _move(dialog, 80.0, 20.0)
    assert dialog.graphic.session.pen_points[0] == pytest.approx((0.0, 0.0), abs=1.5)
    assert dialog.graphic.session.hover == pytest.approx((80.0, 20.0), abs=1.5)
    assert dialog.graphic.session.preview_span() is not None
    assert dialog.graphic.session.plate() is None
    qtbot.keyClick(dialog.graphic.canvas, Qt.Key.Key_Escape)  # type: ignore[no-untyped-call]
    assert dialog.graphic.session.pen_points == ()
    _close(dialog)


def test_mouse_draws_a_two_lane_straight_and_a_handle_drag_undoes(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = _editor(qtbot, env)
    session = dialog.graphic.session
    session.snap_grid = False
    session.snap_points = False
    dialog.findChild(QPushButton, "graphic-plate").click()  # type: ignore[union-attr]
    for point in _PLATE:
        _click(qtbot, dialog, *point)
    _click(qtbot, dialog, *_PLATE[0])
    plate = session.plate()
    assert plate is not None and plate.closed
    dialog.findChild(QPushButton, "graphic-lane").click()  # type: ignore[union-attr]
    _click(qtbot, dialog, 20.0, -50.0)
    _click(qtbot, dialog, 200.0, -50.0)
    dialog.graphic.finish_button.click()
    dialog.findChild(QPushButton, "graphic-lane").click()  # type: ignore[union-attr]
    _click(qtbot, dialog, 20.0, 50.0)
    _click(qtbot, dialog, 200.0, 50.0)
    dialog.graphic.finish_button.click()
    dialog.findChild(QPushButton, "graphic-joint").click()  # type: ignore[union-attr]
    _click(qtbot, dialog, 20.0, 0.0)
    _click(qtbot, dialog, 200.0, 0.0)
    dialog.findChild(QPushButton, "graphic-select").click()  # type: ignore[union-attr]
    _drag(qtbot, dialog, (200.0, -50.0), (180.0, -50.0))
    moved = span_end(session.lanes()[0].spans[-1])
    assert moved == pytest.approx((180.0, -50.0), abs=1.5)
    assert dialog.graphic.elements.selectedItems()
    dialog.graphic.undo_button.click()
    assert span_end(session.lanes()[0].spans[-1]) == pytest.approx((200.0, -50.0), abs=1.5)
    dialog.graphic.redo_button.click()
    assert span_end(session.lanes()[0].spans[-1]) == pytest.approx((180.0, -50.0), abs=1.5)
    dialog.graphic.undo_button.click()
    lane_id = session.lanes()[0].id
    first = dialog.graphic.elements.item(0)
    assert first is not None
    first.setSelected(True)
    assert session.selected
    dialog.article.setText("EB-MOUSE")
    dialog.name.setText("Mausgerade")
    dialog.graphic.canvas.repaint()
    assert session.release_issues() == ()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    stored = planner.library.require(dialog.saved.id)  # type: ignore[union-attr]
    again = PartDialog(env.runtime.translator, planner, stored)
    qtbot.addWidget(again)
    again.show()
    assert again.name.text() == "Mausgerade"
    assert again.article.text() == "EB-MOUSE"
    assert len(again.graphic.session.lanes()) == 2
    assert span_start(again.graphic.session.lanes()[0].spans[0]) == pytest.approx(
        (20.0, -50.0), abs=1.5
    )
    assert same_geometry(
        again.graphic.session.compile(
            article_number=stored.spec.article_number,
            scale=stored.spec.scale,
            name=stored.spec.name,
            manufacturer=stored.spec.manufacturer,
            description=stored.spec.description,
            category=stored.spec.category,
            as_draft=False,
        ),
        stored.spec,
    )
    assert lane_id
    _close(again)


def test_snap_list_and_numeric_length_stay_on_the_same_line(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    session = dialog.graphic.session
    session.snap_points = False
    session.grid_mm = 10.0
    session.snap_grid = True
    dialog.findChild(QPushButton, "graphic-line").click()  # type: ignore[union-attr]
    _click(qtbot, dialog, 14.0, 6.0)
    assert session.snap_target == pytest.approx((10.0, 10.0))
    _click(qtbot, dialog, 96.0, 10.0)
    dialog.findChild(QPushButton, "graphic-select").click()  # type: ignore[union-attr]
    _click(qtbot, dialog, 100.0, 10.0)
    assert session.selected
    chosen = dialog.graphic.elements.selectedItems()
    assert chosen and chosen[0].data(Qt.ItemDataRole.UserRole) in session.selected
    dialog.graphic.length_spin.setValue(50.0)
    dialog.graphic.length_spin.editingFinished.emit()
    assert span_end(session.lanes()[0].spans[0])[0] == pytest.approx(60.0, abs=0.1)
    qtbot.keyClick(dialog.graphic.canvas, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)  # type: ignore[no-untyped-call]
    assert span_end(session.lanes()[0].spans[0])[0] == pytest.approx(100.0, abs=0.5)
    dialog.graphic.canvas.repaint()
    _close(dialog)


def test_a_curve_and_a_bezier_save_reopen_and_join_a_straight(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = _editor(qtbot, env)
    draw_curve(dialog.graphic.session)
    dialog.article.setText("EB-CURVE-UI")
    dialog.name.setText("Bogen")
    dialog.category.setCurrentIndex(dialog.category.findData("curve"))
    dialog.accept()
    assert dialog.saved is not None
    curve = planner.library.require(dialog.saved.id)
    assert all(
        path.spans[0].kind == SPAN_ARC for path in curve.spec.slot_paths if path.kind == PATH_SLOT
    )

    bezier = _editor(qtbot, env)
    draw_bezier(bezier.graphic.session)
    bezier.article.setText("EB-BEZ-UI")
    bezier.name.setText("Freie Kurve")
    bezier.category.setCurrentIndex(bezier.category.findData("special"))
    bezier.accept()
    assert bezier.saved is not None
    bent = planner.library.require(bezier.saved.id)
    assert all(
        path.spans[0].kind == SPAN_BEZIER for path in bent.spec.slot_paths if path.kind == PATH_SLOT
    )
    opened = PartDialog(env.runtime.translator, planner, bent)
    qtbot.addWidget(opened)
    opened.show()
    assert opened.graphic.session.lanes()[0].spans[0].kind == SPAN_BEZIER
    _close(opened)

    straight = _editor(qtbot, env)
    draw_straight(straight.graphic.session, length=200.0, lanes=2)
    straight.article.setText("EB-JOIN")
    straight.name.setText("Verbinder")
    straight.accept()
    assert straight.saved is not None
    spec = straight.saved.spec
    assert rails_can_join(spec.connectors[0], spec.connectors[1])
    track = env.track("Verbindung", lanes=2)
    catalog = {straight.saved.id: spec}
    first = place_instance(empty_plan(track.id), straight.saved.id, spec, 0, 0, 0, catalog)
    pose = join_pose(first.instances[0], spec.connectors[1], spec.connectors[0])
    second = place_instance(
        first, straight.saved.id, spec, pose.x_mm + 15, pose.y_mm + 10, pose.rotation_z_deg, catalog
    )
    seated = second.instances[1]
    assert seated.x_mm == pytest.approx(pose.x_mm)
    assert seated.y_mm == pytest.approx(pose.y_mm)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    row = _library_row(page, "EB-JOIN")
    page.library.setCurrentRow(row)
    page.place_part.click()
    assert page.plan().instances
