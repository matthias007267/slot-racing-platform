"""Geometry edits the part model can express: lanes, joints, contours and borders.

The former drawing tools are gone. These tests call the Qt-free operations directly.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.attachment import attachment_fits
from slot_racing.modules.track_planner.geometry import (
    CubicBezier,
    LineSegment,
    closest_on_segment,
    split_segment,
)
from slot_racing.modules.track_planner.parametric import (
    BODY_BORDER,
    BODY_STRAIGHT,
    DIRECTION_RIGHT,
    SIDE_BOTH,
    PartDraft,
    spec_from_draft,
)
from slot_racing.modules.track_planner.parts import (
    BORDER,
    PATH_SLOT,
    SPAN_BEZIER,
    SPECIAL,
    STRAIGHT,
    TRACK,
    PartSpec,
    SlotSpan,
    as_segment,
    decode_geometry,
    driving_paths,
    encode_geometry,
    same_geometry,
    standard_catalog,
)


def _draft(length: float = 400.0, *, category: str = STRAIGHT, body: str = "straight") -> PartDraft:
    return PartDraft(
        category=category,
        body=BODY_STRAIGHT if body == "straight" else BODY_BORDER,
        length_mm=length,
        radius_mm=None,
        angle_deg=0.0,
        direction=DIRECTION_RIGHT,
        lane_count=2,
        pitch_mm=100.0,
        width_mm=40.0 if body == "border" else None,
        offsets_mm=None,
        border_side=SIDE_BOTH,
        host_lanes=2,
    )


def _straight(length: float = 400.0, name: str = "Eigen") -> PartSpec:
    return spec_from_draft(
        _draft(length),
        article_number="EB-1",
        scale="1:24",
        name=name,
        manufacturer="",
        description="",
        height_mm=None,
        validate_identity=False,
    )


def _catalog(name: str) -> PartSpec:
    return next(spec for spec in standard_catalog() if spec.name == name)


# --------------------------------------------------------------------------------------------
# Catalogue


def test_every_drawable_catalogue_part_is_valid_and_keeps_its_lane_lengths() -> None:
    drawn = 0
    for spec in standard_catalog():
        if ed.support_reason(spec) != "":
            continue
        drawn += 1
        written = ed.explicit(spec) if spec.attachment is None else spec
        assert ed.validate_editing(ed.finalize(written, spec)) == (), spec.name
        if spec.attachment is None:
            before = sorted(round(p.length_mm(), 3) for p in driving_paths(spec))
            after = sorted(round(p.length_mm(), 3) for p in driving_paths(written))
            assert before == after, spec.name
    assert drawn >= 17


def test_unsupported_parts_say_why_and_stay_untouched() -> None:
    assert ed.support_reason(_catalog("Weiche")) == ed.SUPPORT_CATEGORY
    assert ed.support_reason(_catalog("Kreuzung")) == ed.SUPPORT_CATEGORY
    assert ed.support_reason(_catalog("Spurwechsel links")) == ed.SUPPORT_CATEGORY
    assert ed.support_reason(_catalog("Pitlane-Einfahrt")) == ed.SUPPORT_CATEGORY
    assert ed.support_reason(_catalog("Randstreifen Standardgerade")) == (
        ed.SUPPORT_STANDARD_BORDER
    )
    assert ed.support_reason(_catalog("Endstück Kurve 3/30°")) == ed.SUPPORT_END_PIECE
    assert ed.support_reason(_straight(), corrupt=True) == ed.SUPPORT_CORRUPT
    assert ed.support_reason(_catalog("Engstelle")) == ed.SUPPORT_OK
    assert ed.support_reason(_catalog("Schikane")) == ed.SUPPORT_OK


def test_a_branching_groove_is_refused_not_given_a_wrong_length() -> None:
    spec = ed.explicit(_straight())
    branch = replace(
        spec,
        slot_paths=(
            *spec.slot_paths,
            replace(spec.slot_paths[0], lane=1, spans=(SlotSpan.line(-200.0, -50.0, 0.0, -50.0),)),
        ),
    )
    keys = {issue.key for issue in ed.validate_editing(branch)}
    assert "error.planner.editor.lane_twice" in keys or "error.planner.editor.branch" in keys
    assert ed.support_reason(branch) == ed.SUPPORT_BRANCH


# --------------------------------------------------------------------------------------------
# Segments


def test_splitting_keeps_the_curve_for_lines_arcs_and_beziers() -> None:
    line = as_segment(SlotSpan.line(0, 0, 100, 40))
    arc = as_segment(SlotSpan.arc(0, 0, 100, 10, 80))
    bezier = as_segment(SlotSpan.bezier(0, 0, 30, 60, 70, 60, 100, 0))
    for segment in (line, arc, bezier):
        first, second = split_segment(segment, 0.4)
        assert first.point_at(1.0) == pytest.approx(second.point_at(0.0))
        assert first.point_at(0.0) == pytest.approx(segment.point_at(0.0))
        assert second.point_at(1.0) == pytest.approx(segment.point_at(1.0))
        assert first.length_mm() + second.length_mm() == pytest.approx(
            segment.length_mm(), abs=0.01
        )
        t, distance = closest_on_segment(segment, *segment.point_at(0.3))
        assert t == pytest.approx(0.3, abs=0.01)
        assert distance == pytest.approx(0.0, abs=0.05)
    with pytest.raises(ValidationError):
        split_segment(line, 0.0)


def test_moving_a_node_drags_both_neighbours_and_a_bezier_anchor_its_control_point() -> None:
    spec = ed.explicit(_straight())
    ref = ed.groove_refs(spec)[0]
    spec = ed.split_span(spec, ref, 0)
    moved = ed.move_point(spec, ed.PointRef(ref, 0, "end"), -20.0, -10.0)
    spans = ed.spans_of(moved, ref)
    assert (spans[0].x1, spans[0].y1) == pytest.approx((-20.0, -10.0))
    assert (spans[1].x0, spans[1].y0) == pytest.approx((-20.0, -10.0))
    apart = ed.move_point(spec, ed.PointRef(ref, 0, "end"), -20.0, -10.0, join=False)
    assert (ed.spans_of(apart, ref)[1].x0, ed.spans_of(apart, ref)[1].y0) == pytest.approx(
        (0.0, -50.0)
    )
    curved = ed.convert_span(spec, ref, 1, SPAN_BEZIER)
    before = ed.spans_of(curved, ref)[1]
    pulled = ed.move_point(curved, ed.PointRef(ref, 1, "p0"), 10.0, -30.0)
    after = ed.spans_of(pulled, ref)[1]
    assert after.c1x - after.x0 == pytest.approx(before.c1x - before.x0)
    assert after.c1y - after.y0 == pytest.approx(before.c1y - before.y0)


def test_bezier_control_points_survive_the_stored_form_unchanged() -> None:
    spec = ed.explicit(_straight())
    ref = ed.groove_refs(spec)[0]
    spec = ed.convert_span(spec, ref, 0, SPAN_BEZIER)
    spec = ed.move_point(spec, ed.PointRef(ref, 0, "p1"), -123.456, -77.25)
    spec = ed.move_point(spec, ed.PointRef(ref, 0, "p2"), 98.125, -12.5)
    stored = ed.spans_of(spec, ref)[0]
    slots, _contours, _edges = decode_geometry(encode_geometry(spec))
    path = next(item for item in slots if item.kind == PATH_SLOT and item.lane == 1)
    assert path.spans[0] == stored
    assert (path.spans[0].c1x, path.spans[0].c1y) == (-123.456, -77.25)


def test_tangent_modes_smooth_and_symmetric_remove_the_kink() -> None:
    spec = ed.explicit(_straight())
    ref = ed.groove_refs(spec)[0]
    spec = ed.split_span(spec, ref, 0)
    spec = ed.move_point(spec, ed.PointRef(ref, 0, "end"), 0.0, -90.0)
    spec = ed.convert_span(spec, ref, 1, SPAN_BEZIER)
    assert ed.joint_report(spec, ref)[0][1] > 1.0
    smooth = ed.set_tangent(spec, ref, 0, ed.TANGENT_SMOOTH)
    assert ed.joint_report(smooth, ref)[0][1] < 1.0
    both = ed.convert_span(spec, ref, 0, SPAN_BEZIER)
    symmetric = ed.set_tangent(both, ref, 0, ed.TANGENT_SYMMETRIC)
    left, right = (as_segment(item) for item in ed.spans_of(symmetric, ref)[:2])
    assert isinstance(left, CubicBezier)
    assert isinstance(right, CubicBezier)
    assert ed.joint_report(symmetric, ref)[0][1] < 1.0
    _l0, _l1, l2, l3 = left.control_points()
    r0, r1, _r2, _r3 = right.control_points()
    assert math.hypot(l3[0] - l2[0], l3[1] - l2[1]) == pytest.approx(
        math.hypot(r1[0] - r0[0], r1[1] - r0[1])
    )
    assert ed.set_tangent(spec, ref, 0, ed.TANGENT_KINK) == spec


def test_a_gap_is_found_blocks_saving_and_can_be_closed() -> None:
    spec = ed.explicit(_straight())
    ref = ed.groove_refs(spec)[0]
    spec = ed.split_span(spec, ref, 0, 0.3)
    spec = ed.split_span(spec, ref, 1, 0.5)
    spec = ed.delete_span(spec, ref, 1)
    assert "error.planner.editor.gap" in {issue.key for issue in ed.validate_editing(spec)}
    closed = ed.close_gap(spec, ref, 0)
    assert "error.planner.editor.gap" not in {issue.key for issue in ed.validate_editing(closed)}
    assert ed.geometry_path(closed, ref).length_mm() == pytest.approx(400.0)


def test_kinks_are_allowed_and_only_reported_as_a_joint() -> None:
    spec = ed.explicit(_straight())
    ref = ed.groove_refs(spec)[0]
    spec = ed.split_span(spec, ref, 0)
    spec = ed.move_point(spec, ed.PointRef(ref, 0, "end"), 0.0, -70.0)
    assert ed.joint_report(spec, ref)[0][1] > 10.0
    assert "error.planner.editor.gap" not in {i.key for i in ed.validate_editing(spec)}


def test_degenerate_and_non_finite_input_never_reaches_the_part() -> None:
    spec = ed.explicit(_straight())
    ref = ed.groove_refs(spec)[0]
    with pytest.raises(ValidationError):
        ed.set_line(spec, ref, 0, length=0.0)
    with pytest.raises(ValidationError):
        ed.set_line(spec, ref, 0, length=float("nan"))
    with pytest.raises(ValidationError):
        ed.move_point(spec, ed.PointRef(ref, 0, "end"), float("inf"), 0.0)
    assert ed.arc_through((0, 0), (10, 0), (20, 0)) is None
    with pytest.raises(ValidationError):
        ed.set_connector(spec, 0, x_mm=float("nan"))
    with pytest.raises(ValidationError):
        ed.split_span(spec, ref, 5)


# --------------------------------------------------------------------------------------------
# Lanes and joints


def test_lanes_can_be_added_removed_swapped_and_measured() -> None:
    spec = ed.explicit(_straight())
    wider, ref = ed.add_lane(spec)
    assert ed.lane_numbers(wider) == (1, 2, 3)
    assert next(i for i in wider.connectors if i.kind == TRACK).lanes == (1, 2, 3)
    wider = ed.move_point(wider, ed.PointRef(ref, 0, "start"), -200.0, -10.0)
    wider = ed.move_point(wider, ed.PointRef(ref, 0, "end"), 200.0, -10.0)
    assert ed.validate_editing(ed.finalize(wider, spec)) == ()
    swapped = ed.set_lane(wider, ed.groove_refs(wider)[0], 2)
    assert [path.lane for path in swapped.slot_paths if path.kind == PATH_SLOT] == [2, 1, 3]
    narrower = ed.remove_lane(wider, 2)
    assert ed.lane_numbers(narrower) == (1, 2)
    assert narrower.lane_count == 2
    assert all(item.lanes == (1, 2) for item in narrower.connectors if item.kind == TRACK)
    with pytest.raises(ValidationError):
        ed.remove_lane(ed.remove_lane(narrower, 2), 1)
    paths = driving_paths(ed.finalize(wider, spec))
    assert [path.lane for path in paths] == [1, 2, 3]
    assert all(path.length_mm() == pytest.approx(400.0) for path in paths)


def test_five_lanes_are_refused() -> None:
    spec = ed.explicit(_straight())
    for _ in range(2):
        spec, _ref = ed.add_lane(spec)
    with pytest.raises(ValidationError):
        ed.add_lane(spec)


def _squeeze(spec: PartSpec) -> PartSpec:
    """Both lanes run straight in and out and meet nearer the middle."""
    grooves = []
    middles = (-20.0, 20.0)
    index = 0
    for path in spec.slot_paths:
        if path.kind != PATH_SLOT:
            continue
        y0 = path.spans[0].y0
        y_mid = middles[index]
        index += 1
        grooves.append(
            replace(
                path,
                spans=(
                    SlotSpan.line(-200.0, y0, -150.0, y0),
                    SlotSpan.line(-150.0, y0, -70.0, y_mid),
                    SlotSpan.line(-70.0, y_mid, 70.0, y_mid),
                    SlotSpan.line(70.0, y_mid, 150.0, y0),
                    SlotSpan.line(150.0, y0, 200.0, y0),
                ),
            )
        )
    centers = tuple(path for path in spec.slot_paths if path.kind != PATH_SLOT)
    return replace(spec, slot_paths=(*grooves, *centers))


def test_a_part_with_a_narrowing_and_variable_pitch_is_valid_and_measured() -> None:
    spec = ed.explicit(_straight())
    result = ed.finalize(_squeeze(spec), spec)
    assert ed.validate_editing(result) == ()
    lengths = [path.length_mm() for path in driving_paths(result)]
    assert len(lengths) == 2
    assert lengths[0] == pytest.approx(lengths[1])
    assert lengths[0] > 400.0
    assert result.category == SPECIAL


def test_a_groove_that_leaves_its_joint_at_an_angle_is_reported() -> None:
    spec = ed.explicit(_straight())
    ref = ed.groove_refs(spec)[0]
    spec = ed.split_span(spec, ref, 0, 0.5)
    spec = ed.move_point(spec, ed.PointRef(ref, 0, "end"), 0.0, -20.0)
    keys = {issue.key for issue in ed.validate_editing(spec)}
    assert "error.planner.editor.direction" in keys


def test_rail_joints_report_missing_lanes_wrong_lanes_and_too_few_joints() -> None:
    spec = ed.explicit(_straight())
    moved = ed.set_connector(spec, 1, x_mm=250.0)
    assert "error.planner.editor.groove_end" in {i.key for i in ed.validate_editing(moved)}
    other = ed.set_connector(spec, 1, lanes=(1, 2, 3))
    assert "error.planner.editor.lane_missing" in {i.key for i in ed.validate_editing(other)}
    with pytest.raises(ValidationError):
        ed.remove_connector(spec, 0)
    three = ed.add_connector(spec, 0.0, 0.0, 90.0)
    assert len(ed.rail_connectors(three)) == 3
    assert "error.planner.editor.lane_missing" in {i.key for i in ed.validate_editing(three)}
    assert len(ed.rail_connectors(ed.remove_connector(three, 2))) == 2


def test_rails_still_join_only_on_track_joints_with_equal_lanes() -> None:
    spec = ed.explicit(_straight())
    one, _ref = ed.add_lane(spec)
    from slot_racing.modules.track_planner.parts import rails_can_join

    assert not rails_can_join(spec.connectors[1], one.connectors[0])
    assert rails_can_join(one.connectors[1], one.connectors[0])


# --------------------------------------------------------------------------------------------
# Contours and borders


def test_the_outline_follows_the_first_closed_contour_and_holes_are_only_stored() -> None:
    spec = _straight()
    spec = ed.contour_from_outline(spec)
    ref = ed.PathRef(ed.LAYER_CONTOUR, 0)
    assert ed.is_closed(ed.spans_of(spec, ref))
    spec = ed.move_point(spec, ed.PointRef(ref, 0, "end"), 200.0, -60.0)
    shaped = ed.finalize(spec, _straight())
    assert (200.0, -60.0) in [tuple(round(v, 6) for v in p) for p in shaped.outline]
    hole = ed.contour_from_polygon([(-20, -20), (20, -20), (20, 20), (-20, 20)])
    with_hole, _ref = ed.add_path(shaped, ed.LAYER_CONTOUR, hole)
    assert len(with_hole.contours) == 2
    assert ed.finalize(with_hole, shaped).outline == shaped.outline


def test_an_open_contour_is_an_incomplete_outline() -> None:
    spec = _straight()
    open_chain, _ref = ed.add_path(
        spec,
        ed.LAYER_CONTOUR,
        (SlotSpan.line(0, 0, 50, 0), SlotSpan.line(50, 0, 50, 50)),
    )
    keys = {i.key for i in ed.validate_editing(open_chain)}
    assert "error.planner.editor.contour_open" in keys
    closed = ed.close_path(open_chain, ed.PathRef(ed.LAYER_CONTOUR, 0))
    assert "error.planner.editor.contour_open" not in {i.key for i in ed.validate_editing(closed)}


def test_a_standard_border_becomes_custom_only_by_conversion() -> None:
    standard = _catalog("Randstreifen Standardgerade")
    custom = ed.to_custom_border(standard)
    assert custom.attachment is not None
    assert custom.attachment.edge
    assert custom.category == BORDER
    assert custom.connectors == ()
    assert ed.validate_editing(custom) == ()
    assert ed.support_reason(custom) == ed.SUPPORT_OK
    assert ed.to_custom_border(custom) == custom


def test_a_custom_border_fits_the_part_whose_edge_it_copies_and_never_a_rail_end() -> None:
    host = ed.with_side_edges(ed.explicit(_straight()))
    border = ed.to_custom_border(_catalog("Randstreifen Standardgerade"))
    assert not attachment_fits(border, host, "left")
    fitted = ed.copy_edge(border, host, "left")
    assert attachment_fits(fitted, host, "left")
    assert not attachment_fits(fitted, host, "right")
    assert fitted.connectors == ()


def test_a_strip_is_drawn_beside_its_edge_and_closes() -> None:
    border = ed.to_custom_border(_catalog("Randstreifen Standardgerade"))
    edge = border.attachment.edge if border.attachment else ()
    spans = ed.offset_strip(edge, 30.0, left=True)
    assert ed.is_closed(spans)
    curved = ed.to_custom_border(_catalog("Außenrandstreifen Kurve 3/30°"))
    assert curved.attachment is not None
    assert ed.is_closed(ed.offset_strip(curved.attachment.edge, 25.0, left=False))
    with pytest.raises(ValidationError):
        ed.offset_strip(edge, 0.0, left=True)


def test_edge_slots_must_be_one_family_and_unique() -> None:
    host = ed.with_side_edges(ed.explicit(_straight()))
    assert ed.validate_editing(ed.finalize(host, host)) == ()
    clash = replace(
        host, border_edges=(host.border_edges[0], replace(host.border_edges[1], slot="left"))
    )
    assert "error.planner.editor.edge_slots" in {i.key for i in ed.validate_editing(clash)}
    mixed = replace(
        host, border_edges=(host.border_edges[0], replace(host.border_edges[1], slot="inner"))
    )
    assert "error.planner.editor.edge_slots" in {i.key for i in ed.validate_editing(mixed)}


def test_a_border_with_rails_or_grooves_is_not_valid() -> None:
    border = ed.to_custom_border(_catalog("Randstreifen Standardgerade"))
    with_rail = replace(border, connectors=_straight().connectors)
    assert "error.planner.editor.attach_edge" in {i.key for i in ed.validate_editing(with_rail)}


def test_a_narrowing_keeps_its_rails_and_is_not_silently_a_curve() -> None:
    spec = ed.explicit(_straight())
    result = ed.finalize(_squeeze(spec), spec)
    assert result.category == SPECIAL
    assert not same_geometry(result, _straight())
    assert len(ed.rail_connectors(result)) == 2
    assert result.lane_count == 2
    assert ed.validate_editing(result) == ()
    assert isinstance(as_segment(ed.spans_of(result, ed.groove_refs(result)[0])[0]), LineSegment)
