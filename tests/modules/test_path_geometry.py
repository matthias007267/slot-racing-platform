"""Shared path geometry: lines, arcs, Bézier curves, borders and revisions."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtGui import QPainterPath
from pytestqt.qtbot import QtBot

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.attachment import (
    HOST_CUSTOM,
    attachment_fits,
    follow_hosts,
)
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.figure import track_figure
from slot_racing.modules.track_planner.geometry import (
    BEZIER_LENGTH_TOLERANCE_MM,
    ArcSegment,
    CubicBezier,
    GeometryPath,
    LineSegment,
)
from slot_racing.modules.track_planner.lane_length import path_length_mm, span_length_mm
from slot_racing.modules.track_planner.parts import (
    BORDER,
    BORDER_JOINT,
    PATH_CONTOUR,
    STRAIGHT,
    TRACK,
    AttachmentProfile,
    BorderEdge,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    Pose,
    SlotPath,
    SlotSpan,
    build_part,
    can_dock,
    decode_geometry,
    driving_paths,
    encode_geometry,
    rails_can_join,
    rectangle,
    same_geometry,
    snap_pose,
    standard_catalog,
    validate_part_geometry,
    world_xy,
)
from slot_racing.modules.track_planner.ui.track_paint import _add_span
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner


def test_a_line_has_length_ends_and_a_constant_tangent() -> None:
    segment = LineSegment(0.0, 0.0, 30.0, 40.0)
    assert segment.start() == (0.0, 0.0)
    assert segment.end() == (30.0, 40.0)
    assert segment.length_mm() == pytest.approx(50.0)
    assert segment.point_at(0.5) == pytest.approx((15.0, 20.0))
    assert segment.tangent_deg() == pytest.approx(53.130102354)
    moved = segment.translated(5.0, -2.0)
    assert moved.start() == (5.0, -2.0)
    turned = LineSegment(1.0, 0.0, 2.0, 0.0).rotated(90.0)
    assert turned.start() == pytest.approx((0.0, 1.0))
    assert turned.end() == pytest.approx((0.0, 2.0))
    stored = _with_paths((SlotPath((SlotSpan.line(0, 0, 30, 40),)),))
    restored = decode_geometry(encode_geometry(stored))
    assert restored[0][0].spans[0] == SlotSpan.line(0, 0, 30, 40)


def test_an_arc_uses_radius_and_sweep_and_keeps_carrera_curves() -> None:
    arc = ArcSegment(0.0, 0.0, 100.0, 0.0, 90.0)
    assert arc.length_mm() == pytest.approx(157.079632679)
    assert arc.start() == pytest.approx((100.0, 0.0))
    assert arc.end() == pytest.approx((0.0, 100.0))
    assert arc.tangent_deg(0.0) == pytest.approx(90.0)
    assert arc.tangent_deg(1.0) == pytest.approx(180.0)
    opposite = ArcSegment(0.0, 0.0, 100.0, 90.0, -90.0)
    assert opposite.start() == pytest.approx((0.0, 100.0))
    assert opposite.end() == pytest.approx((100.0, 0.0))
    shifted = arc.translated(5.0, 0.0).rotated(0.0, (5.0, 0.0))
    assert shifted.cx == pytest.approx(5.0)
    curve = _catalog("20020571")
    paths = driving_paths(curve)
    assert len(paths) == 2
    drawn = span_length_mm(track_figure(curve).slots[0].spans[0])
    assert paths[0].length_mm() == pytest.approx(drawn)
    assert curve.radius_mm == pytest.approx(300.0)
    assert curve.angle_deg == pytest.approx(60.0)
    assert curve.slot_paths == ()


def test_a_cubic_bezier_keeps_its_control_points() -> None:
    curve = CubicBezier(0.0, 0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 0.0)
    assert curve.point_at(0.0) == (0.0, 0.0)
    assert curve.point_at(1.0) == (100.0, 0.0)
    assert curve.point_at(0.5) == pytest.approx((50.0, 75.0))
    assert curve.tangent_deg(0.0) == pytest.approx(90.0)
    assert curve.tangent_deg(1.0) == pytest.approx(270.0)
    box = curve.bounds()
    assert box.min_x == pytest.approx(0.0)
    assert box.max_x == pytest.approx(100.0)
    assert box.min_y == pytest.approx(0.0)
    assert box.max_y == pytest.approx(75.0)
    straight = CubicBezier(0.0, 0.0, 100.0 / 3.0, 0.0, 200.0 / 3.0, 0.0, 100.0, 0.0)
    assert straight.length_mm() == pytest.approx(100.0, abs=BEZIER_LENGTH_TOLERANCE_MM)
    turned = curve.rotated(90.0)
    assert turned.point_at(0.0) == pytest.approx((0.0, 0.0))
    assert turned.point_at(0.5) == pytest.approx((-75.0, 50.0))
    span = SlotSpan.bezier(0, 0, 0, 100, 100, 100, 100, 0)
    spec = _with_paths(
        (SlotPath((span,), lane=1), SlotPath((SlotSpan.line(0, 50, 100, 50),), lane=2))
    )
    encoded = encode_geometry(spec)
    loaded, _contours, _edges = decode_geometry(encoded)
    assert loaded[0].spans[0] == span
    assert loaded[0].lane == 1
    stored = encoded[0]["spans"]
    assert isinstance(stored, list)
    point = stored[0]
    assert isinstance(point, dict)
    assert point["x1"] == 0
    assert point["y2"] == 100


def test_a_bezier_is_drawn_as_a_cubic(qtbot: QtBot) -> None:
    path = QPainterPath()
    _add_span(path, SlotSpan.bezier(0, 0, 0, 10, 10, 10, 10, 0))
    kind = QPainterPath.ElementType
    assert path.elementCount() == 4
    assert path.elementAt(0).type == kind.MoveToElement
    assert path.elementAt(1).type == kind.CurveToElement
    assert path.elementAt(1).x == pytest.approx(0.0)
    assert path.elementAt(1).y == pytest.approx(10.0)
    assert path.elementAt(2).type == kind.CurveToDataElement
    assert path.elementAt(2).x == pytest.approx(10.0)
    assert path.elementAt(2).y == pytest.approx(10.0)
    assert path.elementAt(3).type == kind.CurveToDataElement
    assert path.elementAt(3).x == pytest.approx(10.0)
    assert path.elementAt(3).y == pytest.approx(0.0)
    assert qtbot is not None


def test_a_composite_path_sums_its_segments_and_reports_a_kink() -> None:
    bow = CubicBezier(40.0, 20.0, 50.0, 20.0, 60.0, 30.0, 70.0, 30.0)
    path = GeometryPath(
        (
            LineSegment(0.0, 0.0, 40.0, 0.0),
            ArcSegment(40.0, 10.0, 10.0, -90.0, 180.0),
            bow,
            LineSegment(70.0, 30.0, 90.0, 30.0),
        )
    )
    arc_length = ArcSegment(0, 0, 10, 0, 180).length_mm()
    assert path.length_mm() == pytest.approx(40.0 + arc_length + bow.length_mm() + 20.0)
    assert path.tangent_at_start_deg() == pytest.approx(0.0)
    turned = path.rotated(90.0)
    assert turned.start() == pytest.approx((0.0, 0.0))
    assert turned.segments[0].end() == pytest.approx((0.0, 40.0))
    assert turned.length_mm() == pytest.approx(path.length_mm())
    kinked = GeometryPath((LineSegment(0, 0, 10, 0), LineSegment(10, 0, 10, 10)))
    joint = kinked.joints()[0]
    assert joint.position_continuous
    assert not joint.tangent_continuous
    spans = tuple(_span(segment) for segment in kinked.segments)
    validate_part_geometry(_with_paths((SlotPath(spans),)))
    broken = GeometryPath((LineSegment(0, 0, 10, 0), LineSegment(12, 0, 20, 0)))
    assert not broken.joints()[0].position_continuous
    with pytest.raises(ValidationError) as caught:
        validate_part_geometry(
            _with_paths((SlotPath((SlotSpan.line(0, 0, 10, 0), SlotSpan.line(12, 0, 20, 0))),))
        )
    assert caught.value.key == "error.planner.geometry"


def test_lanes_may_diverge_and_still_meet_their_connectors() -> None:
    left = SlotPath((SlotSpan.bezier(-100, -40, -40, -70, 40, -10, 100, -40),), lane=1)
    right = SlotPath((SlotSpan.line(-100, 80, 100, 20),), lane=2)
    spec = _with_paths((left, right))
    validate_part_geometry(spec)
    paths = driving_paths(spec)
    assert [path.lane for path in paths] == [1, 2]
    assert abs(paths[0].start()[1] - paths[1].start()[1]) == pytest.approx(120.0)
    assert abs(paths[0].end()[1] - paths[1].end()[1]) == pytest.approx(60.0)
    assert spec.slot_paths[0].spans[0].c1y == pytest.approx(-70.0)
    straight = _catalog("20020601")
    derived = driving_paths(straight)
    assert straight.slot_paths == ()
    assert [path.lane for path in derived] == [1, 2]
    assert derived[0].length_mm() == pytest.approx(345.0)
    assert derived[1].length_mm() == pytest.approx(345.0)


def test_rails_join_only_at_track_connectors() -> None:
    rail = _rail()
    other = replace(rail, article_number="EB-2", name="Zweite")
    target = PartInstance("target", 1, 0.0, 0.0)
    seated = snap_pose(other, Pose(200.0, 4.0, 0.0), ((target, rail),), snap_mm=50.0, grid_mm=None)
    assert seated.y_mm == pytest.approx(0.0)
    assert rails_can_join(rail.connectors[0], rail.connectors[1])
    border = build_part(
        article_number="RAND",
        scale="1:24",
        name="Rand",
        category=BORDER,
        length_mm=200.0,
        width_mm=40.0,
        height_mm=None,
        radius_mm=None,
        angle_deg=0.0,
        lane_count=2,
    )
    assert border.connectors[0].kind == BORDER_JOINT
    assert not rails_can_join(rail.connectors[0], border.connectors[0])
    untouched = snap_pose(
        rail,
        Pose(200.0, 4.0, 0.0),
        ((PartInstance("border", 2, 0.0, 0.0), border),),
        snap_mm=500.0,
        grid_mm=None,
    )
    assert (untouched.x_mm, untouched.y_mm) == pytest.approx((200.0, 4.0))
    assert not can_dock(border, target, rail, ())
    assert can_dock(other, target, rail, ())


def test_borders_use_side_edges_and_follow_rotation() -> None:
    straight = _catalog("20020601")
    border = _catalog("20020560")
    curve = _catalog("20020573")
    outer = next(
        spec for spec in standard_catalog() if spec.name == "Außenrandstreifen Kurve 3/30°"
    )
    inner = next(
        spec for spec in standard_catalog() if spec.name == "Innenrandstreifen Kurve 3/30°"
    )
    assert attachment_fits(border, straight, "left")
    assert attachment_fits(border, straight, "right")
    assert not attachment_fits(border, straight, "inner")
    assert attachment_fits(outer, curve, "outer")
    assert not attachment_fits(outer, curve, "inner")
    assert attachment_fits(inner, curve, "inner")
    assert not attachment_fits(inner, curve, "outer")
    assert not attachment_fits(border, curve, "left")
    host = PartInstance("host", 1, 10.0, 20.0, rotation_z_deg=90.0)
    strip = PartInstance("strip", 2, 0.0, 0.0, host_id="host", attachment_slot="left")
    synced = follow_hosts((host, strip))[1]
    assert synced.rotation_z_deg == pytest.approx(90.0)
    assert world_xy(synced, 0.0, -120.0) == pytest.approx((130.0, 20.0))
    edge = (SlotSpan.line(-100.0, -100.0, 100.0, -100.0),)
    custom_host = replace(straight, border_edges=(BorderEdge("left", edge),))
    custom_border = replace(
        border,
        attachment=AttachmentProfile(HOST_CUSTOM, ("left",), host_lanes=2, edge=edge),
    )
    assert attachment_fits(custom_border, custom_host, "left")
    assert not attachment_fits(custom_border, custom_host, "right")
    assert attachment_fits(border, custom_host, "left")
    assert attachment_fits(border, custom_host, "right")
    assert not can_dock(custom_border, PartInstance("h", 1, 0, 0), custom_host, ())
    roundtrip = decode_geometry(encode_geometry(custom_host))
    assert roundtrip[2][0].slot == "left"
    assert roundtrip[2][0].spans == edge
    border_roundtrip = decode_geometry(encode_geometry(custom_border))
    assert border_roundtrip[2][0].spans == edge


def test_a_used_bezier_edit_forks_and_the_plan_keeps_the_old_geometry(env: Env) -> None:
    planner = _planner(env)
    straight = next(
        record
        for record in planner.list_parts()
        if record.spec.article_number == "20509" and not record.spec.piece_key
    )
    track = env.track("Alt", lanes=2)
    fresh = env.track("Neu", lanes=2)
    catalog = {straight.id: straight.spec}
    planner.save(
        place_instance(empty_plan(track.id), straight.id, straight.spec, 0, 0, 15, catalog)
    )
    bow = SlotSpan.bezier(-172.5, -50.0, -40.0, -90.0, 40.0, -10.0, 172.5, -50.0)
    other = SlotSpan.line(-172.5, 50.0, 172.5, 50.0)
    edited = replace(
        straight.spec,
        slot_paths=(SlotPath((bow,), lane=1), SlotPath((other,), lane=2)),
    )
    revised = planner.update_part(straight.id, edited)
    assert revised.id != straight.id
    assert revised.lineage_id == straight.lineage_id
    frozen = planner.library.require(straight.id)
    assert frozen.spec.slot_paths == straight.spec.slot_paths
    assert frozen.spec.slot_paths
    assert frozen.spec.length_mm == pytest.approx(345.0)
    assert revised.spec.slot_paths[0].spans[0] == bow
    assert planner.load(track.id).instances[0].part_id == straight.id
    assert planner.load(track.id).instances[0].rotation_z_deg == pytest.approx(15.0)
    placed = place_instance(
        empty_plan(fresh.id),
        revised.id,
        revised.spec,
        0,
        0,
        0,
        {revised.id: revised.spec, straight.id: frozen.spec},
    )
    planner.save(placed)
    assert planner.load(fresh.id).instances[0].part_id == revised.id
    assert path_length_mm(revised.spec.slot_paths[0].spans) == pytest.approx(
        driving_paths(revised.spec)[0].length_mm()
    )


def test_contours_stay_apart_from_the_lanes_and_the_filled_outline() -> None:
    outline = rectangle(200.0, 200.0)
    bow = SlotSpan.bezier(-80, -90, -20, -110, 20, -70, 80, -90)
    inner = SlotSpan.arc(0, 0, 40, 0, 180)
    spec = replace(
        _with_paths((SlotPath((SlotSpan.line(-100, -50, 100, -50),), lane=1),)),
        outline=outline,
        contours=(
            SlotPath((bow,), PATH_CONTOUR),
            SlotPath((inner,), PATH_CONTOUR),
        ),
        border_edges=(BorderEdge("left", (SlotSpan.line(-100, -100, 100, -100),)),),
    )
    validate_part_geometry(spec)
    _slots, contours, edges = decode_geometry(encode_geometry(spec))
    assert contours[0].spans[0] == bow
    assert contours[1].spans[0] == inner
    assert edges[0].slot == "left"
    figure = track_figure(spec)
    assert figure.roadway == outline
    assert figure.edges == spec.contours
    assert same_geometry(spec, replace(spec, name="Umbenannt"))
    moved = replace(spec, border_edges=(BorderEdge("right", spec.border_edges[0].spans),))
    assert not same_geometry(spec, moved)


def test_invalid_geometry_is_rejected() -> None:
    with pytest.raises(ValidationError):
        LineSegment(float("nan"), 0.0, 1.0, 0.0)
    with pytest.raises(ValidationError):
        ArcSegment(0.0, 0.0, -10.0, 0.0, 20.0)
    with pytest.raises(ValidationError):
        validate_part_geometry(_with_paths((SlotPath((SlotSpan.line(0, 0, 0, 0),)),)))
    broken = {"kind": "bezier", "x0": 0, "y0": 0, "x1": 1}
    assert decode_geometry([{"kind": "slot", "spans": [broken]}]) == ((), (), ())
    with pytest.raises(ValidationError):
        validate_part_geometry(_with_paths((SlotPath((SlotSpan.line(-10, 0, 10, 0),), lane=9),)))


def _catalog(article: str) -> PartSpec:
    return next(spec for spec in standard_catalog() if spec.article_number == article)


def _with_paths(paths: tuple[SlotPath, ...]) -> PartSpec:
    return PartSpec(
        article_number="EB-1",
        scale="1:24",
        name="Sonderteil",
        category=STRAIGHT,
        length_mm=200.0,
        width_mm=200.0,
        height_mm=None,
        radius_mm=None,
        angle_deg=0.0,
        lane_count=2,
        connectors=(
            ConnectorSpec("a", -100.0, 0.0, 0.0, 180.0, TRACK, (1, 2)),
            ConnectorSpec("b", 100.0, 0.0, 0.0, 0.0, TRACK, (1, 2)),
        ),
        outline=rectangle(200.0, 200.0),
        slot_paths=paths,
    )


def _rail() -> PartSpec:
    return _with_paths(())


def _span(segment: LineSegment | ArcSegment | CubicBezier) -> SlotSpan:
    if isinstance(segment, LineSegment):
        return SlotSpan.line(segment.x0, segment.y0, segment.x1, segment.y1)
    if isinstance(segment, ArcSegment):
        return SlotSpan.arc(
            segment.cx, segment.cy, segment.radius_mm, segment.start_deg, segment.sweep_deg
        )
    return SlotSpan.bezier(
        segment.p0x,
        segment.p0y,
        segment.p1x,
        segment.p1y,
        segment.p2x,
        segment.p2y,
        segment.p3x,
        segment.p3y,
    )
