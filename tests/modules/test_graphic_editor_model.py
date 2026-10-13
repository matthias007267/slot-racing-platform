"""Graphical part editor, model side: geometry edits, lanes, joints, contours, borders, tools."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.attachment import attachment_fits
from slot_racing.modules.track_planner.edit_session import (
    KIND_CONNECTOR,
    KIND_POINT,
    KIND_SEGMENT,
    EditSession,
    Snapping,
    Target,
    hit_targets,
    lane_rows,
    next_target,
    snap_point,
)
from slot_racing.modules.track_planner.edit_tools import (
    TOOL_ADD_POINT,
    TOOL_ARC,
    TOOL_BEZIER,
    TOOL_CONNECTOR,
    TOOL_DELETE,
    TOOL_LINE,
    TOOL_MOVE,
    TOOL_REMOVE_POINT,
    TOOL_SELECT,
    TOOL_SPLIT,
    EditController,
)
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
    CURVE,
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

TOL = 8.0


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


def _border() -> PartSpec:
    return spec_from_draft(
        _draft(220.0, category=BORDER, body="border"),
        article_number="EB-R",
        scale="1:24",
        name="Rand",
        manufacturer="",
        description="",
        height_mm=None,
        validate_identity=False,
    )


def _catalog(name: str) -> PartSpec:
    return next(spec for spec in standard_catalog() if spec.name == name)


def _controller(spec: PartSpec) -> EditController:
    return EditController(EditSession(spec))


def _keys(session: EditSession) -> set[str]:
    return {issue.key for issue in session.issues()}


def _drag(controller: EditController, start: tuple[float, float], end: tuple[float, float]) -> None:
    controller.press(start[0], start[1], TOL)
    controller.move(end[0], end[1], TOL, pressed=True)
    controller.release(end[0], end[1], TOL)


def _engstelle(controller: EditController) -> None:
    """Two lanes that run straight in and out and squeeze to the middle."""
    controller.run(ed.explicit)
    controller.set_tool(TOOL_ADD_POINT)
    for x in (-150, -70, 70, 150):
        controller.press(x, -50, TOL)
        controller.press(x, 50, TOL)
    controller.set_tool(TOOL_MOVE)
    for x in (-70, 70):
        _drag(controller, (x, -50), (x, -20))
        _drag(controller, (x, 50), (x, 20))


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
    session = EditSession(_straight())
    controller = EditController(session)
    controller.run(ed.explicit)
    ref = ed.groove_refs(session.spec)[0]
    controller.run(lambda spec: ed.split_span(spec, ref, 0, 0.3))
    controller.run(lambda spec: ed.split_span(spec, ref, 1, 0.5))
    controller.run(lambda spec: ed.delete_span(spec, ref, 1))
    assert "error.planner.editor.gap" in _keys(session)
    assert lane_rows(session.spec)[0].length_mm is None
    controller.run(lambda spec: ed.close_gap(spec, ref, 0))
    assert "error.planner.editor.gap" not in _keys(session)
    assert lane_rows(session.spec)[0].length_mm == pytest.approx(400.0)


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
    rows = lane_rows(ed.finalize(wider, spec))
    assert [row.lane for row in rows] == [1, 2, 3]
    assert all(row.length_mm == pytest.approx(400.0) for row in rows)


def test_five_lanes_are_refused() -> None:
    spec = ed.explicit(_straight())
    for _ in range(2):
        spec, _ref = ed.add_lane(spec)
    with pytest.raises(ValidationError):
        ed.add_lane(spec)


def test_a_part_with_a_narrowing_and_variable_pitch_is_valid_and_measured() -> None:
    controller = _controller(_straight())
    _engstelle(controller)
    session = controller.session
    assert _keys(session) == set()
    rows = lane_rows(session.result())
    assert rows[0].length_mm is not None
    assert rows[0].length_mm == pytest.approx(rows[1].length_mm or 0.0)
    assert rows[0].length_mm > 400.0
    assert session.result().category == SPECIAL


def test_a_groove_that_leaves_its_joint_at_an_angle_is_reported() -> None:
    controller = _controller(_straight())
    controller.run(ed.explicit)
    ref = ed.groove_refs(controller.spec)[0]
    controller.run(lambda spec: ed.split_span(spec, ref, 0, 0.5))
    controller.run(lambda spec: ed.move_point(spec, ed.PointRef(ref, 0, "end"), 0.0, -20.0))
    assert "error.planner.editor.direction" in _keys(controller.session)


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


# --------------------------------------------------------------------------------------------
# Picking and snapping


def test_hits_are_ordered_handles_first_and_follow_the_zoom_tolerance() -> None:
    spec = ed.explicit(_straight())
    near = hit_targets(spec, -198.0, -49.0, 5.0)
    assert near[0].kind == KIND_POINT
    assert near[0].handle == "start"
    assert hit_targets(spec, -198.0, -49.0, 0.5) == []
    on_line = hit_targets(spec, 0.0, -48.0, 5.0)
    assert on_line[0].kind == KIND_SEGMENT
    joint = hit_targets(spec, -199.0, 1.0, 5.0, layers={ed.LAYER_GROOVE})
    assert joint[0].kind == KIND_CONNECTOR
    tip = hit_targets(spec, -240.0, 0.0, 5.0, arrow_mm=40.0)
    assert tip[0].handle == "tip"


def test_a_repeated_click_cycles_through_overlapping_items() -> None:
    spec = ed.explicit(_straight())
    found = hit_targets(spec, -200.0, 0.0, 8.0)
    assert len(found) >= 2
    first = next_target(found, None, cycle=False)
    second = next_target(found, first, cycle=True)
    assert second is not None
    assert not second.same_item(first)
    assert next_target(found, Target(KIND_POINT), cycle=True) == found[0]
    assert next_target([], None, cycle=True) is None


def test_the_four_editor_snaps_are_independent_and_switchable() -> None:
    spec = ed.explicit(_straight())
    off = Snapping(points=False)
    assert snap_point(spec, 13.0, 27.0, off, 5.0) == snap_point(spec, 13.0, 27.0, off, 5.0)
    plain = snap_point(spec, 13.2, 27.7, off, 5.0)
    assert (plain.x, plain.y, plain.kind) == (13.2, 27.7, "")
    grid = snap_point(spec, 13.2, 27.7, Snapping(grid=True, grid_mm=5.0, points=False), 5.0)
    assert (grid.x, grid.y, grid.kind) == (15.0, 30.0, "grid")
    fine = snap_point(spec, 13.2, 27.7, Snapping(grid=True, grid_mm=1.0, points=False), 5.0)
    assert (fine.x, fine.y) == (13.0, 28.0)
    node = snap_point(spec, -198.0, -48.0, Snapping(grid=True, grid_mm=50.0), 5.0)
    assert (node.x, node.y, node.kind) == (-200.0, -50.0, "point")
    skipped = snap_point(spec, -198.0, -48.0, Snapping(points=True), 5.0, skip=[(-200.0, -50.0)])
    assert skipped.kind == ""
    angled = snap_point(
        spec,
        100.0,
        12.0,
        Snapping(points=False, angle=True, angle_step_deg=45.0),
        5.0,
        anchor=(0.0, 0.0),
    )
    assert math.degrees(math.atan2(angled.y, angled.x)) == pytest.approx(0.0)
    tangent = snap_point(
        spec,
        100.0,
        3.0,
        Snapping(points=False, tangent=True),
        5.0,
        anchor=(0.0, 0.0),
        heading_deg=0.0,
    )
    assert (tangent.x, tangent.y, tangent.kind) == (100.0, 0.0, "tangent")
    with pytest.raises(ValidationError):
        snap_point(spec, float("nan"), 0.0, Snapping(), 5.0)


def test_planner_snaps_are_not_part_of_the_editor_snap_module() -> None:
    import ast

    import slot_racing.modules.track_planner.edit_session as session_module
    import slot_racing.modules.track_planner.edit_tools as tools_module

    for module in (session_module, tools_module):
        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        used |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        used |= {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert not used & {"snap_pose", "attach_accessory", "attachment_fits", "join_pose"}


# --------------------------------------------------------------------------------------------
# Tools and history


def test_drawing_a_line_chain_adds_segments_one_undo_step_each() -> None:
    controller = _controller(replace(_straight(), slot_paths=()))
    controller.run(ed.explicit)
    controller.set_tool(TOOL_LINE)
    controller.set_layer(ed.LAYER_CENTER)
    controller.press(0, 100, TOL)
    controller.press(50, 100, TOL)
    controller.press(50, 150, TOL)
    path = controller.chain
    assert path is not None
    assert len(ed.spans_of(controller.spec, path)) == 2
    assert controller.session.undo()
    assert len(ed.spans_of(controller.spec, path)) == 1
    assert controller.session.redo()
    assert len(ed.spans_of(controller.spec, path)) == 2
    controller.cancel()
    assert not controller.drawing


def test_drawing_an_arc_needs_three_points_and_refuses_a_straight_row() -> None:
    controller = _controller(_straight())
    controller.run(ed.explicit)
    controller.set_tool(TOOL_ARC)
    controller.set_layer(ed.LAYER_CENTER)
    before = controller.spec
    controller.press(0, 100, TOL)
    controller.press(50, 100, TOL)
    controller.press(100, 100, TOL)
    assert controller.message == "error.planner.editor.arc_points"
    assert controller.spec == before
    controller.cancel()
    controller.press(0, 100, TOL)
    controller.press(50, 150, TOL)
    controller.press(100, 100, TOL)
    assert controller.message == ""
    arcs = [
        span for path in controller.spec.slot_paths for span in path.spans if span.kind == "arc"
    ]
    assert len(arcs) == 1


def test_a_bezier_chain_continues_the_tangent_of_its_predecessor() -> None:
    controller = _controller(_straight())
    controller.run(ed.explicit)
    controller.set_tool(TOOL_LINE)
    controller.set_layer(ed.LAYER_CENTER)
    controller.press(0, 100, TOL)
    controller.press(50, 100, TOL)
    controller.set_tool(TOOL_BEZIER)
    controller.press(50, 100, TOL)
    controller.press(150, 160, TOL)
    path = next(
        ed.PathRef(ed.LAYER_CENTER, index)
        for index, item in enumerate(controller.spec.slot_paths)
        if len(item.spans) == 2
    )
    assert ed.joint_report(controller.spec, path)[0][1] < 1.0


def test_dragging_moves_a_point_live_and_writes_exactly_one_undo_step() -> None:
    controller = _controller(_straight())
    controller.run(ed.explicit)
    controller.set_tool(TOOL_MOVE)
    ref = ed.groove_refs(controller.spec)[0]
    start = controller.spec
    controller.press(-200, -50, TOL)
    controller.move(-190, -40, TOL, pressed=True)
    assert controller.session.can_undo
    controller.move(-180, -30, TOL, pressed=True)
    controller.release(-180, -30, TOL)
    spans = ed.spans_of(controller.spec, ref)
    assert (spans[0].x0, spans[0].y0) == pytest.approx((-180.0, -30.0))
    assert controller.session.undo()
    assert controller.spec == start


def test_a_click_without_movement_selects_and_changes_nothing() -> None:
    controller = _controller(_straight())
    controller.run(ed.explicit)
    controller.set_tool(TOOL_SELECT)
    before = controller.spec
    controller.press(-200, -50, TOL)
    controller.release(-200, -50, TOL)
    assert controller.selection is not None
    assert controller.selection.kind == KIND_POINT
    assert controller.spec == before


def test_escape_during_a_drag_restores_the_part() -> None:
    controller = _controller(_straight())
    controller.run(ed.explicit)
    controller.set_tool(TOOL_MOVE)
    before = controller.spec
    controller.press(-200, -50, TOL)
    controller.move(-100, 0, TOL, pressed=True)
    assert controller.spec != before
    controller.cancel()
    assert controller.spec == before


def test_every_edit_tool_changes_what_it_names() -> None:
    controller = _controller(_straight())
    controller.run(ed.explicit)
    ref = ed.groove_refs(controller.spec)[0]

    def count() -> int:
        return len(ed.spans_of(controller.spec, ref))

    controller.set_tool(TOOL_ADD_POINT)
    controller.press(-100, -50, TOL)
    assert count() == 2
    controller.set_tool(TOOL_SPLIT)
    controller.press(100, -50, TOL)
    assert count() == 3
    controller.press(-100, -50, TOL)
    assert len(controller.spec.slot_paths) == 4
    controller.session.undo()
    controller.set_tool(TOOL_REMOVE_POINT)
    controller.press(-100, -50, TOL)
    assert count() == 2
    controller.set_tool(TOOL_DELETE)
    controller.press(150, -50, TOL)
    assert count() == 1
    controller.set_tool(TOOL_CONNECTOR)
    controller.press(-200, 0, TOL)
    assert controller.selection is not None
    assert controller.selection.kind == KIND_CONNECTOR


def test_undo_redo_follow_any_change_and_a_new_change_clears_redo() -> None:
    session = EditSession(_straight())
    controller = EditController(session)
    assert not session.can_undo
    controller.run(ed.explicit)
    controller.run(lambda spec: ed.set_connector(spec, 0, direction_deg=180.0, x_mm=-210.0))
    assert session.can_undo
    assert session.undo()
    assert session.can_redo
    controller.run(lambda spec: ed.set_connector(spec, 0, x_mm=-190.0))
    assert not session.can_redo
    while session.undo():
        pass
    assert session.spec == session.base
    assert not session.changed


def test_an_invalid_change_leaves_the_part_and_the_history_alone() -> None:
    controller = _controller(_straight())
    controller.run(ed.explicit)
    before = controller.spec
    assert not controller.run(lambda spec: ed.split_span(spec, ed.PathRef(ed.LAYER_GROOVE, 9), 0))
    assert controller.spec == before
    assert controller.message == "error.planner.geometry"


def test_the_saved_part_matches_the_drawn_lines_and_keeps_its_flags() -> None:
    controller = _controller(_straight())
    _engstelle(controller)
    result = controller.session.result()
    assert result.category == SPECIAL
    assert not same_geometry(result, _straight())
    assert len(ed.rail_connectors(result)) == 2
    assert result.lane_count == 2
    assert result.category != CURVE
    assert ed.validate_editing(result) == ()
    assert isinstance(as_segment(ed.spans_of(result, ed.groove_refs(result)[0])[0]), LineSegment)
