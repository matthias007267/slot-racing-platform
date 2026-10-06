"""Continue-build arrows: local directions, standard parts, snap, undo and overlays."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsItem
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.document import (
    empty_plan,
    extend_from_connector,
    place_instance,
    set_plan_grid,
    to_document,
    with_instances,
)
from slot_racing.modules.track_planner.figure import track_figure
from slot_racing.modules.track_planner.parts import (
    ARROW_FAN_DEG,
    EXTEND_LEFT,
    EXTEND_RIGHT,
    EXTEND_STRAIGHT,
    STANDARD_CURVE_ARTICLE,
    STANDARD_STRAIGHT_ARTICLE,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    arrow_heading_deg,
    connector_occupied,
    extend_article,
    extend_pose,
    find_catalog_part,
    offered_extend_directions,
    placed_continuation_delta_deg,
    standard_catalog,
    standard_extend_parts,
)
from slot_racing.modules.track_planner.ui.canvas import (
    ARROW_GLYPH_PX,
    ARROW_HIT_RADIUS,
    HANDLE_GAP_PX,
    ExtendArrow,
    InstanceItem,
)
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.uikit.theme import COLORS
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _library_row, _select
from tests.modules.test_track_planner_extend import _arrows
from tests.modules.test_track_planner_ui import _instances, _key
from tests.modules.test_ui_management import open_page


def test_standard_parts_are_chosen_by_article_number() -> None:
    by_article = {spec.article_number: spec for spec in standard_catalog()}
    straight = by_article[STANDARD_STRAIGHT_ARTICLE]
    other = by_article["20020611"]
    catalog = {
        9: replace(other, name="Standardgerade"),
        5: replace(straight, name="Ganz andere Gerade"),
        4: replace(straight, name="Noch eine Gerade"),
    }
    found = find_catalog_part(catalog, STANDARD_STRAIGHT_ARTICLE)
    assert found is not None
    assert found[0] == 4
    assert found[1].name == "Noch eine Gerade"
    assert extend_article(EXTEND_STRAIGHT) == STANDARD_STRAIGHT_ARTICLE
    assert extend_article(EXTEND_LEFT) == extend_article(EXTEND_RIGHT) == STANDARD_CURVE_ARTICLE
    chosen = standard_extend_parts(catalog)
    assert set(chosen) == {EXTEND_STRAIGHT}
    full = standard_extend_parts(_catalog())
    assert full[EXTEND_LEFT][0] == full[EXTEND_RIGHT][0]
    assert full[EXTEND_LEFT][1].article_number == STANDARD_CURVE_ARTICLE
    assert full[EXTEND_STRAIGHT][1].article_number == STANDARD_STRAIGHT_ARTICLE


def test_arrow_headings_follow_the_connector() -> None:
    assert pytest.approx(45) == ARROW_FAN_DEG
    assert arrow_heading_deg(0, EXTEND_STRAIGHT) == pytest.approx(0)
    assert arrow_heading_deg(0, EXTEND_LEFT) == pytest.approx(315)
    assert arrow_heading_deg(0, EXTEND_RIGHT) == pytest.approx(45)
    # A connector that faces up on the screen fans to ↖ ↑ ↗.
    assert arrow_heading_deg(270, EXTEND_LEFT) == pytest.approx(225)
    assert arrow_heading_deg(270, EXTEND_STRAIGHT) == pytest.approx(270)
    assert arrow_heading_deg(270, EXTEND_RIGHT) == pytest.approx(315)
    for direction in (EXTEND_LEFT, EXTEND_STRAIGHT, EXTEND_RIGHT):
        assert arrow_heading_deg(90, direction) == pytest.approx(
            (arrow_heading_deg(0, direction) + 90) % 360
        )


def test_left_and_right_use_one_curve_with_opposite_turns() -> None:
    catalog = _catalog()
    straight = standard_extend_parts(catalog)[EXTEND_STRAIGHT][1]
    curve = standard_extend_parts(catalog)[EXTEND_LEFT][1]
    joint = straight.connectors[1]
    for rotation in (0.0, 90.0, 180.0, 37.0):
        target = PartInstance("rail", 1, 12.0, -4.0, rotation_z_deg=rotation)
        for direction, sign in ((EXTEND_LEFT, -1), (EXTEND_RIGHT, 1)):
            pose = extend_pose(target, joint, curve, direction)
            assert pose is not None
            created = PartInstance(
                "next", 2, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg
            )
            delta = placed_continuation_delta_deg(target, joint, created, curve)
            assert delta == pytest.approx(sign * (curve.angle_deg or 0), abs=0.05)
        straight_pose = extend_pose(target, joint, straight, EXTEND_STRAIGHT)
        assert straight_pose is not None
        seated = PartInstance(
            "on",
            3,
            straight_pose.x_mm,
            straight_pose.y_mm,
            rotation_z_deg=straight_pose.rotation_z_deg,
        )
        assert placed_continuation_delta_deg(target, joint, seated, straight) == pytest.approx(
            0, abs=0.05
        )


def test_another_curve_continues_around_the_same_centre() -> None:
    catalog = _catalog()
    straight_id, straight = standard_extend_parts(catalog)[EXTEND_STRAIGHT]
    curve = standard_extend_parts(catalog)[EXTEND_LEFT][1]
    plan = place_instance(empty_plan(TrackId(1)), straight_id, straight, 0, 0, 0, catalog)
    plan = extend_from_connector(plan, plan.instances[0].id, 1, EXTEND_RIGHT, catalog)
    first = plan.instances[-1]
    spec = catalog[first.part_id]
    free = [
        index
        for index, joint in enumerate(spec.connectors)
        if not connector_occupied(first, joint, [(plan.instances[0], straight)])
    ]
    assert free == [1]
    assert EXTEND_RIGHT in offered_extend_directions(first, spec.connectors[1], straight, curve)
    updated = extend_from_connector(plan, first.id, 1, EXTEND_RIGHT, catalog)
    second = updated.instances[-1]
    assert second.part_id == first.part_id
    assert second.x_mm == pytest.approx(first.x_mm, abs=0.01)
    assert second.y_mm == pytest.approx(first.y_mm, abs=0.01)
    assert second.rotation_z_deg != pytest.approx(first.rotation_z_deg)
    assert placed_continuation_delta_deg(first, spec.connectors[1], second, spec) == pytest.approx(
        spec.angle_deg or 0, abs=0.05
    )


def test_extend_respects_compatibility_snap_and_leaves_failures_unchanged() -> None:
    catalog = _catalog()
    straight_id, straight = standard_extend_parts(catalog)[EXTEND_STRAIGHT]
    curve = standard_extend_parts(catalog)[EXTEND_LEFT][1]
    plan = place_instance(empty_plan(TrackId(1)), straight_id, straight, 0, 0, 0, catalog)
    moved = replace(plan.instances[0], x_mm=3, y_mm=7, rotation_z_deg=15)
    plan = set_plan_grid(with_instances(plan, (moved,)), enabled=True, grid_mm=10, snap_mm=25)
    joint = straight.connectors[1]
    pose = extend_pose(moved, joint, straight, EXTEND_STRAIGHT)
    assert pose is not None
    updated = extend_from_connector(plan, moved.id, 1, EXTEND_STRAIGHT, catalog)
    assert [instance.id for instance in plan.instances] == [moved.id]
    created = updated.instances[-1]
    assert created.part_id == straight_id
    assert created.x_mm == pytest.approx(pose.x_mm, abs=1e-6)
    assert created.y_mm == pytest.approx(pose.y_mm, abs=1e-6)
    assert created.rotation_z_deg == pytest.approx(pose.rotation_z_deg, abs=1e-6)
    assert _gap(moved, joint, created, straight) == pytest.approx(0, abs=1e-4)
    assert placed_continuation_delta_deg(moved, joint, created, straight) == pytest.approx(0)
    document = to_document(updated)
    assert "arrow" not in document
    for instance in document["instances"]:
        assert "arrow" not in instance
        assert "extend" not in instance
    with pytest.raises(ValidationError) as occupied:
        extend_from_connector(updated, moved.id, 1, EXTEND_STRAIGHT, catalog)
    assert occupied.value.key == "error.planner.extend"
    assert len(updated.instances) == 2

    border = next(spec for spec in standard_catalog() if spec.article_number == "20020560")
    border_id = max(catalog) + 1
    catalog[border_id] = border
    border_plan = place_instance(empty_plan(TrackId(2)), border_id, border, 0, 0, 0, catalog)
    border_joint = border.connectors[0]
    assert offered_extend_directions(border_plan.instances[0], border_joint, straight, curve) == ()
    with pytest.raises(ValidationError) as incompatible:
        extend_from_connector(border_plan, border_plan.instances[0].id, 0, EXTEND_RIGHT, catalog)
    assert incompatible.value.key == "error.planner.extend"
    assert len(border_plan.instances) == 1

    without_straight = {
        part_id: spec
        for part_id, spec in catalog.items()
        if spec.article_number != STANDARD_STRAIGHT_ARTICLE
    }
    with pytest.raises(ValidationError):
        extend_from_connector(plan, moved.id, 1, EXTEND_STRAIGHT, without_straight)


def test_the_track_figure_stays_the_rail_geometry() -> None:
    straight = next(
        spec for spec in standard_catalog() if spec.article_number == STANDARD_STRAIGHT_ARTICLE
    )
    curve = next(
        spec for spec in standard_catalog() if spec.article_number == STANDARD_CURVE_ARTICLE
    )
    straight_figure = track_figure(straight)
    curve_figure = track_figure(curve)
    assert straight_figure.roadway == straight.outline
    assert straight_figure.road_arc is None
    assert straight_figure.outer_shoulder == ()
    assert straight_figure.inner_shoulder == ()
    assert curve_figure.road_arc is not None
    assert curve_figure.road_arc.radius_mm == pytest.approx(curve.radius_mm or 0)
    assert curve_figure.road_arc.angle_deg == pytest.approx(curve.angle_deg or 0)
    assert curve_figure.outer_shoulder == ()
    assert track_figure(straight) is straight_figure


def test_an_arrow_glyph_has_no_ring_and_a_larger_hit_area() -> None:
    import slot_racing.modules.track_planner.ui.canvas as canvas_module

    assert not hasattr(canvas_module, "PlusItem")
    arrow = ExtendArrow("part", 0, EXTEND_STRAIGHT, QPointF(40, 40), 0)
    assert arrow.glyph_px == ARROW_GLYPH_PX <= 14
    assert arrow.hit_radius == ARROW_HIT_RADIUS > arrow.glyph_px / 2
    image = QImage(80, 80, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    arrow.paint(painter, None)
    painter.end()
    ink = [
        (x, y)
        for x in range(image.width())
        for y in range(image.height())
        if image.pixel(x, y) & 0xFF000000
    ]
    assert ink
    xs = [point[0] for point in ink]
    ys = [point[1] for point in ink]
    assert max(xs) - min(xs) <= 14
    assert max(ys) - min(ys) <= 14
    assert len(ink) < 100
    assert image.pixel(int(40 + arrow.hit_radius), 40) & 0xFF000000 == 0


def test_a_selected_part_has_no_outer_rectangle(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Rand")
    _place(page, STANDARD_STRAIGHT_ARTICLE)
    item = _instances(page)[0]
    assert item.isSelected()
    rect = item.sceneBoundingRect()
    pad = 3
    source = rect.adjusted(-pad, -pad, pad, pad)
    width = max(math.ceil(source.width()), 1)
    height = max(math.ceil(source.height()), 1)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(COLORS.background))
    painter = QPainter(image)
    page.canvas.scene().render(painter, QRectF(0, 0, width, height), source)
    painter.end()
    accent = QColor(COLORS.accent)
    corners = (
        (pad, pad),
        (pad, height - pad - 1),
        (width - pad - 1, pad),
        (width - pad - 1, height - pad - 1),
    )
    for x, y in corners:
        color = QColor(image.pixel(x, y))
        assert abs(color.green() - accent.green()) > 40
    # The roadway edge itself must not be redrawn as a selection rectangle.
    edge = QColor(image.pixel(width // 2, pad + 4))
    assert abs(edge.green() - accent.green()) > 40
    center = QColor(image.pixel(width // 2, height // 2))
    assert center.rgb() != QColor(COLORS.background).rgb()


def test_the_rotation_handle_sits_just_outside_the_rail(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Griff")
    _place(page, STANDARD_STRAIGHT_ARTICLE)
    straight = _instances(page)[0]
    straight_gap = _knob_gap(page, straight)
    assert straight_gap == pytest.approx(HANDLE_GAP_PX, abs=3)
    legacy = _legacy_gap(page, straight)
    assert straight_gap < legacy

    _place(page, STANDARD_CURVE_ARTICLE, x=800, y=0)
    curve = max(_instances(page), key=lambda item: item.pos().x())
    curve_gap = _knob_gap(page, curve)
    assert curve_gap == pytest.approx(HANDLE_GAP_PX, abs=3)
    assert curve_gap + 8 < _legacy_gap(page, curve)
    assert page.canvas.rotation_handle.isVisible()


def test_free_joints_show_local_arrows_and_occupied_joints_do_not(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Pfeile")
    _place(page, STANDARD_STRAIGHT_ARTICLE)
    arrows = _arrows(page)
    assert len(arrows) == 6
    assert {arrow.direction for arrow in arrows} == {EXTEND_LEFT, EXTEND_STRAIGHT, EXTEND_RIGHT}
    ignores = QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
    assert all(arrow.flags() & ignores for arrow in arrows)
    before = sorted(arrow.heading_deg for arrow in arrows if arrow.direction == EXTEND_STRAIGHT)
    page.rotation_free.setValue(90)
    after = sorted(
        arrow.heading_deg for arrow in _arrows(page) if arrow.direction == EXTEND_STRAIGHT
    )
    assert after == pytest.approx(sorted((heading + 90) % 360 for heading in before))

    _place(page, "20020560", x=900, y=400)
    assert _arrows(page) == []
    _place(page, "20030341", x=900, y=-400)
    assert _arrows(page) == []


def test_clicking_an_arrow_extends_selects_and_undoes_in_one_step(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Bau")
    original = _place(page, STANDARD_STRAIGHT_ARTICLE)
    straight = _arrow_facing(page, EXTEND_STRAIGHT, 0)
    _click(qtbot, page, straight)
    assert len(page.plan().instances) == 2
    created = page.plan().instances[-1]
    assert page.canvas.selected_ids() == [created.id]
    assert page._parts[created.part_id].article_number == STANDARD_STRAIGHT_ARTICLE
    source = page._parts[original.part_id]
    joint = source.connectors[straight.connector_index]
    pose = extend_pose(original, joint, page._parts[created.part_id], EXTEND_STRAIGHT)
    assert pose is not None
    assert created.x_mm == pytest.approx(pose.x_mm, abs=1e-6)
    assert created.rotation_z_deg == pytest.approx(pose.rotation_z_deg, abs=1e-6)
    assert placed_continuation_delta_deg(original, joint, created, source) == pytest.approx(0)
    fresh = _arrows(page)
    assert fresh
    assert all(arrow.instance_id == created.id for arrow in fresh)
    _select_instance(page, original.id)
    assert all(arrow.connector_index != straight.connector_index for arrow in _arrows(page))

    page._extended(original.id, straight.connector_index, EXTEND_STRAIGHT)
    assert len(page.plan().instances) == 2
    assert "weitergebaut" in page.status.text()

    snapshot = (
        created.id,
        created.part_id,
        created.x_mm,
        created.y_mm,
        created.rotation_z_deg,
    )
    page.undo()
    assert created.id not in {instance.id for instance in page.plan().instances}
    assert page.redo_button.isEnabled()
    page.redo_button.click()
    restored = next(instance for instance in page.plan().instances if instance.id == created.id)
    assert (
        restored.id,
        restored.part_id,
        restored.x_mm,
        restored.y_mm,
        restored.rotation_z_deg,
    ) == snapshot
    page.undo()
    _key(qtbot, page, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert created.id in {instance.id for instance in page.plan().instances}
    page.undo()
    _select_instance(page, original.id)
    page.x_mm.setValue(25)
    assert not page.redo_button.isEnabled()

    page.undo()
    _select_instance(page, original.id)
    left = _arrow_facing(page, EXTEND_LEFT, 0)
    _click(qtbot, page, left)
    curve = page.plan().instances[-1]
    curve_spec = page._parts[curve.part_id]
    assert curve_spec.article_number == STANDARD_CURVE_ARTICLE
    assert page.canvas.selected_ids() == [curve.id]
    assert placed_continuation_delta_deg(
        original, source.connectors[left.connector_index], curve, curve_spec
    ) == pytest.approx(-(curve_spec.angle_deg or 0), abs=0.05)
    right = _arrow_facing(page, EXTEND_RIGHT, 0)
    _click(qtbot, page, right)
    other = page.plan().instances[-1]
    other_spec = page._parts[other.part_id]
    assert other_spec.article_number == STANDARD_CURVE_ARTICLE
    assert other.part_id == curve.part_id
    host = next(instance for instance in page.plan().instances if instance.id == right.instance_id)
    host_spec = page._parts[host.part_id]
    assert placed_continuation_delta_deg(
        host, host_spec.connectors[right.connector_index], other, other_spec
    ) == pytest.approx(other_spec.angle_deg or 0, abs=0.05)
    assert page.canvas.selected_ids() == [other.id]
    assert _arrows(page)
    page.copy_selection()
    page.paste_selection()
    assert len(page.plan().instances) == 4
    page.delete_selected()
    assert other.id in {instance.id for instance in page.plan().instances}
    assert len(page.plan().instances) == 3

    page.save()
    loaded = page._planner.load(page.plan().track_id)

    def poses(
        instances: tuple[PartInstance, ...],
    ) -> set[tuple[str, int, float, float, float]]:
        return {
            (
                item.id,
                item.part_id,
                round(item.x_mm, 4),
                round(item.y_mm, 4),
                round(item.rotation_z_deg, 4),
            )
            for item in instances
        }

    assert poses(loaded.instances) == poses(page.plan().instances)


def test_arrows_follow_a_rotated_curve_and_stay_clickable_when_zoomed(
    qtbot: QtBot, env: Env
) -> None:
    page = _open(qtbot, env, "Drehung")
    curve = _place(page, STANDARD_CURVE_ARTICLE)
    page.rotation_free.setValue(90)
    curve = next(instance for instance in page.plan().instances if instance.id == curve.id)
    right = _arrows(page)
    assert right
    chosen = next(arrow for arrow in right if arrow.direction == EXTEND_RIGHT)
    _click(qtbot, page, chosen)
    created = page.plan().instances[-1]
    created_spec = page._parts[created.part_id]
    host_spec = page._parts[curve.part_id]
    assert created_spec.article_number == STANDARD_CURVE_ARTICLE
    assert placed_continuation_delta_deg(
        curve, host_spec.connectors[chosen.connector_index], created, created_spec
    ) == pytest.approx(created_spec.angle_deg or 0, abs=0.05)

    page.canvas.scene().clearSelection()
    for item in _instances(page):
        item.setSelected(True)
    assert _arrows(page) == []
    assert page.canvas.rotation_handle.isVisible()

    _select_instance(page, created.id)
    before = page.canvas.transform().m11()
    page.canvas.zoom_at(QPointF(page.canvas.width() / 2, page.canvas.height() / 2), 240)
    assert page.canvas.transform().m11() > before
    arrow = _arrows(page)[0]
    assert arrow.glyph_px == ARROW_GLYPH_PX
    hit = page.canvas.itemAt(_arrow_point(page, arrow))
    assert isinstance(hit, ExtendArrow)
    _click(qtbot, page, next(item for item in _arrows(page) if item.direction == EXTEND_STRAIGHT))
    added = page.plan().instances[-1]
    assert page._parts[added.part_id].article_number == STANDARD_STRAIGHT_ARTICLE
    assert page.canvas.selected_ids() == [added.id]


def _catalog() -> dict[int, PartSpec]:
    return {index + 1: spec for index, spec in enumerate(standard_catalog())}


def _gap(
    target: PartInstance, joint: ConnectorSpec, created: PartInstance, spec: PartSpec
) -> float:
    from slot_racing.modules.track_planner.parts import world_xy

    point = world_xy(target, joint.x_mm, joint.y_mm)
    gaps = []
    for source in spec.connectors:
        other = world_xy(created, source.x_mm, source.y_mm)
        gaps.append(math.hypot(point[0] - other[0], point[1] - other[1]))
    return min(gaps)


def _open(qtbot: QtBot, env: Env, name: str) -> PlannerPage:
    track = env.track(name, lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.grid.setChecked(False)
    return page


def _place(page: PlannerPage, article: str, *, x: float = 0.0, y: float = 0.0) -> PartInstance:
    page.library.setCurrentRow(_library_row(page, article))
    page.place_part.click()
    page.x_mm.setValue(x)
    page.y_mm.setValue(y)
    return page.plan().instances[-1]


def _select_instance(page: PlannerPage, instance_id: str) -> None:
    page.canvas.scene().clearSelection()
    for item in _instances(page):
        item.setSelected(item.item_id == instance_id)


def _arrow_point(page: PlannerPage, arrow: ExtendArrow) -> QPoint:
    origin = page.canvas.mapFromScene(arrow.scenePos())
    return origin + QPoint(round(arrow.offset.x()), round(arrow.offset.y()))


def _arrow_facing(page: PlannerPage, direction: str, heading: float) -> ExtendArrow:
    arrows = [arrow for arrow in _arrows(page) if arrow.direction == direction]
    assert arrows
    return min(arrows, key=lambda arrow: abs((arrow.heading_deg - heading + 180) % 360 - 180))


def _click(qtbot: QtBot, page: PlannerPage, arrow: ExtendArrow) -> None:
    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        page.canvas.viewport(), Qt.MouseButton.LeftButton, pos=_arrow_point(page, arrow)
    )


def _knob_gap(page: PlannerPage, item: InstanceItem) -> float:
    handle = page.canvas.rotation_handle
    origin = page.canvas.mapFromScene(handle.pos())
    knob = handle.knob_offset
    point = QPointF(origin.x() + knob.x(), origin.y() + knob.y())
    return _point_gap(point, _view_rect(page, item))


def _legacy_gap(page: PlannerPage, item: InstanceItem) -> float:
    origin = page.canvas.mapFromScene(page.canvas.rotation_handle.pos())
    return _point_gap(QPointF(origin.x(), origin.y() - 36), _view_rect(page, item))


def _view_rect(page: PlannerPage, item: InstanceItem) -> QRectF:
    rect = item.geometry_scene_rect()
    top_left = page.canvas.mapFromScene(rect.topLeft())
    bottom_right = page.canvas.mapFromScene(rect.bottomRight())
    return QRectF(QPointF(top_left), QPointF(bottom_right)).normalized()


def _point_gap(point: QPointF, rect: QRectF) -> float:
    if rect.contains(point):
        return -1
    dx = 0.0
    if point.x() < rect.left():
        dx = rect.left() - point.x()
    elif point.x() > rect.right():
        dx = point.x() - rect.right()
    dy = 0.0
    if point.y() < rect.top():
        dy = rect.top() - point.y()
    elif point.y() > rect.bottom():
        dy = point.y() - rect.bottom()
    return math.hypot(dx, dy)
