"""Mouse-driven track planner: library, selection, undo, pan, zoom, rotation and paste."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QDropEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import TrackId
from slot_racing.modules.track_planner.document import (
    add_instance,
    duplicate_instances,
    empty_plan,
    instances_center,
    rotate_instances_around,
)
from slot_racing.modules.track_planner.parts import STRAIGHT, PartInstance, build_part
from slot_racing.modules.track_planner.ui.canvas import (
    InstanceItem,
    rect_fully_inside,
)
from slot_racing.modules.track_planner.ui.library_view import PART_MIME
from slot_racing.modules.track_planner.ui.page import PlannerPage
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _library_row, _planner, _select
from tests.modules.test_ui_management import open_page


def test_rect_fully_inside_rejects_a_partial_overlap() -> None:
    outer = QRectF(0, 0, 100, 80)
    assert rect_fully_inside(QRectF(10, 10, 20, 20), outer)
    assert not rect_fully_inside(QRectF(90, 10, 20, 20), outer)


def test_group_rotation_keeps_distances_to_the_shared_centre() -> None:
    first = PartInstance("a", 1, 0, -40, rotation_z_deg=10)
    second = PartInstance("b", 1, 0, 40, rotation_z_deg=20)
    plan = add_instance(add_instance(empty_plan(TrackId(1)), first), second)
    center = instances_center((first, second))
    turned = rotate_instances_around(plan, ("a", "b"), center[0], center[1], 90)
    back = rotate_instances_around(turned, ("a", "b"), center[0], center[1], -90)
    assert center == (0, 0)
    for original, rotated in zip(plan.instances, turned.instances, strict=True):
        assert _distance(original, center) == _distance(rotated, center)
        assert rotated.rotation_z_deg == (original.rotation_z_deg + 90) % 360
        assert rotated.part_id == original.part_id
    for original, restored in zip(plan.instances, back.instances, strict=True):
        assert restored.x_mm == original.x_mm
        assert restored.y_mm == original.y_mm
        assert restored.rotation_z_deg == original.rotation_z_deg


def test_duplicating_instances_keeps_the_definition_and_the_spacing() -> None:
    plan = add_instance(empty_plan(TrackId(1)), PartInstance("a", 7, 10, 20, rotation_z_deg=37))
    plan = add_instance(plan, PartInstance("b", 8, 110, 20, rotation_z_deg=80))
    copied, created = duplicate_instances(plan, ("a", "b"), 80, 80)
    assert created != ("a", "b")
    assert [instance.part_id for instance in copied.instances[:2]] == [7, 8]
    clones = [instance for instance in copied.instances if instance.id in created]
    assert [instance.part_id for instance in clones] == [7, 8]
    assert clones[1].x_mm - clones[0].x_mm == plan.instances[1].x_mm - plan.instances[0].x_mm
    assert clones[0].rotation_z_deg == 37
    assert plan.instances[0].x_mm == 10


def test_enter_saves_the_open_plan_and_does_not_delete_the_track(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heim", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.track_combo.setFocus()
    QTest.keyClick(page.track_combo, Qt.Key.Key_Return)
    assert page.isVisible()
    assert window.isVisible()
    assert page.status.text() == "Streckenplan gespeichert."
    page.delete_track_button.setFocus()
    QTest.keyClick(page.delete_track_button, Qt.Key.Key_Return)
    assert page.isVisible()
    assert page.track_combo.findData(track.id) >= 0


def test_the_library_is_the_only_part_source_and_can_be_dropped(qtbot: QtBot, env: Env) -> None:
    track = env.track("Heim", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    for button in (page.add_horizontal, page.add_vertical, page.add_curve, page.add_start):
        assert button.isHidden()
    assert page.library.count() > 0
    assert page.library.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    item = page.library.item(0)
    assert item is not None
    card = page.library.itemWidget(item)
    assert isinstance(card, QWidget)
    preview = card.findChild(QWidget, "planner-part-preview")
    name = card.findChild(QLabel, "planner-part-name")
    article = card.findChild(QLabel, "planner-part-article")
    scale = card.findChild(QLabel, "planner-part-scale")
    assert preview is not None and name is not None and article is not None and scale is not None
    card.resize(340, max(72, card.sizeHint().height()))
    layout = card.layout()
    assert layout is not None
    layout.activate()
    assert preview.findChildren(QLabel) == []
    assert card.findChild(QLabel, "planner-part-system") is None
    assert card.findChild(QLabel, "planner-part-category") is None
    assert item.text() == ""
    assert not preview.geometry().intersects(name.geometry())
    assert not preview.geometry().intersects(article.geometry())
    mime = page.library.mimeData([item])
    assert mime.hasFormat(PART_MIME)
    part_id = int(bytes(mime.data(PART_MIME).data()).decode("ascii"))
    event = QDropEvent(
        QPointF(120, 80),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    page.canvas.dropEvent(event)
    assert len(page.plan().instances) == 1
    assert page.plan().instances[0].part_id == part_id
    assert page.canvas.selected_ids() == [page.plan().instances[0].id]
    assert page.canvas.rotation_handle.isVisible()
    assert page.undo_button.isEnabled()


def test_library_rows_show_only_the_rail_and_stay_apart(qtbot: QtBot, env: Env) -> None:
    planner = _planner(env)
    long_name = "Sehr lange Bezeichnung der Sondergeraden im Streckenplaner"
    planner.add_part(
        build_part(
            article_number="EB-LONG",
            scale="1:24",
            name=long_name,
            category=STRAIGHT,
            length_mm=400,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=2,
        )
    )
    track = env.track("Bibliothek", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.resize(980, 520)
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    qtbot.waitExposed(window)
    labels = {"planner-part-name", "planner-part-article", "planner-part-scale"}
    for row in range(page.library.count()):
        item = page.library.item(row)
        assert item is not None and item.text() == ""
        card = page.library.itemWidget(item)
        assert isinstance(card, QWidget)
        shown = {label.objectName() for label in card.findChildren(QLabel)}
        assert shown == labels
        preview = card.findChild(QWidget, "planner-part-preview")
        name = card.findChild(QLabel, "planner-part-name")
        assert preview is not None and name is not None
        assert preview.findChildren(QLabel) == []
        if row + 1 < page.library.count():
            nxt = page.library.item(row + 1)
            assert nxt is not None
            top = page.library.visualItemRect(item)
            below = page.library.visualItemRect(nxt)
            assert top.height() > 0
            assert top.bottom() <= below.top()
    long_item = page.library.item(_library_row(page, "EB-LONG"))
    assert long_item is not None
    long_card = page.library.itemWidget(long_item)
    assert isinstance(long_card, QWidget)
    long_label = long_card.findChild(QLabel, "planner-part-name")
    assert long_label is not None and long_label.text() == long_name
    assert page.library.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert page.library.verticalScrollBar().maximum() > 0


def test_selection_delete_undo_and_paste(qtbot: QtBot, env: Env) -> None:
    track = env.track("Oval", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.grid.setChecked(False)
    page.library.setCurrentRow(0)
    page.place_part.click()
    page.x_mm.setValue(0)
    page.y_mm.setValue(0)
    page.place_part.click()
    assert len(page.plan().instances) == 2
    ordered = sorted(_instances(page), key=lambda item: item.sceneBoundingRect().left())
    assert len(ordered) == 2
    first, second = ordered
    page.canvas.select_fully_inside(first.sceneBoundingRect().adjusted(-1, -1, 1, 1))
    assert page.canvas.selected_ids() == [first.item_id]
    both = first.sceneBoundingRect().united(second.sceneBoundingRect()).adjusted(-2, -2, 2, 2)
    page.canvas.select_fully_inside(both)
    assert set(page.canvas.selected_ids()) == {first.item_id, second.item_id}
    partial = QRectF(both)
    partial.setRight(second.sceneBoundingRect().center().x())
    page.canvas.select_fully_inside(partial)
    assert second.item_id not in page.canvas.selected_ids()
    assert first.item_id in page.canvas.selected_ids()

    definitions = page.library.count()
    page.canvas.select_fully_inside(both)
    _key(qtbot, page, Qt.Key.Key_Delete)
    assert page.plan().instances == ()
    assert page.library.count() == definitions
    assert _planner(env).list_parts()
    page.undo()
    assert len(page.plan().instances) == 2

    kept = [
        (item.part_id, item.x_mm, item.y_mm, item.rotation_z_deg) for item in page.plan().instances
    ]
    page.canvas.select_fully_inside(both)
    _key(qtbot, page, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    _key(qtbot, page, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert len(page.plan().instances) == 4
    originals = page.plan().instances[:2]
    clones = page.plan().instances[2:]
    assert [(item.part_id, item.x_mm, item.y_mm, item.rotation_z_deg) for item in originals] == kept
    assert originals[1].x_mm - originals[0].x_mm == clones[1].x_mm - clones[0].x_mm
    assert {item.part_id for item in clones} == {item.part_id for item in originals}
    assert set(page.canvas.selected_ids()) == {item.id for item in clones}
    assert page.library.count() == definitions
    _key(qtbot, page, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert len(page.plan().instances) == 2

    left = min(_instances(page), key=lambda item: item.sceneBoundingRect().left())
    page.canvas.select_fully_inside(left.sceneBoundingRect().adjusted(-1, -1, 1, 1))
    before = page.plan().instances[0].x_mm
    page.x_mm.setValue(before + 50)
    assert page.plan().instances[0].x_mm != before
    page.undo()
    assert page.plan().instances[0].x_mm == before
    page.rotation_free.setValue(37)
    assert page.plan().instances[0].rotation_z_deg == 37
    page.undo_button.click()
    assert page.plan().instances[0].rotation_z_deg != 37


def test_group_rotation_handle_and_both_directions(qtbot: QtBot, env: Env) -> None:
    track = env.track("Kurve", lanes=3)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.grid.setChecked(False)
    page.library.setCurrentRow(0)
    page.place_part.click()
    page.x_mm.setValue(0)
    page.y_mm.setValue(-80)
    page.place_part.click()
    page.x_mm.setValue(0)
    page.y_mm.setValue(80)
    selected = tuple(page.plan().instances)
    center = instances_center(selected)
    page.canvas.select_fully_inside(QRectF(-500, -500, 1000, 1000))
    assert page.canvas.rotation_handle.isVisible()
    knob = _knob_point(page)
    viewport = page.canvas.viewport()
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=knob)  # type: ignore[no-untyped-call]
    assert page.canvas.rotation_handle.dragging
    turned = knob + _knob_drag(page)
    qtbot.mouseMove(viewport, pos=turned)  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=turned)  # type: ignore[no-untyped-call]
    assert not page.canvas.rotation_handle.dragging
    dragged = page.plan().instances
    assert dragged[0].rotation_z_deg != selected[0].rotation_z_deg
    assert dragged[1].rotation_z_deg != selected[1].rotation_z_deg
    assert math.isclose(_distance(dragged[0], center), _distance(selected[0], center), abs_tol=0.05)
    assert math.isclose(_distance(dragged[1], center), _distance(selected[1], center), abs_tol=0.05)
    page.undo()
    page._rotated([item.id for item in selected], center[0], center[1], -35)
    turned = page.plan().instances
    assert turned[0].rotation_z_deg == (-35) % 360
    assert _distance(selected[0], center) == _distance(turned[0], center)
    assert _distance(selected[1], center) == _distance(turned[1], center)
    page._rotated([item.id for item in turned], center[0], center[1], 82)
    again = page.plan().instances
    assert again[0].rotation_z_deg == (-35 + 82) % 360
    page.undo()
    page.undo()
    assert page.plan().instances[0].rotation_z_deg == selected[0].rotation_z_deg
    assert page.plan().instances[0].y_mm == selected[0].y_mm


def test_pan_and_zoom_move_only_the_view(qtbot: QtBot, env: Env) -> None:
    track = env.track("Pan", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.library.setCurrentRow(0)
    page.place_part.click()
    stored = (page.plan().instances[0].x_mm, page.plan().instances[0].y_mm)
    canvas = page.canvas
    assert canvas.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert canvas.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert not canvas.horizontalScrollBar().isVisible()
    assert not canvas.verticalScrollBar().isVisible()
    watch = QPoint(160, 120)
    before = canvas.mapToScene(watch)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.MouseButton.RightButton, pos=watch)  # type: ignore[no-untyped-call]
    qtbot.mouseMove(viewport, pos=watch + QPoint(40, -25))  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(viewport, Qt.MouseButton.RightButton, pos=watch + QPoint(40, -25))  # type: ignore[no-untyped-call]
    after = canvas.mapToScene(watch)
    assert after != before
    assert (page.plan().instances[0].x_mm, page.plan().instances[0].y_mm) == stored
    scale = canvas.transform().m11()
    anchor = QPointF(180, 140)
    scene_anchor = canvas.mapToScene(anchor.toPoint())
    canvas.zoom_at(anchor, 120)
    assert canvas.transform().m11() > scale
    assert _near(canvas.mapToScene(anchor.toPoint()), scene_anchor)
    canvas.zoom_at(anchor, -120)
    assert math.isclose(canvas.transform().m11(), scale, rel_tol=1e-6)
    wheel = QWheelEvent(
        QPointF(180, 140),
        QPointF(180, 140),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    canvas.wheelEvent(wheel)
    assert math.isclose(canvas.transform().m11(), scale, rel_tol=1e-6)
    canvas.wheelEvent(
        QWheelEvent(
            QPointF(180, 140),
            QPointF(180, 140),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
    )
    assert canvas.transform().m11() > scale


def test_a_rubber_band_drag_selects_only_parts_inside_it(qtbot: QtBot, env: Env) -> None:
    track = env.track("Box", lanes=4)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.grid.setChecked(False)
    page.library.setCurrentRow(0)
    page.place_part.click()
    page.x_mm.setValue(0)
    page.y_mm.setValue(0)
    page.place_part.click()
    ordered = sorted(_instances(page), key=lambda item: item.sceneBoundingRect().left())
    assert len(ordered) == 2
    inside, outside = ordered
    canvas = page.canvas
    viewport = canvas.viewport()
    covered = inside.sceneBoundingRect()
    origin = canvas.mapFromScene(covered.topLeft() - QPointF(12, 12))
    end = canvas.mapFromScene(
        QPointF(outside.sceneBoundingRect().center().x(), covered.bottom() + 12)
    )
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=origin)  # type: ignore[no-untyped-call]
    assert canvas._band.isVisible()
    qtbot.mouseMove(viewport, pos=end)  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)  # type: ignore[no-untyped-call]
    assert not canvas._band.isVisible()
    assert canvas.selected_ids() == [inside.item_id]
    click = canvas.mapFromScene(inside.sceneBoundingRect().center())
    qtbot.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=click)  # type: ignore[no-untyped-call]
    assert canvas.selected_ids() == [inside.item_id]


def _key(
    qtbot: QtBot,
    page: PlannerPage,
    key: Qt.Key,
    modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, key, modifier)  # type: ignore[no-untyped-call]


def _knob_point(page: PlannerPage) -> QPoint:
    handle = page.canvas.rotation_handle
    origin = page.canvas.mapFromScene(handle.pos())
    knob = handle.knob_offset
    return origin + QPoint(round(knob.x()), round(knob.y()))


def _knob_drag(page: PlannerPage) -> QPoint:
    """A step across the pointer angle. Moving straight out from the centre would not rotate."""
    knob = page.canvas.rotation_handle.knob_offset
    side = QPoint(round(-knob.y()), round(knob.x()))
    length = math.hypot(side.x(), side.y())
    if length < 12:
        return QPoint(36, 24)
    scale = 40 / length
    return QPoint(round(side.x() * scale), round(side.y() * scale))


def _instances(page: PlannerPage) -> list[InstanceItem]:
    return [item for item in page.canvas.scene().items() if isinstance(item, InstanceItem)]


def _distance(instance: PartInstance, center: tuple[float, float]) -> float:
    return math.hypot(instance.x_mm - center[0], instance.y_mm - center[1])


def _near(actual: QPointF, expected: QPointF) -> bool:
    return abs(actual.x() - expected.x()) < 1.5 and abs(actual.y() - expected.y()) < 1.5
