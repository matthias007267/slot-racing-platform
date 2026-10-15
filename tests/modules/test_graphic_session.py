"""Qt-free drawing: a straight, a curve and a Bézier part share one part body."""

from __future__ import annotations

import pytest

from slot_racing.modules.track_planner.graphic_session import (
    TOOL_ARC,
    TOOL_BEZIER,
    TOOL_JOINT,
    TOOL_LANE,
    TOOL_LINE,
    TOOL_PLATE,
    TOOL_SELECT,
    TOOL_START,
    GraphicSession,
)
from slot_racing.modules.track_planner.parts import (
    PATH_SLOT,
    PATH_START,
    SPAN_ARC,
    SPAN_BEZIER,
    STANDARD_STRAIGHT_ARTICLE,
    PartSpec,
    decode_geometry,
    encode_geometry,
    rails_can_join,
    same_geometry,
    standard_catalog,
)


def _free(session: GraphicSession) -> GraphicSession:
    session.snap_grid = False
    session.snap_points = False
    return session


def _plate(session: GraphicSession, corners: tuple[tuple[float, float], ...]) -> None:
    session.set_tool(TOOL_PLATE)
    for point in corners:
        session.click(point[0], point[1], tolerance_mm=6)
    session.click(corners[0][0], corners[0][1], tolerance_mm=6)


def _lane(session: GraphicSession, points: tuple[tuple[float, float], ...], tool: str) -> None:
    session.set_tool(tool)
    for point in points:
        session.click(point[0], point[1], tolerance_mm=6)
    session.finish_path()


def draw_straight(
    session: GraphicSession, length: float = 200.0, lanes: int = 2, pitch: float = 100.0
) -> None:
    """One closed plate, one straight groove per lane, and both end joints."""
    _free(session)
    outer = pitch * (lanes - 1) / 2.0 + 40.0
    _plate(
        session,
        ((-20.0, -outer), (length + 20.0, -outer), (length + 20.0, outer), (-20.0, outer)),
    )
    for index in range(lanes):
        offset = (index - (lanes - 1) / 2.0) * pitch
        _lane(session, ((0.0, offset), (length, offset)), TOOL_LANE)
    session.set_tool(TOOL_JOINT)
    session.click(0.0, 0.0, tolerance_mm=6)
    session.click(length, 0.0, tolerance_mm=6)


def draw_curve(session: GraphicSession) -> None:
    """Two arc grooves. The via point is the bulge of each arc."""
    _free(session)
    _plate(session, ((-30.0, -80.0), (220.0, -80.0), (220.0, 120.0), (-30.0, 120.0)))
    _lane(session, ((0.0, -40.0), (180.0, -40.0), (90.0, 10.0)), TOOL_ARC)
    _lane(session, ((0.0, 40.0), (180.0, 40.0), (90.0, 90.0)), TOOL_ARC)
    session.set_tool(TOOL_JOINT)
    session.click(0.0, 0.0, tolerance_mm=6)
    session.click(180.0, 0.0, tolerance_mm=6)


def draw_bezier(session: GraphicSession) -> None:
    """Two cubic grooves. The third click of each curve is the bulge."""
    _free(session)
    _plate(session, ((-30.0, -80.0), (230.0, -80.0), (230.0, 120.0), (-30.0, 120.0)))
    _lane(session, ((0.0, -40.0), (200.0, -40.0), (100.0, 20.0)), TOOL_BEZIER)
    _lane(session, ((0.0, 40.0), (200.0, 40.0), (100.0, 100.0)), TOOL_BEZIER)
    session.set_tool(TOOL_JOINT)
    session.click(0.0, 0.0, tolerance_mm=6)
    session.click(200.0, 0.0, tolerance_mm=6)


def _spec(
    session: GraphicSession, *, category: str = "straight", article: str = "EB-G"
) -> PartSpec:
    return session.compile(
        article_number=article,
        scale="1:24",
        name="Gezeichnet",
        manufacturer="Eigen",
        description="Notiz",
        category=category,
        as_draft=False,
    )


def test_a_two_lane_straight_closes_assigns_both_ends_and_roundtrips() -> None:
    session = GraphicSession()
    draw_straight(session, length=200.0, lanes=2)
    assert session.release_issues() == ()
    spec = _spec(session)
    assert spec.lane_count == 2
    assert len(spec.connectors) == 2
    assert spec.connectors[0].lanes == (1, 2)
    assert spec.connectors[1].lanes == (1, 2)
    assert rails_can_join(spec.connectors[0], spec.connectors[1])
    assert {round(spec.connectors[0].direction_deg), round(spec.connectors[1].direction_deg)} == {
        0,
        180,
    }
    assert all(path.kind == PATH_SLOT and path.spans[0].kind == "line" for path in spec.slot_paths)
    assert spec.contours and spec.contours[0].spans
    again = GraphicSession()
    again.load(spec)
    assert not again.edited
    assert again.release_issues() == ()
    stored = encode_geometry(spec)
    slots, contours, _edges = decode_geometry(stored)
    assert len(slots) == 2
    assert len(contours) == 1
    assert same_geometry(
        again.compile(
            article_number=spec.article_number,
            scale=spec.scale,
            name=spec.name,
            manufacturer=spec.manufacturer,
            description=spec.description,
            category=spec.category,
            as_draft=False,
        ),
        spec,
    )


def test_a_curve_and_a_bezier_part_keep_their_span_kinds() -> None:
    curve = GraphicSession()
    draw_curve(curve)
    assert curve.release_issues() == ()
    curve_spec = _spec(curve, category="curve", article="EB-CURVE")
    assert all(path.spans[0].kind == SPAN_ARC for path in curve_spec.slot_paths)
    assert curve_spec.radius_mm is None
    bent = GraphicSession()
    draw_bezier(bent)
    assert bent.release_issues() == ()
    bent_spec = _spec(bent, category="special", article="EB-BEZ")
    assert all(path.spans[0].kind == SPAN_BEZIER for path in bent_spec.slot_paths)
    reloaded = GraphicSession()
    reloaded.load(bent_spec)
    assert reloaded.lanes()[0].spans[0].kind == SPAN_BEZIER


def test_snap_undo_handles_and_transforms_share_one_geometry() -> None:
    session = GraphicSession()
    session.snap_points = False
    session.grid_mm = 10.0
    session.set_tool(TOOL_LINE)
    session.click(14.0, 6.0, tolerance_mm=4)
    assert session.pen_points == ((10.0, 10.0),)
    assert session.snap_target == (10.0, 10.0)
    session.click(104.0, 10.0, tolerance_mm=4)
    lane = session.lanes()[0]
    assert lane.spans[0].x1 == pytest.approx(100.0)
    session.set_tool(TOOL_SELECT)
    session.click(100.0, 10.0, tolerance_mm=6)
    session.set_metric("length", 80.0)
    assert session.lanes()[0].spans[0].x1 == pytest.approx(90.0)
    session.undo()
    assert session.lanes()[0].spans[0].x1 == pytest.approx(100.0)
    session.redo()
    assert session.lanes()[0].spans[0].x1 == pytest.approx(90.0)
    session.undo()
    session.undo()
    session.undo()
    assert len(session.elements) == 0
    assert not session.edited
    session = GraphicSession()
    draw_straight(session, length=100.0, lanes=1, pitch=100.0)
    session.set_tool(TOOL_SELECT)
    session.select_ids(frozenset(item.id for item in session.elements))
    before = session.elements
    session.rotate_selection(90.0)
    assert session.elements != before
    session.undo()
    assert session.elements == before
    session.select_ids(frozenset({session.lanes()[0].id}))
    session.mirror_selection("y")
    assert session.edited
    session.undo()
    copied = session.lanes()[0].id
    session.select_ids(frozenset({copied}))
    session.duplicate_selection()
    assert len(session.lanes()) == 2
    session.delete_selection()
    assert len(session.lanes()) == 1


def test_a_start_line_is_stored_apart_from_the_grooves() -> None:
    session = GraphicSession()
    draw_straight(session, length=120.0, lanes=2)
    session.set_tool(TOOL_START)
    session.click(40.0, -70.0, tolerance_mm=6)
    session.click(40.0, 70.0, tolerance_mm=6)
    spec = _spec(session, article="EB-START")
    assert any(path.kind == PATH_START for path in spec.slot_paths)
    assert sum(1 for path in spec.slot_paths if path.kind == PATH_SLOT) == 2


def test_an_untouched_catalogue_part_compiles_to_the_same_body() -> None:
    spec = next(
        item for item in standard_catalog() if item.article_number == STANDARD_STRAIGHT_ARTICLE
    )
    session = GraphicSession()
    session.load(spec)
    assert session.elements
    assert not session.edited
    compiled = session.compile(
        article_number=spec.article_number,
        scale=spec.scale,
        name="Neuer Name",
        manufacturer=spec.manufacturer,
        description="Nur Text",
        category=spec.category,
        as_draft=False,
    )
    assert compiled.name == "Neuer Name"
    assert compiled.description == "Nur Text"
    assert same_geometry(compiled, spec)


def test_escape_drops_only_the_open_span_and_a_locked_element_stays() -> None:
    session = GraphicSession()
    _free(session)
    session.set_tool(TOOL_PLATE)
    session.click(0.0, 0.0, tolerance_mm=6)
    assert session.pen_points
    session.cancel_action()
    assert session.pen_points == ()
    assert session.elements == ()
    draw_straight(session, length=80.0, lanes=1, pitch=100.0)
    plate = session.plate()
    assert plate is not None
    session.set_locked(plate.id, True)
    session.select_ids(frozenset({plate.id}))
    session.delete_selection()
    assert session.plate() is not None
