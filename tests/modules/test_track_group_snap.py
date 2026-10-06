"""A selection docks as one rigid body, and only through joints that are actually free."""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import combinations

import pytest
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import TrackId
from slot_racing.modules.track_planner.document import (
    TrackPlan,
    empty_plan,
    extend_from_connector,
    place_instance,
    reposition_instance,
    reposition_selection,
    set_plan_grid,
    toggle_group,
)
from slot_racing.modules.track_planner.parts import (
    EXTEND_RIGHT,
    EXTEND_STRAIGHT,
    STANDARD_CURVE_ARTICLE,
    STANDARD_STRAIGHT_ARTICLE,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    Pose,
    free_connector_indexes,
    rotate_xy,
    signed_delta_deg,
    snap_pose,
    standard_catalog,
    world_xy,
)
from slot_racing.modules.track_planner.ui.page import PlannerPage
from tests.modules.conftest import Env
from tests.modules.test_track_extend_arrows import _open, _place
from tests.modules.test_track_parts import _select
from tests.modules.test_track_planner_extend import _arrows
from tests.modules.test_track_planner_ui import _instances
from tests.modules.test_ui_management import open_page


def test_a_new_part_ignores_an_occupied_joint() -> None:
    plan, catalog = _straights(2)
    point = _meetings(plan.instances, catalog)[0][0]
    short = catalog[3]
    proposed = _pose_near_point(short, point, 12)
    pose = snap_pose(
        short,
        proposed,
        _pairs(plan.instances, catalog),
        snap_mm=25,
        grid_mm=None,
    )
    assert (pose.x_mm, pose.y_mm, pose.rotation_z_deg) == pytest.approx(
        (proposed.x_mm, proposed.y_mm, proposed.rotation_z_deg)
    )


def test_one_free_part_still_docks_on_a_free_joint() -> None:
    catalog = _catalog()
    straight = catalog[1]
    plan = place_instance(_open_plan(), 1, straight, 0, 0, 0, catalog)
    plan = place_instance(plan, 1, straight, 800, 0, 0, catalog)
    moving = plan.instances[1]
    target = plan.instances[0]
    docked = snap_pose(straight, Pose(355, 3, 0), ((target, straight),), snap_mm=25, grid_mm=None)
    updated = reposition_instance(plan, moving.id, 355, 3, catalog)
    seated = _by_id(updated, moving.id)
    assert (seated.x_mm, seated.y_mm, seated.rotation_z_deg) == pytest.approx(
        (docked.x_mm, docked.y_mm, docked.rotation_z_deg)
    )
    assert seated.x_mm == pytest.approx(straight.length_mm or 0.0)
    _assert_joined(_joint_pairs((target, seated), catalog), (target, seated), catalog)


def test_a_connected_chain_snaps_and_turns_as_one_body() -> None:
    plan, catalog = _straights(3)
    plan, target = _add_straight(plan, catalog, 4000, 0, 90)
    chain = plan.instances[:-1]
    before = list(chain)
    pairs = _joint_pairs(before, catalog)
    assert len(pairs) == 2
    updated = reposition_selection(plan, _approach(before, catalog, target), before[0].id, catalog)
    after = [_by_id(updated, item.id) for item in before]
    assert abs(_assert_rigid(before, after)) == pytest.approx(90)
    _assert_joined(pairs, after, catalog)
    assert _docked(after, catalog, _by_id(updated, target.id))
    assert (_by_id(updated, target.id).rotation_z_deg) == pytest.approx(90)


def test_a_branch_keeps_every_joint_when_the_group_turns() -> None:
    plan, catalog = _straights(2)
    plan = extend_from_connector(plan, plan.instances[-1].id, 1, EXTEND_RIGHT, catalog)
    plan, target = _add_straight(plan, catalog, -2500, 600, 90)
    chosen = plan.instances[:-1]
    before = list(chosen)
    pairs = _joint_pairs(before, catalog)
    assert len(pairs) == 2
    updated = reposition_selection(plan, _approach(before, catalog, target), before[0].id, catalog)
    after = [_by_id(updated, item.id) for item in before]
    _assert_rigid(before, after)
    _assert_joined(pairs, after, catalog)
    assert _docked(after, catalog, _by_id(updated, target.id))


def test_internal_joints_are_not_snap_sources() -> None:
    plan, catalog = _straights(3)
    point = _meetings(plan.instances, catalog)[0][0]
    bait = _pose_near_point(catalog[3], point, 12)
    plan = place_instance(plan, 3, catalog[3], bait.x_mm, bait.y_mm, bait.rotation_z_deg, catalog)
    seated = plan.instances[-1]
    landed = world_xy(seated, catalog[3].connectors[0].x_mm, catalog[3].connectors[0].y_mm)
    assert math.hypot(landed[0] - point[0], landed[1] - point[1]) == pytest.approx(12)
    chain = [item for item in plan.instances if item.part_id == 1]
    updated = reposition_selection(
        plan,
        {item.id: (item.x_mm, item.y_mm) for item in chain},
        chain[0].id,
        catalog,
    )
    for old in chain:
        item = _by_id(updated, old.id)
        assert (item.x_mm, item.y_mm, item.rotation_z_deg) == pytest.approx(
            (old.x_mm, old.y_mm, old.rotation_z_deg)
        )


def test_a_partial_selection_does_not_rewire_until_the_joint_is_free() -> None:
    plan, catalog = _straights(4)
    selected = plan.instances[1:3]
    anchor = world_xy(selected[0], catalog[1].connectors[0].x_mm, catalog[1].connectors[0].y_mm)
    bait = _pose_near_point(catalog[1], (anchor[0], anchor[1] + 400), 12)
    plan = place_instance(plan, 1, catalog[1], bait.x_mm, bait.y_mm, 0, catalog)
    proposed = {item.id: (item.x_mm, item.y_mm + 400) for item in selected}
    updated = reposition_selection(plan, proposed, selected[0].id, catalog)
    for item in selected:
        moved = _by_id(updated, item.id)
        assert (moved.x_mm, moved.y_mm, moved.rotation_z_deg) == pytest.approx(
            (item.x_mm, item.y_mm + 400, item.rotation_z_deg)
        )
    body = [_by_id(updated, item.id) for item in selected]
    pairs = _joint_pairs(body, catalog)
    snapped = reposition_selection(
        updated,
        {item.id: (item.x_mm, item.y_mm) for item in body},
        body[0].id,
        catalog,
    )
    after = [_by_id(snapped, item.id) for item in body]
    _assert_rigid(body, after)
    _assert_joined(pairs, after, catalog)
    assert _docked(after, catalog, snapped.instances[-1])


def test_without_a_free_joint_the_grid_shifts_the_whole_selection() -> None:
    plan, catalog = _straights(3)
    plan = set_plan_grid(plan, enabled=True, grid_mm=10, snap_mm=25)
    chain = list(plan.instances)
    pairs = _joint_pairs(chain, catalog)
    proposed = {item.id: (item.x_mm + 13, item.y_mm + 7) for item in chain}
    updated = reposition_selection(plan, proposed, chain[0].id, catalog)
    after = [_by_id(updated, item.id) for item in chain]
    for old, item in zip(chain, after, strict=True):
        assert (item.x_mm, item.y_mm, item.rotation_z_deg) == pytest.approx(
            (old.x_mm + 10, old.y_mm + 10, old.rotation_z_deg)
        )
    _assert_joined(pairs, after, catalog)


def test_a_closed_loop_has_no_free_joint_to_snap() -> None:
    plan, catalog = _curve_loop()
    placed = _pairs(plan.instances, catalog)
    assert all(
        free_connector_indexes(item, catalog[item.part_id], placed) == () for item in plan.instances
    )
    joint = catalog[2].connectors[0]
    point = world_xy(plan.instances[0], joint.x_mm, joint.y_mm)
    bait = _pose_near_point(catalog[1], point, 10)
    plan = place_instance(plan, 1, catalog[1], bait.x_mm, bait.y_mm, 0, catalog)
    loop = plan.instances[:-1]
    updated = reposition_selection(
        plan,
        {item.id: (item.x_mm, item.y_mm) for item in loop},
        loop[0].id,
        catalog,
    )
    for old in loop:
        item = _by_id(updated, old.id)
        assert (item.x_mm, item.y_mm, item.rotation_z_deg) == pytest.approx(
            (old.x_mm, old.y_mm, old.rotation_z_deg)
        )


def test_unconnected_parts_in_one_selection_rotate_together() -> None:
    catalog = _catalog()
    straight = catalog[1]
    plan = place_instance(_open_plan(), 1, straight, 0, 0, 0, catalog)
    plan = place_instance(plan, 1, straight, 700, 40, 0, catalog)
    plan, target = _add_straight(plan, catalog, 0, 2000, 90)
    chosen = plan.instances[:2]
    before = list(chosen)
    updated = reposition_selection(plan, _approach(before, catalog, target), before[0].id, catalog)
    after = [_by_id(updated, item.id) for item in before]
    assert _assert_rigid(before, after) != pytest.approx(0)
    assert _docked(after, catalog, _by_id(updated, target.id))


def test_grouping_does_not_change_the_snap_and_keeps_the_group() -> None:
    plan, catalog = _straights(3)
    plan, target = _add_straight(plan, catalog, 4000, 0, 90)
    chain = plan.instances[:-1]
    proposed = _approach(chain, catalog, target)
    plain = reposition_selection(plan, proposed, chain[0].id, catalog)
    grouped = reposition_selection(
        toggle_group(plan, [item.id for item in chain]),
        proposed,
        chain[0].id,
        catalog,
    )
    group_id = _by_id(grouped, chain[0].id).group_id
    assert group_id is not None
    for old in chain:
        left = _by_id(plain, old.id)
        right = _by_id(grouped, old.id)
        assert (right.x_mm, right.y_mm, right.rotation_z_deg) == pytest.approx(
            (left.x_mm, left.y_mm, left.rotation_z_deg)
        )
        assert right.group_id == group_id


def test_a_selection_does_not_dock_into_the_middle_of_a_track() -> None:
    plan, catalog = _straights(2)
    point = _meetings(plan.instances, catalog)[0][0]
    short = catalog[3]
    proposed = _pose_near_point(short, point, 12)
    plan = place_instance(plan, 3, short, 4000, 4000, 0, catalog)
    moving = plan.instances[-1]
    updated = reposition_instance(plan, moving.id, proposed.x_mm, proposed.y_mm, catalog)
    seated = _by_id(updated, moving.id)
    assert (seated.x_mm, seated.y_mm, seated.rotation_z_deg) == pytest.approx(
        (proposed.x_mm, proposed.y_mm, 0)
    )


def test_continue_arrows_use_the_same_free_joints(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Pfeile")
    _place(page, STANDARD_STRAIGHT_ARTICLE)
    current = page.plan().instances[-1].id
    for _ in range(2):
        page._extended(current, 1, EXTEND_STRAIGHT)
        current = page.plan().instances[-1].id
    end = page.plan().instances[-1]
    placed = [(item, page._parts[item.part_id]) for item in page.plan().instances]
    free = free_connector_indexes(end, page._parts[end.part_id], placed)
    assert free == (1,)
    assert {arrow.connector_index for arrow in _arrows(page)} == set(free)
    middle = page.plan().instances[1]
    _select_one(page, middle.id)
    assert _arrows(page) == []
    assert free_connector_indexes(middle, page._parts[middle.part_id], placed) == ()


def test_the_page_docks_a_dragged_selection_as_one_body(qtbot: QtBot, env: Env) -> None:
    track = env.track("Snap", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    page.grid.setChecked(False)
    _place(page, STANDARD_STRAIGHT_ARTICLE)
    current = page.plan().instances[-1].id
    page._extended(current, 1, EXTEND_STRAIGHT)
    page._extended(page.plan().instances[-1].id, 1, EXTEND_STRAIGHT)
    _place(page, STANDARD_STRAIGHT_ARTICLE, x=4000, y=0)
    page.rotation_free.setValue(90)
    chain = page.plan().instances[:3]
    target = page.plan().instances[3]
    before = list(chain)
    pairs = _joint_pairs(before, page._parts)
    proposed = _approach(before, page._parts, target)
    page._moved_group(before[0].id, [(item_id, xy[0], xy[1]) for item_id, xy in proposed.items()])
    after = [_by_id(page.plan(), item.id) for item in before]
    _assert_rigid(before, after)
    _assert_joined(pairs, after, page._parts)
    assert _docked(after, page._parts, _by_id(page.plan(), target.id))
    assert len(_instances(page)) == 4


def _catalog() -> dict[int, PartSpec]:
    by_article = {spec.article_number: spec for spec in standard_catalog()}
    return {
        1: by_article[STANDARD_STRAIGHT_ARTICLE],
        2: by_article[STANDARD_CURVE_ARTICLE],
        3: by_article["20020611"],
    }


def _open_plan() -> TrackPlan:
    return set_plan_grid(empty_plan(TrackId(1)), enabled=False, grid_mm=10, snap_mm=25)


def _straights(count: int) -> tuple[TrackPlan, dict[int, PartSpec]]:
    catalog = _catalog()
    plan = place_instance(_open_plan(), 1, catalog[1], 0, 0, 0, catalog)
    current = plan.instances[-1].id
    for _ in range(count - 1):
        plan = extend_from_connector(plan, current, 1, EXTEND_STRAIGHT, catalog)
        current = plan.instances[-1].id
    return plan, catalog


def _curve_loop() -> tuple[TrackPlan, dict[int, PartSpec]]:
    catalog = _catalog()
    curve = catalog[2]
    plan = place_instance(_open_plan(), 2, curve, 0, 0, 0, catalog)
    current = plan.instances[-1].id
    for _ in range(5):
        end = _by_id(plan, current)
        free = free_connector_indexes(end, curve, _pairs(plan.instances, catalog))
        assert free
        plan = extend_from_connector(plan, current, free[-1], EXTEND_RIGHT, catalog)
        current = plan.instances[-1].id
    return plan, catalog


def _add_straight(
    plan: TrackPlan, catalog: dict[int, PartSpec], x_mm: float, y_mm: float, rotation: float
) -> tuple[TrackPlan, PartInstance]:
    updated = place_instance(plan, 1, catalog[1], x_mm, y_mm, rotation, catalog)
    return updated, updated.instances[-1]


def _pairs(
    instances: Sequence[PartInstance], catalog: dict[int, PartSpec]
) -> tuple[tuple[PartInstance, PartSpec], ...]:
    return tuple((item, catalog[item.part_id]) for item in instances)


def _by_id(plan: TrackPlan, instance_id: str) -> PartInstance:
    return next(item for item in plan.instances if item.id == instance_id)


def _meetings(
    instances: Sequence[PartInstance], catalog: dict[int, PartSpec]
) -> list[tuple[tuple[float, float], str, str, str, str]]:
    return [
        (world_xy(left, source.x_mm, source.y_mm), left.id, source.name, right.id, target.name)
        for left, right in combinations(instances, 2)
        for source in catalog[left.part_id].connectors
        for target in catalog[right.part_id].connectors
        if math.hypot(
            *(
                world_xy(left, source.x_mm, source.y_mm)[index]
                - world_xy(right, target.x_mm, target.y_mm)[index]
                for index in (0, 1)
            )
        )
        <= 1.0
    ]


def _joint_pairs(
    instances: Sequence[PartInstance], catalog: dict[int, PartSpec]
) -> list[tuple[str, str, str, str]]:
    return [
        (left_id, source, right_id, target)
        for _point, left_id, source, right_id, target in _meetings(instances, catalog)
    ]


def _assert_joined(
    pairs: Sequence[tuple[str, str, str, str]],
    instances: Sequence[PartInstance],
    catalog: dict[int, PartSpec],
) -> None:
    assert pairs
    by_id = {item.id: item for item in instances}
    for left_id, source_name, right_id, target_name in pairs:
        left = by_id[left_id]
        right = by_id[right_id]
        source = _named(catalog[left.part_id], source_name)
        target = _named(catalog[right.part_id], target_name)
        start = world_xy(left, source.x_mm, source.y_mm)
        other = world_xy(right, target.x_mm, target.y_mm)
        assert math.hypot(start[0] - other[0], start[1] - other[1]) == pytest.approx(0, abs=1e-6)


def _assert_rigid(before: list[PartInstance], after: list[PartInstance]) -> float:
    deltas = [
        signed_delta_deg(old.rotation_z_deg, new.rotation_z_deg)
        for old, new in zip(before, after, strict=True)
    ]
    assert deltas == pytest.approx([deltas[0]] * len(deltas))
    delta = deltas[0]
    for left, right in combinations(range(len(before)), 2):
        old = (before[right].x_mm - before[left].x_mm, before[right].y_mm - before[left].y_mm)
        turned = rotate_xy(old[0], old[1], delta)
        new = (after[right].x_mm - after[left].x_mm, after[right].y_mm - after[left].y_mm)
        assert new == pytest.approx(turned)
    return delta


def _approach(
    moving: Sequence[PartInstance], catalog: dict[int, PartSpec], target: PartInstance
) -> dict[str, tuple[float, float]]:
    spec = catalog[target.part_id]
    placed = (*_pairs(moving, catalog), (target, spec))
    source_item, source = _one_free(moving, catalog, placed)
    target_item, joint = _one_free((target,), catalog, placed)
    start = world_xy(source_item, source.x_mm, source.y_mm)
    goal = world_xy(target_item, joint.x_mm, joint.y_mm)
    dx = goal[0] - start[0]
    dy = goal[1] + 12.0 - start[1]
    return {item.id: (item.x_mm + dx, item.y_mm + dy) for item in moving}


def _one_free(
    instances: Sequence[PartInstance],
    catalog: dict[int, PartSpec],
    placed: Sequence[tuple[PartInstance, PartSpec]],
) -> tuple[PartInstance, ConnectorSpec]:
    for item in instances:
        spec = catalog[item.part_id]
        indexes = free_connector_indexes(item, spec, placed)
        if indexes:
            return item, spec.connectors[indexes[0]]
    raise AssertionError("expected a free joint")


def _docked(
    moving: Sequence[PartInstance], catalog: dict[int, PartSpec], target: PartInstance
) -> bool:
    spec = catalog[target.part_id]
    for item in moving:
        for source in catalog[item.part_id].connectors:
            start = world_xy(item, source.x_mm, source.y_mm)
            for joint in spec.connectors:
                other = world_xy(target, joint.x_mm, joint.y_mm)
                if math.hypot(start[0] - other[0], start[1] - other[1]) <= 1e-4:
                    return True
    return False


def _named(spec: PartSpec, name: str) -> ConnectorSpec:
    return next(item for item in spec.connectors if item.name == name)


def _pose_near_point(spec: PartSpec, point: tuple[float, float], gap: float) -> Pose:
    joint = spec.connectors[0]
    return Pose(point[0] - joint.x_mm, point[1] + gap - joint.y_mm, 0.0)


def _select_one(page: PlannerPage, instance_id: str) -> None:
    page.canvas.scene().clearSelection()
    for item in _instances(page):
        item.setSelected(item.item_id == instance_id)
