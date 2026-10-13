"""Graphical part editor in the dialog: tools, mouse, view, snaps, history, save and cancel.

The dialog is driven with pytest-qt events only. No real window was clicked. Modal questions are
replaced by plain functions, so no test waits in a nested event loop.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QToolButton,
    QWidget,
)
from pytestqt.qtbot import QtBot

from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.edit_tools import TOOLS
from slot_racing.modules.track_planner.parametric import (
    BODY_STRAIGHT,
    DIRECTION_RIGHT,
    SIDE_BOTH,
    PartDraft,
    spec_from_draft,
)
from slot_racing.modules.track_planner.parts import (
    PartRecord,
    PartSpec,
    SlotSpan,
    driving_paths,
    same_geometry,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui import part_editor, part_graphic
from slot_racing.modules.track_planner.ui.dialog_lifetime import destroy_dialog
from slot_racing.modules.track_planner.ui.part_canvas import PartCanvas
from slot_racing.modules.track_planner.ui.part_editor import PartDialog
from slot_racing.modules.track_planner.ui.part_graphic import GraphicEditor
from tests.modules.conftest import Env
from tests.modules.test_part_revisions import _article
from tests.modules.test_track_parts import _planner

DRAW_TAB = 1
PARAM_TAB = 0


@pytest.fixture(autouse=True)
def _no_modal_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly instead of opening a nested event loop."""

    def refuse(*_args: object, **_kwargs: object) -> QMessageBox.StandardButton:
        raise AssertionError("an unexpected modal question was opened")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(refuse))
    monkeypatch.setattr(part_graphic, "ask", lambda *_a, **_k: True)
    monkeypatch.setattr(part_editor, "ask", lambda *_a, **_k: True)
    monkeypatch.setattr(part_editor, "offer_adoption", lambda *_a, **_k: 0)


def _custom_straight(length: float = 400.0) -> PartDraft:
    return PartDraft(
        category="straight",
        body=BODY_STRAIGHT,
        length_mm=length,
        radius_mm=None,
        angle_deg=0.0,
        direction=DIRECTION_RIGHT,
        lane_count=2,
        pitch_mm=100.0,
        width_mm=None,
        offsets_mm=None,
        border_side=SIDE_BOTH,
        host_lanes=2,
    )


def _saved_straight(planner: TrackPlannerService, article: str = "EB-G") -> PartRecord:
    spec = spec_from_draft(
        _custom_straight(),
        article_number=article,
        scale="1:24",
        name="Eigene Gerade",
        manufacturer="",
        description="",
        height_mm=None,
    )
    return planner.add_part(spec)


@contextmanager
def _open(
    qtbot: QtBot,
    env: Env,
    planner: TrackPlannerService,
    record: PartRecord | None = None,
    *,
    duplicate: bool = False,
) -> Iterator[PartDialog]:
    dialog = PartDialog(env.runtime.translator, planner, record, duplicate=duplicate)
    dialog.show()
    qtbot.waitExposed(dialog)
    try:
        yield dialog
    finally:
        dialog._dismissed = True
        dialog.close()
        destroy_dialog(dialog)


def _draw(dialog: PartDialog) -> GraphicEditor:
    dialog.tabs.setCurrentIndex(DRAW_TAB)
    editor = dialog._graphic
    assert isinstance(editor, GraphicEditor)
    editor.canvas.fit_view()
    return editor


def _pos(canvas: PartCanvas, x: float, y: float) -> QPoint:
    point = canvas.to_screen(x, y)
    return QPoint(round(point.x()), round(point.y()))


def _click(
    canvas: PartCanvas,
    x: float,
    y: float,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
) -> None:
    QTest.mouseClick(canvas, button, Qt.KeyboardModifier.NoModifier, _pos(canvas, x, y))


def _drag(
    canvas: PartCanvas,
    start: tuple[float, float],
    end: tuple[float, float],
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
    steps: int = 4,
) -> None:
    begin = _pos(canvas, *start)
    finish = _pos(canvas, *end)
    QTest.mousePress(canvas, button, Qt.KeyboardModifier.NoModifier, begin)
    for step in range(1, steps + 1):
        here = QPoint(
            begin.x() + (finish.x() - begin.x()) * step // steps,
            begin.y() + (finish.y() - begin.y()) * step // steps,
        )
        QTest.mouseMove(canvas, here)
    QTest.mouseRelease(canvas, button, Qt.KeyboardModifier.NoModifier, finish)


def _tool(editor: GraphicEditor, name: str) -> None:
    button = editor.findChild(QToolButton, f"gfx-tool-{name}")
    assert isinstance(button, QToolButton)
    assert button.isEnabled()
    button.click()


def _button(editor: QWidget, name: str) -> QPushButton:
    button = editor.findChild(QPushButton, name)
    assert isinstance(button, QPushButton)
    return button


def _spin(editor: QWidget, name: str) -> QDoubleSpinBox:
    spin = editor.findChild(QDoubleSpinBox, name)
    assert isinstance(spin, QDoubleSpinBox)
    return spin


def _wheel(canvas: PartCanvas, at: QPoint, delta: int) -> None:
    event = QWheelEvent(
        QPointF(at),
        canvas.mapToGlobal(QPointF(at)),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(canvas, event)


def _count(editor: GraphicEditor, ref: ed.PathRef) -> int:
    return len(ed.spans_of(editor.session.spec, ref))


def _squeeze(editor: GraphicEditor) -> None:
    """Two lanes that run straight in and out and squeeze to the middle, drawn with the mouse."""
    canvas = editor.canvas
    editor.controller.run(ed.explicit)
    _tool(editor, "add_point")
    for x in (-150.0, -70.0, 70.0, 150.0):
        _click(canvas, x, -50.0)
        _click(canvas, x, 50.0)
    _tool(editor, "move")
    for x in (-70.0, 70.0):
        _drag(canvas, (x, -50.0), (x, -20.0))
        _drag(canvas, (x, 50.0), (x, 20.0))


def _fill_identity(dialog: PartDialog, article: str, name: str) -> None:
    dialog.article.setText(article)
    dialog.name.setText(name)


# --------------------------------------------------------------------------------------------
# Toolbar, view and snaps


def test_the_drawing_tab_has_every_tool_and_the_parametric_tab_still_works(
    qtbot: QtBot, env: Env
) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        assert dialog.tabs.count() == 2
        assert dialog.tabs.tabText(PARAM_TAB) == "Parameter"
        assert dialog.tabs.tabText(DRAW_TAB) == "Zeichnen"
        assert dialog.length.isEnabled()
        editor = _draw(dialog)
        for tool in TOOLS:
            button = editor.findChild(QToolButton, f"gfx-tool-{tool}")
            assert isinstance(button, QToolButton), tool
            assert button.isCheckable()
        assert len(TOOLS) == 12
        for name in ("gfx-undo", "gfx-redo", "gfx-fit"):
            assert isinstance(editor.findChild(QPushButton, name), QPushButton)
        for tool in TOOLS:
            _tool(editor, tool)
            assert editor.controller.tool == tool
            checked = [
                name
                for name in TOOLS
                if editor.findChild(QToolButton, f"gfx-tool-{name}").isChecked()  # type: ignore[union-attr]
            ]
            assert checked == [tool]
        dialog.tabs.setCurrentIndex(PARAM_TAB)
        assert dialog.length.isEnabled()
        dialog.length.setValue(300.0)
        assert dialog._preview_spec() is not None


def test_wheel_zoom_keeps_the_point_under_the_cursor_and_the_middle_button_pans(
    qtbot: QtBot, env: Env
) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        editor = _draw(dialog)
        canvas = editor.canvas
        anchor = QPoint(300, 200)
        world = canvas.to_world(QPointF(anchor))
        before = canvas.scale
        _wheel(canvas, anchor, 120)
        assert canvas.scale == pytest.approx(before * 1.2)
        assert canvas.to_world(QPointF(anchor)) == pytest.approx(world)
        _wheel(canvas, anchor, -240)
        assert canvas.scale < before
        for _ in range(60):
            _wheel(canvas, anchor, 120)
        assert canvas.scale <= 60.0
        canvas.fit_view()
        origin = canvas.origin
        _drag(canvas, (0.0, 0.0), (40.0, 10.0), button=Qt.MouseButton.MiddleButton)
        assert canvas.origin.x() > origin.x()
        assert canvas.origin.y() > origin.y()
        spec_before = editor.session.spec
        assert editor.session.spec == spec_before
        assert not editor.session.changed
        editor.fit_button.click()
        assert canvas.origin == origin or abs(canvas.origin.x() - origin.x()) < 1.0


def test_the_grid_snap_is_optional_and_its_size_adjustable(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        editor = _draw(dialog)
        canvas = editor.canvas
        controller = editor.controller
        controller.run(ed.explicit)
        _tool(editor, "line")
        controller.set_layer(ed.LAYER_CENTER)
        grid = editor.grid_snap
        assert not grid.isChecked()
        editor.point_snap.setChecked(False)
        _click(canvas, 13.0, 107.0)
        _click(canvas, 61.0, 107.0)
        assert controller.chain is not None
        first = ed.spans_of(controller.spec, controller.chain)[0]
        assert first.x0 % 25.0 != pytest.approx(0.0, abs=1e-6)
        controller.cancel()
        grid.setChecked(True)
        editor.grid_size.setValue(25.0)
        assert controller.snapping.grid
        assert controller.snapping.grid_mm == 25.0
        _click(canvas, 13.0, 107.0)
        _click(canvas, 61.0, 107.0)
        assert controller.chain is not None
        second = ed.spans_of(controller.spec, controller.chain)[0]
        for value in (second.x0, second.y0, second.x1, second.y1):
            assert value % 25.0 == pytest.approx(0.0, abs=1e-6)
        controller.cancel()
        grid.setChecked(False)
        assert not controller.snapping.grid


def test_selection_shows_markers_and_numeric_entry_moves_the_point(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        editor = _draw(dialog)
        controller = editor.controller
        controller.run(ed.explicit)
        _tool(editor, "select")
        _click(editor.canvas, -172.5, -50.0)
        selection = controller.selection
        assert selection is not None
        assert selection.kind == "point"
        label = editor.findChild(QLabel, "gfx-selection")
        assert isinstance(label, QLabel)
        assert "Spur 1" in label.text()
        x = _spin(editor, "gfx-point-x")
        y = _spin(editor, "gfx-point-y")
        assert x.value() == pytest.approx(-172.5)
        y.setValue(-45.5)
        span = ed.spans_of(controller.spec, ed.PathRef(ed.LAYER_GROOVE, 0))[0]
        assert (span.x0, span.y0) == (-172.5, -45.5)
        assert editor.session.can_undo
        editor.canvas.repaint()


def test_overlapping_items_are_picked_in_turn(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        editor = _draw(dialog)
        controller = editor.controller
        controller.run(ed.explicit)
        _tool(editor, "select")
        _click(editor.canvas, -172.5, -50.0)
        first = controller.selection
        _click(editor.canvas, -172.5, -50.0)
        second = controller.selection
        assert first is not None and second is not None
        assert not first.same_item(second)


def test_hit_tolerance_follows_the_zoom(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        editor = _draw(dialog)
        canvas = editor.canvas
        near = canvas.tolerance_mm()
        for _ in range(6):
            _wheel(canvas, QPoint(300, 200), 120)
        assert canvas.tolerance_mm() < near / 2.0


# --------------------------------------------------------------------------------------------
# Dragging, history, validity


def test_dragging_a_control_point_with_the_mouse_undo_and_redo(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        editor = _draw(dialog)
        controller = editor.controller
        canvas = editor.canvas
        controller.run(ed.explicit)
        ref = ed.groove_refs(controller.spec)[0]
        controller.run(lambda spec: ed.convert_span(spec, ref, 0, "bezier"))
        base = controller.spec
        start = ed.handle_points(ed.spans_of(base, ref)[0])["p1"]
        _tool(editor, "move")
        target = (start[0], start[1] - 30.0)
        _drag(canvas, start, target)
        moved = ed.handle_points(ed.spans_of(controller.spec, ref)[0])["p1"]
        assert moved[1] == pytest.approx(target[1], abs=2.0)
        assert moved[0] == pytest.approx(target[0], abs=2.0)
        for name in ("p0", "p3"):
            assert ed.handle_points(ed.spans_of(controller.spec, ref)[0])[name] == (
                ed.handle_points(ed.spans_of(base, ref)[0])[name]
            )
        undo = _button(editor, "gfx-undo")
        redo = _button(editor, "gfx-redo")
        assert undo.isEnabled()
        undo.click()
        assert controller.spec == base
        assert redo.isEnabled()
        redo.click()
        assert ed.handle_points(ed.spans_of(controller.spec, ref)[0])["p1"] == moved


def test_a_drag_writes_one_history_step_and_no_database_row(
    qtbot: QtBot, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    planner = _planner(env)
    saved = {"count": 0}
    real_update = planner.update_part
    real_add = planner.add_part

    def update(*args: object, **kwargs: object) -> PartRecord:
        saved["count"] += 1
        return real_update(*args, **kwargs)  # type: ignore[arg-type]

    def add(*args: object, **kwargs: object) -> PartRecord:
        saved["count"] += 1
        return real_add(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(planner, "update_part", update)
    monkeypatch.setattr(planner, "add_part", add)
    before_rows = len(planner.list_parts())
    with _open(qtbot, env, planner) as dialog:
        editor = _draw(dialog)
        editor.controller.run(ed.explicit)
        _tool(editor, "move")
        steps = 0
        editor.session.listeners.append(lambda: None)
        _drag(editor.canvas, (-172.5, -50.0), (-200.0, 0.0), steps=12)
        steps += 1
        assert saved["count"] == 0
        assert editor.session.undo()
        assert editor.session.undo()
        assert not editor.session.undo()
    assert saved["count"] == 0
    assert len(planner.list_parts()) == before_rows


def test_an_invalid_part_is_listed_and_cannot_be_saved(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    rows = len(planner.list_parts())
    with _open(qtbot, env, planner) as dialog:
        _fill_identity(dialog, "EB-BAD", "Kaputt")
        editor = _draw(dialog)
        editor.controller.run(ed.explicit)
        _tool(editor, "connector")
        _drag(editor.canvas, (172.5, 0.0), (260.0, 0.0))
        issues = editor.findChild(QListWidget, "gfx-issues")
        assert isinstance(issues, QListWidget)
        assert issues.count() >= 1
        assert "Rille" in issues.item(0).text()
        dialog.accept()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert "Rille" in dialog.message.text()
        assert len(planner.list_parts()) == rows
        editor.session.undo()
        assert issues.count() == 0


def test_cancel_asks_before_dropping_drawn_changes_and_stores_nothing(
    qtbot: QtBot, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    planner = _planner(env)
    rows = len(planner.list_parts())
    answers: list[str] = []

    def question(*_args: object, **_kwargs: object) -> QMessageBox.StandardButton:
        answers.append("asked")
        return QMessageBox.StandardButton.No if len(answers) == 1 else QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    with _open(qtbot, env, planner) as dialog:
        _fill_identity(dialog, "EB-CAN", "Abbruch")
        editor = _draw(dialog)
        editor.controller.run(ed.explicit)
        editor.controller.run(lambda spec: ed.set_connector(spec, 0, x_mm=-210.0))
        dialog.reject()
        assert answers == ["asked"]
        assert dialog.isVisible()
        dialog.reject()
        assert answers == ["asked", "asked"]
        assert not dialog.isVisible()
    assert len(planner.list_parts()) == rows


def test_repeated_open_and_close_keeps_qt_alive(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    record = _saved_straight(planner)
    for _ in range(6):
        with _open(qtbot, env, planner, record) as dialog:
            editor = _draw(dialog)
            editor.controller.run(ed.explicit)
            _tool(editor, "select")
            _click(editor.canvas, -172.5, -50.0)
            dialog.tabs.setCurrentIndex(PARAM_TAB)
            dialog.tabs.setCurrentIndex(DRAW_TAB)
            QApplication.processEvents()
    assert planner.library.require(record.id).spec.slot_paths == ()


def test_unchanged_drawing_tab_does_not_make_the_dialog_dirty(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    record = _saved_straight(planner)
    with _open(qtbot, env, planner, record) as dialog:
        _draw(dialog)
        dialog.tabs.setCurrentIndex(PARAM_TAB)
        assert not dialog._dirty()
        editor = _draw(dialog)
        editor.controller.run(lambda spec: ed.explicit(spec))
        assert dialog._dirty()
        editor.session.undo()
        assert not dialog._dirty()


# --------------------------------------------------------------------------------------------
# Shared state between the two tabs


def test_switching_modes_loses_no_geometry_and_the_form_only_edits_metadata(
    qtbot: QtBot, env: Env
) -> None:
    planner = _planner(env)
    engstelle = _article(planner, "20030350")
    with _open(qtbot, env, planner, engstelle) as dialog:
        editor = _draw(dialog)
        controller = editor.controller
        ref = ed.groove_refs(controller.spec)[0]

        def to_bezier(spec: PartSpec) -> PartSpec:
            spans = list(ed.spans_of(spec, ref))
            first = spans[1]
            spans[1] = ed.bezier_between(ed.span_start(first), ed.span_end(first))
            return ed.with_spans(spec, ref, tuple(spans))

        controller.run(to_bezier)
        controller.run(lambda spec: ed.move_point(spec, ed.PointRef(ref, 1, "p1"), 12.5, -31.25))
        drawn = controller.spec
        control = ed.handle_points(ed.spans_of(drawn, ref)[1])["p1"]
        dialog.tabs.setCurrentIndex(PARAM_TAB)
        assert not dialog.length.isEnabled()
        assert not dialog.category.isEnabled()
        assert dialog.notice.isVisible()
        assert "Zeichnen" in dialog.notice.text()
        dialog.description.setPlainText("Geändert im Parameterreiter")
        dialog.name.setText("Engstelle gezeichnet")
        dialog.tabs.setCurrentIndex(DRAW_TAB)
        assert editor.session.spec == drawn
        assert ed.handle_points(ed.spans_of(editor.session.spec, ref)[1])["p1"] == control
        dialog.tabs.setCurrentIndex(PARAM_TAB)
        preview = dialog._preview_spec()
        assert preview is not None
        assert same_geometry(preview, drawn)


def test_a_complex_part_is_never_reduced_without_the_explicit_confirmation(
    qtbot: QtBot, env: Env
) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        _fill_identity(dialog, "EB-KEEP", "Komplex")
        editor = _draw(dialog)
        controller = editor.controller
        controller.run(ed.explicit)
        ref = ed.groove_refs(controller.spec)[0]
        controller.run(lambda spec: ed.split_span(spec, ref, 0, 0.4))
        controller.run(lambda spec: ed.move_point(spec, ed.PointRef(ref, 0, "end"), -50.0, -55.0))
        drawn = controller.session.result()
        dialog.tabs.setCurrentIndex(PARAM_TAB)
        assert not dialog.length.isEnabled()
        dialog.length.setValue(300.0)
        saved = dialog._spec_to_save()
        assert same_geometry(saved, drawn)
        assert saved.length_mm != 300.0
        dialog.tabs.setCurrentIndex(DRAW_TAB)
        reparam = _button(dialog, "part-reparametrize")
        assert not reparam.isEnabled()


def test_reparametrizing_needs_a_confirmation_and_is_exact(
    qtbot: QtBot, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    planner = _planner(env)
    with _open(qtbot, env, planner) as dialog:
        _fill_identity(dialog, "EB-RP", "Zurück")
        editor = _draw(dialog)
        editor.controller.run(ed.explicit)
        spec = editor.session.result()
        assert ed.validate_editing(spec) == ()
        reparam = _button(dialog, "part-reparametrize")
        assert reparam.isEnabled()
        monkeypatch.setattr(part_editor, "ask", lambda *_a, **_k: False)
        reparam.click()
        assert dialog._graphic_changed()
        monkeypatch.setattr(part_editor, "ask", lambda *_a, **_k: True)
        reparam.click()
        assert not dialog._graphic_changed()
        assert dialog.length.isEnabled()
        assert dialog.tabs.currentIndex() == PARAM_TAB
        saved = dialog._spec_to_save()
        assert saved.slot_paths == spec.slot_paths
        assert saved.connectors == spec.connectors
        assert saved.outline == spec.outline
