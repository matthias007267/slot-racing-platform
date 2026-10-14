"""Mouse clicks create a floor plate, and existing parts stay untouched."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel, QPushButton
from pytestqt.qtbot import QtBot
from sqlalchemy import select

from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.parts import (
    SPAN_BEZIER,
    STANDARD_STRAIGHT_ARTICLE,
    same_geometry,
)
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.track_planner.ui.part_editor import PartDialog
from slot_racing.modules.tracks.models import TrackLayout
from tests.modules.conftest import Env
from tests.modules.test_parametric_editor import _draft, _save_draft
from tests.modules.test_part_revisions import _article, _catalog
from tests.modules.test_track_parts import _library_row, _planner, _select
from tests.modules.test_ui_management import open_page

_RECT = ((0.0, 0.0), (345.0, 0.0), (345.0, 200.0), (0.0, 200.0))


def _editor(qtbot: QtBot, env: Env, record: object = None) -> PartDialog:
    dialog = PartDialog(env.runtime.translator, _planner(env), record)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    dialog.resize(1100, 760)
    dialog.show()
    dialog.tabs.setCurrentIndex(1)
    qtbot.waitUntil(lambda: dialog.plate.canvas.width() > 200)
    dialog.plate.canvas.fit_rect(-10.0, -10.0, 360.0, 220.0)
    return dialog


def _click(qtbot: QtBot, dialog: PartDialog, x_mm: float, y_mm: float) -> None:
    canvas = dialog.plate.canvas
    pos = canvas.widget_point(x_mm, y_mm)
    assert canvas.rect().contains(pos)
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=pos)  # type: ignore[no-untyped-call]


def test_four_clicks_close_the_floor_plate(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    dialog.plate.floor_button.click()
    for point in _RECT:
        _click(qtbot, dialog, *point)
    _click(qtbot, dialog, 0.0, 0.0)
    session = dialog.plate.session
    assert session.closed
    assert len(session.points) == 4
    for actual, expected in zip(session.points, _RECT, strict=True):
        assert actual == pytest.approx(expected, abs=1.5)
    dialog.plate.canvas.repaint()
    dialog._dismissed = True
    dialog.close()


def test_the_mouse_preview_is_not_part_of_the_plate(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    dialog.plate.floor_button.click()
    _click(qtbot, dialog, 0.0, 0.0)
    canvas = dialog.plate.canvas
    qtbot.mouseMove(canvas, canvas.widget_point(120.0, 40.0))  # type: ignore[no-untyped-call]
    assert dialog.plate.session.hover is not None
    assert dialog.plate.session.hover == pytest.approx((120.0, 40.0), abs=1.5)
    assert len(dialog.plate.session.points) == 1
    assert dialog.plate.session.save_issue() == "error.planner.plate.short"
    dialog._dismissed = True
    dialog.close()


def test_a_selected_corner_can_be_edited_and_undone(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    dialog.plate.floor_button.click()
    for point in _RECT:
        _click(qtbot, dialog, *point)
    dialog.plate.close_button.click()
    assert dialog.plate.session.closed
    dialog.plate.select_button.click()
    origin = dialog.plate.session.points[1]
    _click(qtbot, dialog, *origin)
    assert dialog.plate.session.selected == 1
    dialog.plate.x_spin.setValue(300.0)
    dialog.plate.y_spin.setValue(0.0)
    dialog.plate.x_spin.editingFinished.emit()
    assert dialog.plate.session.points[1] == pytest.approx((300.0, 0.0))
    dialog.plate.undo_button.click()
    assert dialog.plate.session.points[1] == pytest.approx(origin)
    dialog._dismissed = True
    dialog.close()


def test_a_plate_saves_and_reopens_with_the_same_corners(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = _editor(qtbot, env)
    dialog.article.setText("EB-PLATE")
    dialog.name.setText("Bodenplatte")
    dialog.plate.floor_button.click()
    for point in _RECT:
        _click(qtbot, dialog, *point)
    _click(qtbot, dialog, *_RECT[0])
    dialog.plate.select_button.click()
    for index, point in enumerate(_RECT):
        _click(qtbot, dialog, *dialog.plate.session.points[index])
        dialog.plate.x_spin.setValue(point[0])
        dialog.plate.y_spin.setValue(point[1])
        dialog.plate.x_spin.editingFinished.emit()
    assert dialog.plate.session.points == _RECT
    dialog.accept()
    assert dialog.saved is not None
    stored = planner.library.require(dialog.saved.id)
    again = PartDialog(env.runtime.translator, planner, stored)
    qtbot.addWidget(again)
    again.show()
    again.tabs.setCurrentIndex(1)
    assert again.plate.session.points == _RECT
    assert again.plate.session.closed
    assert again.plate.editable
    assert not again.length.isEnabled()
    assert planner.library.require(stored.id).spec.category == "draft"
    assert same_geometry(planner.library.require(stored.id).spec, stored.spec)
    again._dismissed = True
    again.close()


def test_zoom_and_pan_still_hit_the_clicked_corner(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    dialog.plate.floor_button.click()
    for point in _RECT:
        _click(qtbot, dialog, *point)
    _click(qtbot, dialog, 0.0, 0.0)
    dialog.plate.select_button.click()
    canvas = dialog.plate.canvas

    def _center(x_mm: float, y_mm: float, scale: float) -> None:
        canvas.set_view(
            scale,
            canvas.width() / 2.0 - x_mm * scale,
            canvas.height() / 2.0 - y_mm * scale,
        )

    last = dialog.plate.session.points[-1]
    _center(*last, 2.4)
    _click(qtbot, dialog, *last)
    assert dialog.plate.session.selected == len(dialog.plate.session.points) - 1
    first = dialog.plate.session.points[0]
    _center(*first, 0.7)
    _click(qtbot, dialog, *first)
    assert dialog.plate.session.selected == 0
    before = canvas._pan_x
    qtbot.mousePress(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(40, 40))  # type: ignore[no-untyped-call]
    qtbot.mouseMove(canvas, QPoint(70, 55))  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(70, 55))  # type: ignore[no-untyped-call]
    assert canvas._pan_x != before
    corner = dialog.plate.session.points[2]
    _center(*corner, 1.2)
    _click(qtbot, dialog, *corner)
    assert dialog.plate.session.selected == 2
    dialog._dismissed = True
    dialog.close()


def test_an_open_or_invalid_plate_is_not_stored(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = _editor(qtbot, env)
    dialog.article.setText("EB-OPEN")
    dialog.name.setText("Offen")
    dialog.plate.floor_button.click()
    _click(qtbot, dialog, 0.0, 0.0)
    _click(qtbot, dialog, 0.0, 0.0)
    assert len(dialog.plate.session.points) == 1
    _click(qtbot, dialog, 40.0, 0.0)
    dialog.accept()
    assert dialog.saved is None
    assert dialog.isVisible()
    assert not any(record.spec.article_number == "EB-OPEN" for record in planner.list_parts())
    dialog._dismissed = True
    dialog.close()


def test_existing_parts_survive_the_graphic_tab(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    straight = _article(planner, STANDARD_STRAIGHT_ARTICLE)
    custom = _save_draft(planner, _draft(length_mm=400.0), article="EB-BZ2", name="Bogen")
    bent = ed.explicit(custom.spec)
    ref = ed.groove_refs(bent)[0]
    bent = ed.convert_span(bent, ref, 0, SPAN_BEZIER)
    bent = ed.finalize(bent, custom.spec)
    complex_part = planner.update_part(custom.id, bent)
    for record in (straight, complex_part):
        before = record.spec
        dialog = _editor(qtbot, env, record)
        assert not dialog.plate.editable
        assert "nicht punktweise" in dialog.plate.issue.text()
        dialog.tabs.setCurrentIndex(0)
        dialog.tabs.setCurrentIndex(1)
        dialog.plate.canvas.repaint()
        assert same_geometry(dialog.plate.reference or before, before)
        assert same_geometry(planner.library.require(record.id).spec, before)
        dialog._dismissed = True
        dialog.close()


def test_opening_a_used_part_does_not_fork_a_revision(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    record = _article(planner, "20020517")
    track = env.track("Bleibt", lanes=2)
    planner.save(
        place_instance(empty_plan(track.id), record.id, record.spec, 4, 5, 0, _catalog(record))
    )
    with env.runtime.database.session() as session:
        before = [
            (row.id, row.data)
            for row in session.scalars(select(TrackLayout).where(TrackLayout.name != "plan"))
        ]
    revision = planner.library.require(record.id).revision
    dialog = _editor(qtbot, env, record)
    dialog.tabs.setCurrentIndex(0)
    dialog._dismissed = True
    dialog.close()
    kept = planner.library.require(record.id)
    assert kept.id == record.id
    assert kept.revision == revision
    assert same_geometry(kept.spec, record.spec)
    assert planner.load(track.id).instances[0].part_id == record.id
    with env.runtime.database.session() as session:
        after = [
            (row.id, row.data)
            for row in session.scalars(select(TrackLayout).where(TrackLayout.name != "plan"))
        ]
    assert after == before


def test_undo_and_redo_follow_the_clicks(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    dialog.plate.floor_button.click()
    for point in _RECT:
        _click(qtbot, dialog, *point)
    dialog.plate.close_button.click()
    assert dialog.plate.session.closed
    dialog.plate.undo_button.click()
    assert not dialog.plate.session.closed
    assert len(dialog.plate.session.points) == 4
    dialog.plate.undo_button.click()
    assert len(dialog.plate.session.points) == 3
    dialog.plate.redo_button.click()
    dialog.plate.redo_button.click()
    assert dialog.plate.session.closed
    assert len(dialog.plate.session.points) == 4
    dialog._dismissed = True
    dialog.close()


def test_clicks_on_empty_space_and_repeated_dialogs_do_not_crash(qtbot: QtBot, env: Env) -> None:
    dialog = _editor(qtbot, env)
    dialog.plate.floor_button.click()
    for point in _RECT:
        _click(qtbot, dialog, *point)
    _click(qtbot, dialog, 0.0, 0.0)
    dialog.plate.select_button.click()
    _click(qtbot, dialog, *dialog.plate.session.points[0])
    assert dialog.plate.session.selected == 0
    _click(qtbot, dialog, *dialog.plate.session.points[-1])
    assert dialog.plate.session.selected == 3
    _click(qtbot, dialog, 160.0, 90.0)
    assert dialog.plate.session.selected is None
    dialog.plate.floor_button.click()
    _click(qtbot, dialog, 20.0, 20.0)
    assert dialog.plate.session.flash == "error.planner.plate.exists"
    assert len(dialog.plate.session.points) == 4
    qtbot.keyClick(dialog.plate.canvas, Qt.Key.Key_Escape)
    assert dialog.plate.session.closed
    dialog.plate.floor_button.click()
    dialog.plate.select_button.click()
    dialog._dismissed = True
    dialog.close()
    for _ in range(3):
        other = PartDialog(env.runtime.translator, _planner(env))
        qtbot.addWidget(other)
        other.show()
        other.tabs.setCurrentIndex(1)
        other.tabs.setCurrentIndex(0)
        other._dismissed = True
        other.close()


def test_the_planner_palette_hides_the_draft(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    dialog = _editor(qtbot, env)
    dialog.article.setText("EB-HIDE")
    dialog.name.setText("Versteckt")
    dialog.plate.floor_button.click()
    for point in _RECT:
        _click(qtbot, dialog, *point)
    dialog.plate.close_button.click()
    for index, point in enumerate(_RECT):
        dialog.plate.select_button.click()
        _click(qtbot, dialog, *dialog.plate.session.points[index])
        dialog.plate.x_spin.setValue(point[0])
        dialog.plate.y_spin.setValue(point[1])
        dialog.plate.x_spin.editingFinished.emit()
    dialog.accept()
    assert dialog.saved is not None
    track = env.track("Palette", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    with pytest.raises(AssertionError, match="EB-HIDE"):
        _library_row(page, "EB-HIDE")
    assert _library_row(page, STANDARD_STRAIGHT_ARTICLE) >= 0
    labels = []
    manager_text = []
    for row in range(page.library.count()):
        item = page.library.item(row)
        card = None if item is None else page.library.itemWidget(item)
        label = None if card is None else card.findChild(QLabel, "planner-part-article")
        if label is not None:
            labels.append(label.text())
    assert all("EB-HIDE" not in text for text in labels)
    from slot_racing.modules.track_planner.ui.library_manager import LibraryManager

    manager = LibraryManager(env.runtime.translator, planner)
    qtbot.addWidget(manager)
    manager.show()
    manager.search.setText("EB-HIDE")
    visible = [row for row in manager._rows if not row.isHidden()]
    assert any(row.part_id == dialog.saved.id for row in visible)
    manager_text.append("shown")
    assert manager_text == ["shown"]
    lane = dialog.findChild(QPushButton, "plate-lane")
    assert lane is not None and not lane.isEnabled()
