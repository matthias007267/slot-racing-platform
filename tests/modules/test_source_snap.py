"""Centre correction and edge snapping against the Carrera source files.

Attach points and template placeholders name a part's centre. The plan stores
the geometric origin. Border pieces that have connectors and no attach points
join on those edges. Exclusive digital joints still pair by source id.
"""

from __future__ import annotations

import math

import pytest
from sqlalchemy import select

from slot_racing.core.domain import TrackId
from slot_racing.modules.track_planner.catalog_import import TemplateDraft, load_carrera_library
from slot_racing.modules.track_planner.document import empty_plan, place_instance, place_template
from slot_racing.modules.track_planner.models import (
    TrackPartConnector,
    TrackPartDefinition,
    TrackPartTemplate,
    TrackPartTemplateMember,
)
from slot_racing.modules.track_planner.parts import (
    BORDER_JOINT,
    DEFAULT_SNAP_MM,
    EDGE_LENGTH_TOLERANCE_MM,
    EXTEND_LEFT,
    EXTEND_RIGHT,
    EXTEND_STRAIGHT,
    TRACK,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    Pose,
    connector_occupied,
    connectors_compatible,
    extend_pose,
    join_pose,
    neighbor_pose,
    origin_from_midpoint,
    placed_continuation_delta_deg,
    snap_pose,
)
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner

_CURVE = "cc253612-a0cb-4b17-b940-18460c337acb"
_TEMPLATES = (
    "30343",
    "30345",
    "30347",
    "30350",
    "30351",
    "30356",
    "30362",
    "30363",
    "30364",
    "30365",
)


def _oracle(
    mid_x: float,
    mid_y: float,
    angle_deg: float,
    center_x: float,
    center_y: float,
) -> tuple[float, float]:
    """Softyroyal's centre-to-origin step. Degrees in, the same rotation as the plan."""
    turn = math.radians(angle_deg)
    rotated_x = center_x * math.cos(turn) - center_y * math.sin(turn)
    rotated_y = center_x * math.sin(turn) + center_y * math.cos(turn)
    return (mid_x - rotated_x, mid_y - rotated_y)


def _parts() -> dict[str, PartSpec]:
    return {spec.source_id: spec for spec in load_carrera_library().parts}


def _article(article: str, piece_key: str = "") -> PartSpec:
    return next(
        spec
        for spec in load_carrera_library().parts
        if spec.article_number == article and spec.piece_key == piece_key
    )


def _straight() -> PartSpec:
    return _article("20509")


def _curve() -> PartSpec:
    return _parts()[_CURVE]


def test_origin_from_midpoint_matches_the_source_formula() -> None:
    assert origin_from_midpoint(12.0, -4.0, 35.0, 0.0, 0.0) == (12.0, -4.0)
    curve = _curve()
    assert curve.center_x_mm == pytest.approx(0.0, abs=1e-9)
    assert curve.center_y_mm == pytest.approx(-297.0)
    joint = next(
        connector
        for connector in curve.connectors
        if connector.kind == TRACK and connector.neighbor_angle == pytest.approx(60.0)
    )
    expected = _oracle(
        joint.neighbor_x, joint.neighbor_y, joint.neighbor_angle, 0.0, curve.center_y_mm
    )
    assert expected == pytest.approx((0.0, 0.0), abs=1e-9)
    assert origin_from_midpoint(
        joint.neighbor_x, joint.neighbor_y, joint.neighbor_angle, 0.0, curve.center_y_mm
    ) == pytest.approx(expected)


def test_a_curve_neighbour_uses_the_origin_and_a_straight_does_not_move() -> None:
    host = PartInstance("host", 1, 0.0, 0.0)
    curve = _curve()
    straight = _straight()
    for connector in curve.connectors:
        if connector.kind != TRACK or not connector.has_neighbor:
            continue
        pose = neighbor_pose(
            host, connector, center_x_mm=curve.center_x_mm, center_y_mm=curve.center_y_mm
        )
        assert pose.x_mm == pytest.approx(0.0, abs=1e-6)
        assert pose.y_mm == pytest.approx(0.0, abs=1e-6)
        assert pose.rotation_z_deg == pytest.approx(connector.neighbor_angle)
        raw = neighbor_pose(host, connector)
        assert math.hypot(raw.x_mm - pose.x_mm, raw.y_mm - pose.y_mm) == pytest.approx(297.0)
    rails = [connector for connector in straight.connectors if connector.kind == TRACK]
    assert {round(connector.neighbor_x, 1) for connector in rails} == {-345.0, 345.0}
    for connector in rails:
        pose = neighbor_pose(host, connector)
        assert pose.x_mm == pytest.approx(connector.neighbor_x)
        assert pose.y_mm == pytest.approx(connector.neighbor_y)


def test_a_second_curve_snaps_to_the_corrected_origin() -> None:
    curve = _curve()
    host = PartInstance("host", 1, 0.0, 0.0)
    seated = snap_pose(curve, Pose(12.0, -6.0, 55.0), ((host, curve),), snap_mm=200, grid_mm=None)
    assert seated.x_mm == pytest.approx(0.0)
    assert seated.y_mm == pytest.approx(0.0)
    assert seated.rotation_z_deg == pytest.approx(60.0)
    other = snap_pose(curve, Pose(-8.0, 5.0, 310.0), ((host, curve),), snap_mm=200, grid_mm=None)
    assert (other.x_mm, other.y_mm, other.rotation_z_deg) == pytest.approx((0.0, 0.0, 300.0))
    # The old reading of the attach point is 297 mm away and is not a joint.
    missed = snap_pose(
        curve, Pose(250.0, -140.0, 60.0), ((host, curve),), snap_mm=200, grid_mm=None
    )
    assert missed.x_mm == pytest.approx(250.0)
    assert missed.y_mm == pytest.approx(-140.0)


def test_rail_extend_and_an_eighty_millimetre_dock_stay() -> None:
    straight = _straight()
    curve = _curve()
    host = PartInstance("host", 1, 0.0, 0.0)
    rail = next(
        connector
        for connector in straight.connectors
        if connector.kind == TRACK and connector.x_mm > 0
    )
    left = extend_pose(host, rail, curve, EXTEND_LEFT)
    right = extend_pose(host, rail, curve, EXTEND_RIGHT)
    forward = extend_pose(host, rail, straight, EXTEND_STRAIGHT)
    assert left is not None and right is not None and forward is not None
    assert _turn(host, rail, left, curve) == pytest.approx(-60.0, abs=0.05)
    assert _turn(host, rail, right, curve) == pytest.approx(60.0, abs=0.05)
    assert _turn(host, rail, forward, straight) == pytest.approx(0.0, abs=0.05)
    assert DEFAULT_SNAP_MM == 200.0
    # 80 mm short of the rail is inside 200 mm and outside Softyroyal's 50 mm.
    # Shrinking the radius would refuse a dock that already lands on the joint.
    wide = snap_pose(
        straight, Pose(265.0, 0.0, 0.0), ((host, straight),), snap_mm=200, grid_mm=None
    )
    tight = snap_pose(
        straight, Pose(265.0, 0.0, 0.0), ((host, straight),), snap_mm=50, grid_mm=None
    )
    assert wide.x_mm == pytest.approx(345.0)
    assert tight.x_mm == pytest.approx(265.0)
    close = snap_pose(
        straight, Pose(330.0, 4.0, 0.0), ((host, straight),), snap_mm=30, grid_mm=None
    )
    assert (close.x_mm, close.y_mm) == pytest.approx((345.0, 0.0))


def test_template_members_are_placed_from_their_centres() -> None:
    library = load_carrera_library()
    specs = {spec.source_id: spec for spec in library.parts}
    for template in library.templates:
        if template.article_number not in _TEMPLATES:
            continue
        ids = {member.source_id: index for index, member in enumerate(template.members, start=1)}
        members = tuple(
            (ids[member.source_id], member.x_mm, member.y_mm, member.angle_deg)
            for member in template.members
        )
        centers = {
            ids[member.source_id]: (
                specs[member.source_id].center_x_mm,
                specs[member.source_id].center_y_mm,
            )
            for member in template.members
        }
        placed = place_template(empty_plan(TrackId(1)), members, 0.0, 0.0, 0.0, centers)
        turned = place_template(empty_plan(TrackId(1)), members, 10.0, -5.0, 90.0, centers)
        for member, instance, rotated in zip(
            template.members, placed.instances, turned.instances, strict=True
        ):
            spec = specs[member.source_id]
            origin = _oracle(
                member.x_mm, member.y_mm, member.angle_deg, spec.center_x_mm, spec.center_y_mm
            )
            assert (instance.x_mm, instance.y_mm) == pytest.approx(origin)
            assert instance.rotation_z_deg == pytest.approx(member.angle_deg % 360.0)
            world = _rotate(origin[0], origin[1], 90.0)
            assert (rotated.x_mm, rotated.y_mm) == pytest.approx((10.0 + world[0], -5.0 + world[1]))
            assert rotated.rotation_z_deg == pytest.approx((90.0 + member.angle_deg) % 360.0)
        if template.article_number in {"30343", "30345", "30347", "30356"}:
            for member, instance in zip(template.members, placed.instances, strict=True):
                assert (instance.x_mm, instance.y_mm) == pytest.approx((member.x_mm, member.y_mm))
        if template.article_number in {"30362", "30363"}:
            shifted = next(
                instance
                for member, instance in zip(template.members, placed.instances, strict=True)
                if specs[member.source_id].center_y_mm == pytest.approx(-297.0)
            )
            assert (shifted.x_mm, shifted.y_mm) == pytest.approx((0.0, -297.0), abs=1e-3)
        if template.article_number in {"30364", "30365"}:
            shifted = next(
                instance
                for member, instance in zip(template.members, placed.instances, strict=True)
                if specs[member.source_id].center_y_mm == pytest.approx(-297.0)
            )
            assert (shifted.x_mm, shifted.y_mm) == pytest.approx((0.0, 297.0), abs=1e-3)
        if template.article_number == "30350":
            _assert_bottleneck(template, placed.instances, specs, 49.5)
        if template.article_number == "30351":
            _assert_bottleneck(template, placed.instances, specs, -49.5)


def test_border_edges_snap_and_do_not_join_a_rail() -> None:
    straight = _straight()
    border = _article("20560")
    host = PartInstance("host", 1, 0.0, 0.0)
    assert border.center_x_mm == pytest.approx(0.0)
    assert border.center_y_mm == pytest.approx(0.0)
    lengths = sorted(
        round(connector.edge_length_mm, 3)
        for connector in border.connectors
        if connector.edge_length_mm is not None
    )
    assert lengths == [99.0, 99.0, 172.5, 172.5]
    assert all(connector.kind == BORDER_JOINT for connector in border.connectors)
    assert not any(connector.has_neighbor for connector in border.connectors)
    named = [
        neighbor_pose(
            host, connector, center_x_mm=border.center_x_mm, center_y_mm=border.center_y_mm
        )
        for connector in straight.connectors
        if connector.kind == BORDER_JOINT and connector.has_neighbor
    ]
    assert {
        (round(pose.x_mm, 3), round(pose.y_mm, 3), round(pose.rotation_z_deg, 3)) for pose in named
    } == {
        (0.0, -148.5, 0.0),
        (0.0, 148.5, 180.0),
    }
    poses = {
        (
            round(join_pose(host, target, source).x_mm, 3),
            round(join_pose(host, target, source).y_mm, 3),
            round(join_pose(host, target, source).rotation_z_deg, 3),
        )
        for source in border.connectors
        for target in straight.connectors
        if connectors_compatible(
            source, target, left_source=border.source_id, right_source=straight.source_id
        )
    }
    assert (0.0, -148.5, 0.0) in poses
    assert (0.0, 148.5, 180.0) in poses
    side = next(connector for connector in straight.connectors if connector.edge_length_mm == 172.5)
    assert not connectors_compatible(
        side, side, left_source=straight.source_id, right_source=straight.source_id
    )
    rail = next(connector for connector in straight.connectors if connector.kind == TRACK)
    end = next(
        connector
        for connector in border.connectors
        if connector.edge_length_mm == pytest.approx(99.0)
    )
    assert not connectors_compatible(
        end, rail, left_source=border.source_id, right_source=straight.source_id
    )
    cap = _article("20598", "20598.1")
    assert not any(
        connectors_compatible(
            edge, side, left_source=cap.source_id, right_source=straight.source_id
        )
        for edge in cap.connectors
    )
    assert any(
        connectors_compatible(
            edge, border_edge, left_source=cap.source_id, right_source=border.source_id
        )
        for edge in cap.connectors
        for border_edge in border.connectors
        if edge.edge_length_mm is not None
        and border_edge.edge_length_mm is not None
        and abs(edge.edge_length_mm - border_edge.edge_length_mm) <= EDGE_LENGTH_TOLERANCE_MM
    )
    bare = [
        spec
        for spec in load_carrera_library().parts
        if spec.connectors and not any(connector.has_neighbor for connector in spec.connectors)
    ]
    assert len(bare) == 21
    curve = _curve()
    outer = next(
        spec
        for spec in load_carrera_library().parts
        if spec.source_id
        == next(
            connector.target_source_id
            for connector in curve.connectors
            if connector.has_neighbor
            and connector.target_source_id
            and connector.kind == BORDER_JOINT
            and connector.direction_deg == pytest.approx(270.0)
        )
    )
    attach = next(
        connector
        for connector in curve.connectors
        if connector.target_source_id == outer.source_id and connector.has_neighbor
    )
    pose = neighbor_pose(host, attach, center_x_mm=outer.center_x_mm, center_y_mm=outer.center_y_mm)
    assert pose.x_mm == pytest.approx(0.0, abs=1e-9)
    assert pose.y_mm == pytest.approx(0.0, abs=1e-9)
    assert math.hypot(attach.neighbor_x, attach.neighbor_y) == pytest.approx(445.5)


def test_digital_joints_keep_the_source_pairing() -> None:
    halves = [spec for spec in load_carrera_library().parts if spec.article_number == "30345"]
    left = next(
        spec
        for spec in halves
        if any("C_Dig_right" in connector.edge_types for connector in spec.connectors)
    )
    right = next(spec for spec in halves if spec is not left)
    typed = [
        next(
            connector
            for connector in spec.connectors
            if connector.exclusive and connector.edge_types
        )
        for spec in (left, right)
    ]
    assert connectors_compatible(
        typed[0], typed[1], left_source=left.source_id, right_source=right.source_id
    )
    assert not connectors_compatible(typed[0], typed[1], left_source="nope", right_source="other")
    assert {typed[0].edge_types, typed[1].edge_types} == {("C_Dig_left",), ("C_Dig_right",)}
    pits = {
        spec.piece_key: spec
        for spec in load_carrera_library().parts
        if spec.article_number == "30356"
    }
    mouths = {
        spec.piece_key: spec
        for spec in load_carrera_library().parts
        if spec.article_number == "20602"
    }
    start = pits["30356.1"].connectors[0]
    end = pits["30356.2"].connectors[0]
    start_edge = next(
        connector
        for connector in mouths["20602.1"].connectors
        if connector.edge_types == ("PSL_Start",)
    )
    end_edge = next(
        connector
        for connector in mouths["20602.2"].connectors
        if connector.edge_types == ("PSL_End",)
    )
    assert connectors_compatible(
        start,
        start_edge,
        left_source=pits["30356.1"].source_id,
        right_source=mouths["20602.1"].source_id,
    )
    assert not connectors_compatible(
        start,
        end_edge,
        left_source=pits["30356.1"].source_id,
        right_source=mouths["20602.2"].source_id,
    )
    assert not connectors_compatible(
        end,
        start_edge,
        left_source=pits["30356.2"].source_id,
        right_source=mouths["20602.1"].source_id,
    )
    plain = ConnectorSpec("e", 0, 0, 0, 0, BORDER_JOINT, (), edge_length_mm=start.edge_length_mm)
    assert not connectors_compatible(
        start, plain, left_source=pits["30356.1"].source_id, right_source="plain"
    )
    dig_left = ConnectorSpec(
        "e", 0, 0, 0, 0, BORDER_JOINT, (), edge_length_mm=100.0, edge_types=("C_Dig_left",)
    )
    dig_right = ConnectorSpec(
        "e", 0, 0, 0, 180, BORDER_JOINT, (), edge_length_mm=100.0, edge_types=("C_Dig_right",)
    )
    assert not connectors_compatible(dig_left, dig_right, left_source="a", right_source="b")
    assert connectors_compatible(
        ConnectorSpec("e", 0, 0, 0, 0, BORDER_JOINT, (), edge_length_mm=100.0),
        ConnectorSpec(
            "f", 0, 0, 0, 180, BORDER_JOINT, (), edge_length_mm=100.0 + EDGE_LENGTH_TOLERANCE_MM
        ),
    )
    assert not connectors_compatible(
        ConnectorSpec("e", 0, 0, 0, 0, BORDER_JOINT, (), edge_length_mm=100.0),
        ConnectorSpec("f", 0, 0, 0, 180, BORDER_JOINT, (), edge_length_mm=100.0 + 0.11),
    )


def test_occupancy_stays_at_one_millimetre() -> None:
    straight = _straight()
    host = PartInstance("host", 1, 0.0, 0.0)
    rail = next(
        connector
        for connector in straight.connectors
        if connector.kind == TRACK and connector.x_mm > 0
    )
    shifted = PartInstance("next", 2, 345.5, 0.0)
    assert connector_occupied(host, rail, ((shifted, straight),), source_id=straight.source_id)
    assert not connector_occupied(
        host, rail, ((shifted, straight),), tolerance_mm=0.1, source_id=straight.source_id
    )


def test_catalogue_reads_use_the_file_without_rewriting_rows(env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    straight = _straight()
    curve = _curve()
    with env.runtime.database.session() as session:
        row = session.scalar(
            select(TrackPartDefinition).where(TrackPartDefinition.source_id == straight.source_id)
        )
        assert row is not None
        revision = row.revision
        slots = row.slot_paths
        stored = session.scalars(
            select(TrackPartConnector)
            .where(TrackPartConnector.part_id == row.id)
            .order_by(TrackPartConnector.sort_order, TrackPartConnector.id)
        ).all()
        joints = [
            (connector.name, connector.neighbor_x, connector.neighbor_y) for connector in stored
        ]
        template = session.scalar(
            select(TrackPartTemplate).where(TrackPartTemplate.article_number == "30362")
        )
        assert template is not None
        members = session.scalars(
            select(TrackPartTemplateMember)
            .where(TrackPartTemplateMember.template_id == template.id)
            .order_by(TrackPartTemplateMember.sort_order, TrackPartTemplateMember.id)
        ).all()
        placeholders = [(member.x_mm, member.y_mm, member.angle_deg) for member in members]
    planner.library.ensure_seed()
    loaded = {record.spec.source_id: record.spec for record in planner.list_parts()}
    assert loaded[curve.source_id].center_y_mm == pytest.approx(-297.0)
    assert loaded[curve.source_id].connectors == curve.connectors
    assert loaded[straight.source_id].connectors == straight.connectors
    border = loaded[_article("20560").source_id]
    assert any(connector.edge_length_mm == pytest.approx(172.5) for connector in border.connectors)
    with env.runtime.database.session() as session:
        again = session.scalar(
            select(TrackPartDefinition).where(TrackPartDefinition.source_id == straight.source_id)
        )
        assert again is not None
        assert again.revision == revision
        assert again.slot_paths == slots
        kept = session.scalars(
            select(TrackPartConnector)
            .where(TrackPartConnector.part_id == again.id)
            .order_by(TrackPartConnector.sort_order, TrackPartConnector.id)
        ).all()
        assert [
            (connector.name, connector.neighbor_x, connector.neighbor_y) for connector in kept
        ] == joints
        template = session.scalar(
            select(TrackPartTemplate).where(TrackPartTemplate.article_number == "30362")
        )
        assert template is not None
        again_members = session.scalars(
            select(TrackPartTemplateMember)
            .where(TrackPartTemplateMember.template_id == template.id)
            .order_by(TrackPartTemplateMember.sort_order, TrackPartTemplateMember.id)
        ).all()
        assert [
            (member.x_mm, member.y_mm, member.angle_deg) for member in again_members
        ] == placeholders
    track = env.track("Gerade")
    record = next(
        item for item in planner.list_parts() if item.spec.source_id == straight.source_id
    )
    plan = place_instance(
        empty_plan(track.id), record.id, record.spec, 0.0, 0.0, 0.0, {record.id: record.spec}
    )
    planner.save(plan)
    loaded_plan = planner.load(track.id)
    assert len(loaded_plan.instances) == 1
    assert loaded_plan.instances[0].x_mm == pytest.approx(plan.instances[0].x_mm)
    assert loaded_plan.instances[0].y_mm == pytest.approx(plan.instances[0].y_mm)
    assert loaded_plan.instances[0].rotation_z_deg == pytest.approx(
        plan.instances[0].rotation_z_deg
    )


def _assert_bottleneck(
    template: TemplateDraft,
    instances: tuple[PartInstance, ...],
    specs: dict[str, PartSpec],
    delta_y: float,
) -> None:
    for member, instance in zip(template.members, instances, strict=True):
        center_y = specs[member.source_id].center_y_mm
        if center_y == pytest.approx(-49.5):
            assert instance.y_mm == pytest.approx(member.y_mm + delta_y)
        else:
            assert center_y == pytest.approx(0.0)
            assert instance.y_mm == pytest.approx(member.y_mm)


def _rotate(x_mm: float, y_mm: float, angle_deg: float) -> tuple[float, float]:
    turn = math.radians(angle_deg)
    return (
        x_mm * math.cos(turn) - y_mm * math.sin(turn),
        x_mm * math.sin(turn) + y_mm * math.cos(turn),
    )


def _turn(host: PartInstance, joint: ConnectorSpec, pose: Pose, spec: PartSpec) -> float:
    seated = PartInstance("next", 2, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg)
    delta = placed_continuation_delta_deg(host, joint, seated, spec)
    assert delta is not None
    return delta
