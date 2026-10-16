"""autoConnect companions and placements that meet more than one joint.

Companions come from the source attach points. They are ordinary instances.
A pose is a multi-connection only when every joint it claims actually meets.
Existing parts stay where they are.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import TrackId
from slot_racing.modules.track_planner.catalog_import import load_carrera_library
from slot_racing.modules.track_planner.document import (
    TrackPlan,
    add_instance,
    complete_auto_connections,
    empty_plan,
    place_instance,
    preview_placement,
    reposition_instance,
)
from slot_racing.modules.track_planner.inventory import analyze_inventory
from slot_racing.modules.track_planner.parts import (
    BORDER_JOINT,
    TRACK,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    Pose,
    attach_edges,
    attach_satisfied,
    build_part,
    connector_occupied,
    join_pose,
    neighbor_pose,
    origin_from_midpoint,
    snap_pose,
)
from slot_racing.modules.track_planner.ui.canvas import MM, InstanceItem
from slot_racing.modules.track_planner.ui.page import PlannerPage
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _select
from tests.modules.test_ui_management import open_page

_STRAIGHT = "4187668d-ee71-440a-9bb2-6e400e017416"
_CURVE = "cc253612-a0cb-4b17-b940-18460c337acb"


def _catalog() -> dict[int, PartSpec]:
    return {index: spec for index, spec in enumerate(load_carrera_library().parts, start=1)}


def _by_source(catalog: dict[int, PartSpec], source_id: str) -> tuple[int, PartSpec]:
    found = next(item for item in catalog.items() if item[1].source_id == source_id)
    return found


def _article(
    catalog: dict[int, PartSpec], article: str, piece_key: str = ""
) -> tuple[int, PartSpec]:
    return next(
        item
        for item in catalog.items()
        if item[1].article_number == article and item[1].piece_key == piece_key
    )


def _row_for_source(page: PlannerPage, source_id: str) -> int:
    for row in range(page.library.count()):
        item = page.library.item(row)
        if item is None:
            continue
        part_id = item.data(Qt.ItemDataRole.UserRole)
        spec = page._parts.get(part_id) if isinstance(part_id, int) else None
        if spec is not None and spec.source_id == source_id:
            return row
    raise AssertionError(source_id)


def _host(
    part_id: int, *, rotation: float = 0.0, x_mm: float = 0.0, y_mm: float = 0.0
) -> PartInstance:
    return PartInstance("host", part_id, x_mm, y_mm, rotation_z_deg=rotation)


def _plan_with(instance: PartInstance) -> TrackPlan:
    return add_instance(empty_plan(TrackId(1)), instance)


def _track_joint(spec: PartSpec, *, positive_x: bool) -> ConnectorSpec:
    joints = [connector for connector in spec.connectors if connector.kind == TRACK]
    return (
        max(joints, key=lambda connector: connector.x_mm)
        if positive_x
        else min(joints, key=lambda connector: connector.x_mm)
    )


def _seat(
    target: PartInstance,
    target_joint: ConnectorSpec,
    source_joint: ConnectorSpec,
    part_id: int,
    instance_id: str,
) -> PartInstance:
    pose = join_pose(target, target_joint, source_joint)
    return PartInstance(
        instance_id, part_id, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg
    )


def test_completing_a_straight_places_both_borders_once() -> None:
    catalog = _catalog()
    straight_id, straight = _by_source(catalog, _STRAIGHT)
    border_id, _border = _article(catalog, "20560")
    host = _host(straight_id, rotation=25.0, x_mm=40.0, y_mm=-15.0)
    plan = complete_auto_connections(_plan_with(host), catalog, ("host",))
    added = [instance for instance in plan.instances if instance.id != "host"]
    assert len(added) == 2
    assert {instance.part_id for instance in added} == {border_id}
    assert all(instance.group_id is None for instance in added)
    expected = {
        (
            round(neighbor_pose(host, attach, center_x_mm=0.0, center_y_mm=0.0).x_mm, 4),
            round(neighbor_pose(host, attach, center_x_mm=0.0, center_y_mm=0.0).y_mm, 4),
            round(neighbor_pose(host, attach, center_x_mm=0.0, center_y_mm=0.0).rotation_z_deg, 4),
        )
        for attach in straight.connectors
        if attach.kind == BORDER_JOINT and attach.has_neighbor
    }
    assert {
        (round(instance.x_mm, 4), round(instance.y_mm, 4), round(instance.rotation_z_deg, 4))
        for instance in added
    } == expected
    assert {round(instance.rotation_z_deg, 4) for instance in added} == {25.0, 205.0}
    again = complete_auto_connections(plan, catalog)
    assert again.instances == plan.instances
    for attach in straight.connectors:
        if attach.kind == BORDER_JOINT and attach.has_neighbor:
            assert attach_satisfied(
                host,
                straight,
                attach,
                tuple(
                    zip(
                        plan.instances,
                        (catalog[item.part_id] for item in plan.instances),
                        strict=True,
                    )
                ),
            )


def test_a_curve_border_uses_the_centre_correction() -> None:
    catalog = _catalog()
    curve_id, curve = _by_source(catalog, _CURVE)
    host = _host(curve_id, rotation=15.0, x_mm=12.0, y_mm=-8.0)
    plan = complete_auto_connections(_plan_with(host), catalog, ("host",))
    added = [instance for instance in plan.instances if instance.id != "host"]
    assert len(added) == 2
    articles = {catalog[instance.part_id].article_number for instance in added}
    assert articles == {"20561", "20551"}
    for instance in added:
        spec = catalog[instance.part_id]
        attach = next(
            connector
            for connector in curve.connectors
            if connector.has_neighbor and connector.target_source_id == spec.source_id
        )
        pose = neighbor_pose(
            host, attach, center_x_mm=spec.center_x_mm, center_y_mm=spec.center_y_mm
        )
        raw = origin_from_midpoint(
            attach.neighbor_x, attach.neighbor_y, attach.neighbor_angle, 0.0, 0.0
        )
        assert (instance.x_mm, instance.y_mm, instance.rotation_z_deg) == pytest.approx(
            (pose.x_mm, pose.y_mm, pose.rotation_z_deg)
        )
        assert (
            math.hypot(
                pose.x_mm - host.x_mm - _rotate_x(raw, host),
                pose.y_mm - host.y_mm - _rotate_y(raw, host),
            )
            > 100.0
        )
        assert len(attach_edges(curve, attach)) == 2
        assert attach_satisfied(
            host,
            curve,
            attach,
            tuple((item, catalog[item.part_id]) for item in plan.instances),
        )


def test_one_edge_does_not_count_as_a_connected_border_side() -> None:
    catalog = _catalog()
    straight_id, straight = _by_source(catalog, _STRAIGHT)
    _border_id, border = _article(catalog, "20560")
    host = _host(straight_id)
    seated = snap_pose(
        border, Pose(0.0, -80.0, 10.0), ((host, straight),), snap_mm=200, grid_mm=None
    )
    top = next(connector for connector in straight.connectors if connector.name == "j1")
    placed_border = PartInstance(
        "border", _border_id, seated.x_mm, seated.y_mm, rotation_z_deg=seated.rotation_z_deg
    )
    placed = ((host, straight), (placed_border, border))
    assert attach_satisfied(host, straight, top, placed)
    assert len(attach_edges(straight, top)) == 2
    shifted = replace(placed_border, x_mm=placed_border.x_mm + 172.5)
    shifted_placed = ((host, straight), (shifted, border))
    assert not attach_satisfied(host, straight, top, shifted_placed)
    met = [
        edge
        for edge in attach_edges(straight, top)
        if connector_occupied(host, edge, shifted_placed, source_id=straight.source_id)
    ]
    assert len(met) == 1


def test_a_missing_companion_adds_nothing_and_stock_marks_real_copies() -> None:
    catalog = _catalog()
    straight_id, _straight = _by_source(catalog, _STRAIGHT)
    border_id, _border = _article(catalog, "20560")
    host = _host(straight_id)
    plan = _plan_with(host)
    thinned = {part_id: spec for part_id, spec in catalog.items() if part_id != border_id}
    assert complete_auto_connections(plan, thinned, ("host",)).instances == plan.instances
    curve_id, curve = _by_source(catalog, _CURVE)
    outer = next(
        connector.target_source_id for connector in curve.connectors if connector.name == "j0"
    )
    without_outer = {part_id: spec for part_id, spec in catalog.items() if spec.source_id != outer}
    curve_host = PartInstance("curve", curve_id, 0.0, 0.0)
    curve_plan = _plan_with(curve_host)
    assert complete_auto_connections(curve_plan, without_outer).instances == curve_plan.instances
    full = complete_auto_connections(plan, catalog, ("host",))
    owned = {straight_id: 1, border_id: 1}
    report = analyze_inventory(full.instances, owned)
    borders = [instance.id for instance in full.instances if instance.part_id == border_id]
    assert report.balance(straight_id).excess_ids == ()
    assert report.balance(border_id).used == 2
    assert report.balance(border_id).excess_ids == (borders[1],)
    assert borders[0] not in report.excess_ids()
    empty = analyze_inventory(full.instances, {straight_id: 1, border_id: 0})
    assert empty.balance(border_id).excess_ids == tuple(borders)


def test_companions_are_not_completed_in_turn() -> None:
    base = build_part(
        article_number="CHAIN",
        scale="1:24",
        name="Kette",
        category="straight",
        length_mm=80,
        width_mm=40,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=2,
    )
    child_edge = ConnectorSpec("e0", 0, 0, 0, 90, BORDER_JOINT, (), edge_length_mm=50)
    child_auto = ConnectorSpec(
        "j0",
        0,
        0,
        0,
        270,
        BORDER_JOINT,
        (),
        target_source_id="G",
        exclusive=True,
        has_neighbor=True,
        neighbor_x=80,
        neighbor_y=0,
        neighbor_angle=0,
        edge_length_mm=50,
    )
    grand_edge = ConnectorSpec("e0", 0, 0, 0, 90, BORDER_JOINT, (), edge_length_mm=50)
    host_attach = ConnectorSpec(
        "j0",
        0,
        0,
        0,
        270,
        BORDER_JOINT,
        (),
        target_source_id="C",
        exclusive=True,
        has_neighbor=True,
        neighbor_x=0,
        neighbor_y=0,
        neighbor_angle=0,
        edge_length_mm=50,
    )
    catalog = {
        1: replace(base, source_id="H", connectors=(host_attach,)),
        2: replace(
            base,
            source_id="C",
            article_number="CHILD",
            name="Kind",
            connectors=(child_edge, child_auto),
        ),
        3: replace(
            base, source_id="G", article_number="GRAND", name="Enkel", connectors=(grand_edge,)
        ),
    }
    plan = complete_auto_connections(
        _plan_with(PartInstance("host", 1, 0.0, 0.0)), catalog, ("host",)
    )
    assert [instance.part_id for instance in plan.instances] == [1, 2]
    assert plan.instances[1].x_mm == pytest.approx(0.0)


def test_a_short_straight_fills_an_exact_gap_and_refuses_a_wide_one() -> None:
    catalog = _catalog()
    left_id, left_spec = _by_source(catalog, _STRAIGHT)
    filler_id, filler_spec = _article(catalog, "20611")
    left = PartInstance("left", left_id, 0.0, 0.0)
    filler_pose = join_pose(
        left, _track_joint(left_spec, positive_x=True), _track_joint(filler_spec, positive_x=False)
    )
    filler = PartInstance(
        "gap",
        filler_id,
        filler_pose.x_mm,
        filler_pose.y_mm,
        rotation_z_deg=filler_pose.rotation_z_deg,
    )
    right_pose = join_pose(
        filler,
        _track_joint(filler_spec, positive_x=True),
        _track_joint(left_spec, positive_x=False),
    )
    right = PartInstance(
        "right", left_id, right_pose.x_mm, right_pose.y_mm, rotation_z_deg=right_pose.rotation_z_deg
    )
    seated = snap_pose(
        filler_spec,
        Pose(filler.x_mm + 20.0, filler.y_mm - 8.0, 12.0),
        ((left, left_spec), (right, left_spec)),
        snap_mm=200,
        grid_mm=None,
    )
    assert (seated.x_mm, seated.y_mm, seated.rotation_z_deg) == pytest.approx(
        (filler.x_mm, filler.y_mm, filler.rotation_z_deg)
    )
    parked = PartInstance(
        "moved", filler_id, seated.x_mm, seated.y_mm, rotation_z_deg=seated.rotation_z_deg
    )
    assert connector_occupied(
        left,
        _track_joint(left_spec, positive_x=True),
        ((parked, filler_spec),),
        source_id=left_spec.source_id,
    )
    assert connector_occupied(
        right,
        _track_joint(left_spec, positive_x=False),
        ((parked, filler_spec),),
        source_id=left_spec.source_id,
    )
    widened = replace(right, x_mm=right.x_mm + 40.0)
    single = snap_pose(
        filler_spec,
        Pose(filler.x_mm + 20.0, filler.y_mm - 8.0, 12.0),
        ((left, left_spec), (widened, left_spec)),
        snap_mm=200,
        grid_mm=None,
    )
    assert widened.x_mm == right.x_mm + 40.0
    assert _matches_one_end(single, filler_spec, ((left, left_spec), (widened, left_spec)))
    alone = PartInstance(
        "alone", filler_id, single.x_mm, single.y_mm, rotation_z_deg=single.rotation_z_deg
    )
    assert _occupied_track_ends(alone, filler_spec, ((left, left_spec), (widened, left_spec))) == 1


def test_a_straight_drops_between_curves_and_between_lane_changers() -> None:
    catalog = _catalog()
    straight_id, straight = _by_source(catalog, _STRAIGHT)
    curve_id, curve = _by_source(catalog, _CURVE)
    change_id, change = _article(catalog, "20517")
    _assert_gap(straight_id, straight, curve_id, curve, _track_joint(curve, positive_x=True))
    _assert_gap(straight_id, straight, change_id, change, _track_joint(change, positive_x=True))


def test_a_digital_joint_stays_exclusive_while_plain_ends_can_meet() -> None:
    catalog = _catalog()
    straight_id, straight = _by_source(catalog, _STRAIGHT)
    left_id, left = _article(catalog, "30343", "30343.1")
    plain = next(
        connector
        for connector in left.connectors
        if connector.kind == TRACK and not connector.exclusive
    )
    exclusive = next(connector for connector in left.connectors if connector.exclusive)
    host = PartInstance("switch", left_id, 0.0, 0.0)
    seated = snap_pose(
        straight,
        Pose(host.x_mm + plain.x_mm, host.y_mm + plain.y_mm, 0.0),
        ((host, left),),
        snap_mm=200,
        grid_mm=None,
    )
    placed = PartInstance(
        "rail", straight_id, seated.x_mm, seated.y_mm, rotation_z_deg=seated.rotation_z_deg
    )
    assert connector_occupied(host, plain, ((placed, straight),), source_id=left.source_id)
    assert not connector_occupied(host, exclusive, ((placed, straight),), source_id=left.source_id)
    border_id, border = _article(catalog, "20560")
    missed = snap_pose(
        border, Pose(exclusive.x_mm, exclusive.y_mm, 0.0), ((host, left),), snap_mm=30, grid_mm=None
    )
    missed_part = PartInstance(
        "edge", border_id, missed.x_mm, missed.y_mm, rotation_z_deg=missed.rotation_z_deg
    )
    assert not connector_occupied(
        host, exclusive, ((missed_part, border),), source_id=left.source_id
    )


def test_preview_uses_the_placement_pose_and_does_not_store_companions() -> None:
    catalog = _catalog()
    straight_id, straight = _by_source(catalog, _STRAIGHT)
    plan = empty_plan(TrackId(1))
    preview = preview_placement(
        plan, straight_id, straight, 10.0, -6.0, 0.0, catalog, auto_connect=True
    )
    placed = place_instance(
        plan, straight_id, straight, 10.0, -6.0, 0.0, catalog, auto_connect=True
    )
    assert plan.instances == ()
    assert [
        (instance.part_id, instance.x_mm, instance.y_mm, instance.rotation_z_deg)
        for instance in preview
    ] == [
        (instance.part_id, instance.x_mm, instance.y_mm, instance.rotation_z_deg)
        for instance in placed.instances
    ]
    assert len(preview) == 3
    bare = preview_placement(
        plan, straight_id, straight, 10.0, -6.0, 0.0, catalog, auto_connect=False
    )
    assert len(bare) == 1
    moved = reposition_instance(placed, placed.instances[0].id, 30.0, 4.0, catalog)
    assert len(moved.instances) == len(placed.instances)


def test_the_option_is_off_and_opening_a_track_does_not_add_parts(qtbot: QtBot, env: Env) -> None:
    track = env.track("Offen", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    assert page.auto_on_place.isChecked() is False
    assert page.auto_on_place.text() == "Beim Platzieren ergänzen"
    page.library.setCurrentRow(_row_for_source(page, _STRAIGHT))
    before = tuple(page.plan().instances)
    page.auto_on_place.click()
    assert page.auto_on_place.isChecked() is True
    assert page.plan().instances == before
    page.auto_on_place.click()
    page.place_part.click()
    assert len(page.plan().instances) == 1
    page.save()
    QApplication.processEvents()
    again, reopened = open_page(qtbot, env, "track_planner")
    again.show()
    assert isinstance(reopened, PlannerPage)
    _select(reopened, track.id)
    assert len(reopened.plan().instances) == 1
    assert reopened.plan().instances[0].part_id == page.plan().instances[0].part_id
    assert reopened.auto_on_place.isChecked() is False


def test_undo_removes_the_whole_companion_group_and_one_border_can_be_deleted(
    qtbot: QtBot, env: Env
) -> None:
    track = env.track("Gruppe", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.library.setCurrentRow(_row_for_source(page, _STRAIGHT))
    page.auto_on_place.setChecked(True)
    page.place_part.click()
    assert len(page.plan().instances) == 3
    stored = tuple(
        (item.part_id, item.x_mm, item.y_mm, item.rotation_z_deg) for item in page.plan().instances
    )
    page.undo()
    assert page.plan().instances == ()
    page.redo()
    assert (
        tuple(
            (item.part_id, item.x_mm, item.y_mm, item.rotation_z_deg)
            for item in page.plan().instances
        )
        == stored
    )
    border = next(
        instance
        for instance in page.plan().instances
        if instance.part_id != page.plan().instances[0].part_id
    )
    page.canvas.scene().clearSelection()
    for item in page.canvas.scene().items():
        if isinstance(item, InstanceItem) and item.item_id == border.id:
            item.setSelected(True)
    page.delete_selected()
    assert len(page.plan().instances) == 2
    assert border.id not in {instance.id for instance in page.plan().instances}
    page.undo()
    assert len(page.plan().instances) == 3


def test_the_drop_preview_matches_the_later_placement_and_adds_nothing(
    qtbot: QtBot, env: Env
) -> None:
    track = env.track("Vorschau", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    item = page.library.item(_row_for_source(page, _STRAIGHT))
    assert item is not None
    part_id = item.data(Qt.ItemDataRole.UserRole)
    assert isinstance(part_id, int)
    spec = page._parts[part_id]
    before = tuple(page.plan().instances)
    page.canvas.show_drop_preview(part_id, 0.0, -20.0)
    assert page.plan().instances == before
    assert len(page.canvas._ghosts) == 1
    page.canvas.set_auto_connect(True)
    page.canvas.show_drop_preview(part_id, 0.0, -20.0)
    assert page.plan().instances == before
    expected = preview_placement(
        page.plan(), part_id, spec, 0.0, -20.0, 0.0, page._parts, auto_connect=True
    )
    assert len(page.canvas._ghosts) == len(expected) == 3
    for ghost, instance in zip(page.canvas._ghosts, expected, strict=True):
        assert ghost.pos().x() / MM == pytest.approx(instance.x_mm)
        assert ghost.pos().y() / MM == pytest.approx(instance.y_mm)
        assert ghost.rotation() == pytest.approx(instance.rotation_z_deg)
    page.canvas.clear_drop_preview()
    assert page.canvas._ghosts == []
    assert page.plan().instances == before


def _assert_gap(
    straight_id: int,
    straight: PartSpec,
    other_id: int,
    other: PartSpec,
    other_joint: ConnectorSpec,
) -> None:
    host = PartInstance("straight", straight_id, 0.0, 0.0)
    left = _seat(host, _track_joint(straight, positive_x=False), other_joint, other_id, "left")
    right = _seat(host, _track_joint(straight, positive_x=True), other_joint, other_id, "right")
    seated = snap_pose(
        straight,
        Pose(12.0, -9.0, 20.0),
        ((left, other), (right, other)),
        snap_mm=200,
        grid_mm=None,
    )
    assert (seated.x_mm, seated.y_mm, seated.rotation_z_deg) == pytest.approx((0.0, 0.0, 0.0))
    parked = PartInstance(
        "back", straight_id, seated.x_mm, seated.y_mm, rotation_z_deg=seated.rotation_z_deg
    )
    assert connector_occupied(left, other_joint, ((parked, straight),), source_id=other.source_id)
    assert connector_occupied(right, other_joint, ((parked, straight),), source_id=other.source_id)
    shifted = replace(right, x_mm=right.x_mm + 30.0)
    single = snap_pose(
        straight,
        Pose(12.0, -9.0, 20.0),
        ((left, other), (shifted, other)),
        snap_mm=200,
        grid_mm=None,
    )
    assert shifted.x_mm == right.x_mm + 30.0
    assert _matches_one_end(single, straight, ((left, other), (shifted, other)))
    alone = PartInstance(
        "one", straight_id, single.x_mm, single.y_mm, rotation_z_deg=single.rotation_z_deg
    )
    assert _occupied_track_ends(alone, straight, ((left, other), (shifted, other))) == 1


def _matches_one_end(
    pose: Pose,
    spec: PartSpec,
    hosts: tuple[tuple[PartInstance, PartSpec], ...],
) -> bool:
    """The pose is one rigid joint seat, not a point stretched between the hosts."""
    for source in spec.connectors:
        if source.kind != TRACK:
            continue
        for host, host_spec in hosts:
            for target in host_spec.connectors:
                if target.kind != TRACK:
                    continue
                seated = join_pose(host, target, source)
                if (
                    math.hypot(seated.x_mm - pose.x_mm, seated.y_mm - pose.y_mm) < 1e-6
                    and abs((seated.rotation_z_deg - pose.rotation_z_deg + 180.0) % 360.0 - 180.0)
                    < 1e-6
                ):
                    return True
    return False


def _occupied_track_ends(
    moving: PartInstance,
    spec: PartSpec,
    hosts: tuple[tuple[PartInstance, PartSpec], ...],
) -> int:
    count = 0
    for host, host_spec in hosts:
        for target in host_spec.connectors:
            if target.kind == TRACK and connector_occupied(
                host, target, ((moving, spec),), source_id=host_spec.source_id
            ):
                count += 1
    return count


def _rotate_x(point: tuple[float, float], host: PartInstance) -> float:
    turn = math.radians(host.rotation_z_deg)
    return point[0] * math.cos(turn) - point[1] * math.sin(turn)


def _rotate_y(point: tuple[float, float], host: PartInstance) -> float:
    turn = math.radians(host.rotation_z_deg)
    return point[0] * math.sin(turn) + point[1] * math.cos(turn)
