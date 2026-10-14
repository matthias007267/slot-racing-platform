"""Clicks draw open lanes and one arc, then the same draft opens again."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.editing import span_end, span_start, spans_length_mm
from slot_racing.modules.track_planner.models import TrackPartDefinition
from slot_racing.modules.track_planner.parts import (
    SPAN_ARC,
    SPAN_BEZIER,
    SPAN_LINE,
    STANDARD_CURVE_ARTICLE,
    same_geometry,
)
from slot_racing.modules.track_planner.plate_session import draft_lanes, plate_corners
from slot_racing.modules.track_planner.ui.part_editor import PartDialog
from tests.modules.conftest import Env
from tests.modules.test_parametric_editor import _draft, _save_draft
from tests.modules.test_part_revisions import _article
from tests.modules.test_plate_editor import _RECT, _click, _editor, _move
from tests.modules.test_track_parts import _planner

_LANE_A = ((40.0, 50.0), (200.0, 50.0), (200.0, 140.0))
_LANE_B = ((60.0, 120.0), (220.0, 120.0), (220.0, 40.0))


def _close(dialog: PartDialog) -> None:
    dialog._dismissed = True
    dialog.close()


def _snap_plate(qtbot: QtBot, dialog: PartDialog) -> None:
    dialog.plate.floor_button.click()
    for point in _RECT:
        _click(qtbot, dialog, *point)
    dialog.plate.close_button.click()
    dialog.plate.select_button.click()
    for index, point in enumerate(_RECT):
        _click(qtbot, dialog, *dialog.plate.session.points[index])
        dialog.plate.x_spin.setValue(point[0])
        dialog.plate.y_spin.setValue(point[1])
        dialog.plate.x_spin.editingFinished.emit()
    assert dialog.plate.session.points == _RECT


def _draw_lane(qtbot: QtBot, dialog: PartDialog, points: tuple[tuple[float, float], ...]) -> int:
    plate = dialog.plate
    plate.lane_button.click()
    assert plate.lane_button.isChecked()
    assert not plate.floor_button.isChecked()
    assert plate.add_lane_button.isEnabled()
    plate.add_lane_button.click()
    assert "wird gezeichnet" in plate.drawing_label.text()
    for point in points:
        _click(qtbot, dialog, *point)
    lane = plate.session.active_lane
    assert lane is not None
    plate.select_button.click()
    for index, point in enumerate(points):
        _click(qtbot, dialog, *plate.session.lane_vertices(lane)[index])
        plate.x_spin.setValue(point[0])
        plate.y_spin.setValue(point[1])
        plate.x_spin.editingFinished.emit()
    assert plate.session.lane_vertices(lane) == points
    plate.lane_button.click()
    assert plate.finish_lane_button.isEnabled()
    plate.finish_lane_button.click()
    assert plate.session.lane_finished(lane)
    assert "wird bearbeitet" in plate.drawing_label.text()
    return lane


def test_clicks_draw_two_lanes_and_an_arc_then_reopen(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = _editor(qtbot, env)
    dialog.article.setText("EB-LANES")
    dialog.name.setText("Zwei Fahrspuren")
    assert dialog.plate.lane_button.text() == "Fahrspur"
    assert not dialog.plate.add_lane_button.isEnabled()
    _snap_plate(qtbot, dialog)
    dialog.plate.lane_button.click()
    dialog.plate.add_lane_button.click()
    _click(qtbot, dialog, *_LANE_A[0])
    _click(qtbot, dialog, *_LANE_A[1])
    _move(dialog, 120.0, 80.0)
    assert dialog.plate.session.hover is not None
    assert len(dialog.plate.session.lane_spans(0)) == 1
    dialog.plate.select_button.click()
    for index, point in enumerate(_LANE_A[:2]):
        _click(qtbot, dialog, *dialog.plate.session.lane_vertices(0)[index])
        dialog.plate.x_spin.setValue(point[0])
        dialog.plate.y_spin.setValue(point[1])
        dialog.plate.x_spin.editingFinished.emit()
    dialog.plate.lane_button.click()
    _click(qtbot, dialog, *_LANE_A[2])
    dialog.plate.select_button.click()
    _click(qtbot, dialog, *dialog.plate.session.lane_vertices(0)[2])
    dialog.plate.x_spin.setValue(_LANE_A[2][0])
    dialog.plate.y_spin.setValue(_LANE_A[2][1])
    dialog.plate.x_spin.editingFinished.emit()
    dialog.plate.lane_button.click()
    dialog.plate.finish_lane_button.click()
    assert dialog.plate.session.lane_vertices(0) == _LANE_A
    assert all(span.kind == SPAN_LINE for span in dialog.plate.session.lane_spans(0))
    second = _draw_lane(qtbot, dialog, _LANE_B)
    assert second == 1
    assert dialog.plate.session.lane_vertices(0) == _LANE_A
    assert dialog.plate.session.lane_vertices(1) == _LANE_B
    dialog.plate.select_button.click()
    _click(qtbot, dialog, 120.0, 50.0)
    assert dialog.plate.session.lane_span == (0, 0)
    before = spans_length_mm(dialog.plate.session.lane_spans(0))
    dialog.plate.arc_button.click()
    span = dialog.plate.session.lane_spans(0)[0]
    assert span.kind == SPAN_ARC
    dialog.plate.radius_spin.setValue(20.0)
    dialog.plate.radius_spin.editingFinished.emit()
    assert "halbe Sehne" in dialog.plate.issue.text()
    assert dialog.plate.session.lane_spans(0)[0].radius_mm == pytest.approx(160.0)
    dialog.plate.radius_spin.setValue(100.0)
    dialog.plate.radius_spin.editingFinished.emit()
    dialog.plate.left_button.click()
    span = dialog.plate.session.lane_spans(0)[0]
    assert span.kind == SPAN_ARC
    assert span.radius_mm == pytest.approx(100.0)
    assert span.sweep_deg < 0.0
    assert span_start(span) == pytest.approx(_LANE_A[0])
    assert span_end(span) == pytest.approx(_LANE_A[1])
    assert dialog.plate.angle_label.text() != "—"
    assert dialog.plate.segment_label.text() == f"{spans_length_mm((span,)):.3f}"
    length = spans_length_mm(dialog.plate.session.lane_spans(0))
    assert length != pytest.approx(before)
    assert dialog.plate.length_label.text() == f"{length:.3f}"
    assert dialog.plate.lane_label.text() == "1"
    _click(qtbot, dialog, *_LANE_A[2])
    dialog.plate.x_spin.setValue(210.0)
    dialog.plate.y_spin.setValue(130.0)
    dialog.plate.x_spin.editingFinished.emit()
    assert dialog.plate.session.lane_vertices(0)[2] == (210.0, 130.0)
    assert dialog.plate.session.points == _RECT
    moved = spans_length_mm(dialog.plate.session.lane_spans(0))
    assert dialog.plate.length_label.text() == f"{moved:.3f}"
    dialog.plate.undo_button.click()
    assert dialog.plate.session.lane_vertices(0)[2] == _LANE_A[2]
    assert dialog.plate.session.points == _RECT
    dialog.plate.redo_button.click()
    assert dialog.plate.session.lane_vertices(0)[2] == (210.0, 130.0)
    dialog.plate.canvas.repaint()
    expected = dialog.plate.session.sketch_lanes()
    dialog.accept()
    assert dialog.saved is not None
    stored = planner.library.require(dialog.saved.id)
    assert stored.spec.category == "draft"
    assert stored.spec.connectors == ()
    assert plate_corners(stored.spec) == _RECT
    loaded_lanes = draft_lanes(stored.spec)
    assert loaded_lanes == expected
    assert loaded_lanes is not None
    assert loaded_lanes[0][0].kind == SPAN_ARC
    track = env.track("Nicht platzierbar", lanes=2)
    with pytest.raises(ValidationError):
        place_instance(
            empty_plan(track.id),
            stored.id,
            stored.spec,
            4.0,
            5.0,
            0.0,
            {stored.id: stored.spec},
        )
    again = PartDialog(env.runtime.translator, planner, stored)
    qtbot.addWidget(again)
    again.show()
    again.tabs.setCurrentIndex(1)
    assert again.plate.editable
    assert again.plate.session.points == _RECT
    assert again.plate.session.sketch_lanes() == expected
    assert same_geometry(planner.library.require(stored.id).spec, stored.spec)
    _close(again)


def test_zoom_and_pan_select_the_lane_point(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    _snap_plate(qtbot, dialog)
    _draw_lane(qtbot, dialog, _LANE_A)
    dialog.plate.select_button.click()
    canvas = dialog.plate.canvas

    def _center(x_mm: float, y_mm: float, scale: float) -> None:
        canvas.set_view(
            scale,
            canvas.width() / 2.0 - x_mm * scale,
            canvas.height() / 2.0 - y_mm * scale,
        )

    last = dialog.plate.session.lane_vertices(0)[-1]
    _center(*last, 3.0)
    _click(qtbot, dialog, *last)
    assert dialog.plate.session.lane_point == (0, 2)
    first = dialog.plate.session.lane_vertices(0)[0]
    _center(*first, 0.6)
    _click(qtbot, dialog, *first)
    assert dialog.plate.session.lane_point == (0, 0)
    before = canvas._pan_x
    qtbot.mousePress(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(40, 40))  # type: ignore[no-untyped-call]
    qtbot.mouseMove(canvas, QPoint(80, 60))  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(80, 60))  # type: ignore[no-untyped-call]
    assert canvas._pan_x != before
    middle = dialog.plate.session.lane_vertices(0)[1]
    _center(*middle, 1.5)
    _click(qtbot, dialog, *middle)
    assert dialog.plate.session.lane_point == (0, 1)
    _close(dialog)


def test_opening_a_draft_without_edits_keeps_the_revision(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = _editor(qtbot, env)
    dialog.article.setText("EB-REV")
    dialog.name.setText("Revision")
    _snap_plate(qtbot, dialog)
    _draw_lane(qtbot, dialog, _LANE_A)
    dialog.accept()
    assert dialog.saved is not None
    saved = dialog.saved
    opened = PartDialog(env.runtime.translator, planner, planner.library.require(saved.id))
    qtbot.addWidget(opened)
    opened.show()
    opened.tabs.setCurrentIndex(1)
    opened.tabs.setCurrentIndex(0)
    _close(opened)
    kept = planner.library.require(saved.id)
    assert kept.id == saved.id
    assert kept.revision == saved.revision
    assert same_geometry(kept.spec, saved.spec)
    edited = PartDialog(env.runtime.translator, planner, kept)
    qtbot.addWidget(edited)
    edited.show()
    edited.tabs.setCurrentIndex(1)
    edited.plate.select_button.click()
    _click(qtbot, edited, *edited.plate.session.lane_vertices(0)[1])
    edited.plate.x_spin.setValue(180.0)
    edited.plate.y_spin.setValue(50.0)
    edited.plate.x_spin.editingFinished.emit()
    edited.accept()
    assert edited.saved is not None
    assert edited.saved.id == saved.id
    assert edited.saved.revision == saved.revision
    assert not same_geometry(edited.saved.spec, saved.spec)
    with env.runtime.database.session() as session:
        rows = session.scalars(
            select(TrackPartDefinition).where(TrackPartDefinition.lineage_id == saved.lineage_id)
        ).all()
    assert [(row.id, row.revision) for row in rows] == [(saved.id, saved.revision)]


def test_complex_parts_keep_their_geometry_when_metadata_changes(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    custom = _save_draft(planner, _draft(length_mm=400.0), article="EB-BZ3", name="Bogen")
    bent = ed.explicit(custom.spec)
    ref = ed.groove_refs(bent)[0]
    bent = ed.convert_span(bent, ref, 0, SPAN_BEZIER)
    bent = ed.finalize(bent, custom.spec)
    complex_part = planner.update_part(custom.id, bent)
    records = (
        _article(planner, STANDARD_CURVE_ARTICLE),
        _article(planner, "20020517"),
        _article(planner, "20030373"),
        complex_part,
    )
    for record in records:
        before = record.spec
        dialog = _editor(qtbot, env, record)
        assert not dialog.plate.editable
        assert "nicht punktweise" in dialog.plate.issue.text()
        dialog.tabs.setCurrentIndex(0)
        dialog.tabs.setCurrentIndex(1)
        dialog.plate.canvas.repaint()
        dialog.description.setPlainText(f"{record.spec.description} Hinweis")
        dialog.accept()
        assert dialog.saved is not None
        assert dialog.saved.revision == record.revision
        assert same_geometry(dialog.saved.spec, before)
        assert same_geometry(planner.library.require(record.id).spec, before)
        assert dialog.saved.spec.slot_paths == before.slot_paths


def test_lane_clicks_modes_and_repeated_dialogs_do_not_crash(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    _snap_plate(qtbot, dialog)
    _draw_lane(qtbot, dialog, _LANE_A)
    _draw_lane(qtbot, dialog, _LANE_B)
    dialog.plate.select_button.click()
    _click(qtbot, dialog, *_LANE_A[0])
    assert dialog.plate.session.lane_point == (0, 0)
    _click(qtbot, dialog, *_LANE_B[2])
    assert dialog.plate.session.lane_point == (1, 2)
    _click(qtbot, dialog, 140.0, 120.0)
    assert dialog.plate.session.lane_span == (1, 0)
    dialog.plate.floor_button.click()
    assert dialog.plate.floor_button.isChecked()
    assert "Bodenplatte" in dialog.plate.drawing_label.text()
    dialog.plate.lane_button.click()
    dialog.plate.add_lane_button.click()
    _click(qtbot, dialog, 80.0, 80.0)
    assert dialog.plate.session.unfinished_lane() == 2
    qtbot.keyClick(dialog.plate.canvas, Qt.Key.Key_Escape)  # type: ignore[no-untyped-call]
    assert dialog.plate.session.unfinished_lane() is None
    assert len(dialog.plate.session.lanes) == 2
    assert dialog.plate.session.points == _RECT
    dialog.plate.select_button.click()
    dialog.plate.lane_button.click()
    dialog.plate.floor_button.click()
    _close(dialog)
    for _ in range(3):
        other = PartDialog(env.runtime.translator, _planner(env))
        qtbot.addWidget(other)
        other.show()
        other.tabs.setCurrentIndex(1)
        other.tabs.setCurrentIndex(0)
        _close(other)
