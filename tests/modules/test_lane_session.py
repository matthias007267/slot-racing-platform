"""Driving lanes stay on the same undo stack as the floor plate."""

from __future__ import annotations

import math

import pytest

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.editing import arc_between, spans_length_mm
from slot_racing.modules.track_planner.parts import (
    SPAN_ARC,
    SPAN_LINE,
    driving_paths,
    same_geometry,
)
from slot_racing.modules.track_planner.plate_session import (
    PlateSession,
    draft_lanes,
    plate_corners,
    stored_sketch,
)
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner

_RECT = ((0.0, 0.0), (345.0, 0.0), (345.0, 200.0), (0.0, 200.0))


def _plate() -> PlateSession:
    session = PlateSession()
    for point in _RECT:
        assert session.add_point(*point)
    assert session.close()
    return session


def _lane(session: PlateSession, points: tuple[tuple[float, float], ...]) -> int:
    assert session.add_lane()
    index = session.active_lane
    assert index is not None
    for point in points:
        assert session.add_lane_point(*point)
    assert session.finish_lane()
    return index


def test_three_clicks_finish_one_open_lane() -> None:
    session = _plate()
    index = _lane(session, ((20.0, 40.0), (180.0, 40.0), (180.0, 120.0)))
    spans = session.lane_spans(index)
    assert session.lane_finished(index)
    assert len(spans) == 2
    assert all(span.kind == SPAN_LINE for span in spans)
    assert (spans[0].x0, spans[0].y0, spans[0].x1, spans[0].y1) == (20.0, 40.0, 180.0, 40.0)
    assert (spans[1].x0, spans[1].y0, spans[1].x1, spans[1].y1) == (180.0, 40.0, 180.0, 120.0)
    assert session.hover is None


def test_two_lanes_keep_independent_points() -> None:
    session = _plate()
    first = _lane(session, ((10.0, 20.0), (80.0, 20.0), (80.0, 70.0)))
    second = _lane(session, ((30.0, 100.0), (200.0, 110.0), (210.0, 160.0)))
    assert first == 0 and second == 1
    assert session.lane_vertices(0)[0] == (10.0, 20.0)
    assert session.lane_vertices(1)[0] == (30.0, 100.0)
    assert session.lane_vertices(0) != session.lane_vertices(1)


def test_a_line_becomes_an_arc_with_the_same_ends() -> None:
    session = _plate()
    index = _lane(session, ((0.0, 0.0), (100.0, 0.0), (100.0, 80.0)))
    session.select_lane_span(index, 0)
    assert session.convert_to_arc()
    span = session.lane_spans(index)[0]
    assert span.kind == SPAN_ARC
    assert span.sweep_deg > 0.0
    session.set_arc(80.0, right=False, long=False)
    span = session.lane_spans(index)[0]
    assert span.radius_mm == pytest.approx(80.0)
    assert span.sweep_deg < 0.0
    assert abs(span.sweep_deg) < 180.0
    start = (span.x0 + span.radius_mm * math.cos(math.radians(span.start_deg)),)
    # Endpoints stay the chord. The following segment still begins there.
    following = session.lane_spans(index)[1]
    assert following.kind == SPAN_LINE
    assert (following.x0, following.y0) == (100.0, 0.0)
    assert session.lane_length_mm(index) == pytest.approx(
        spans_length_mm(session.lane_spans(index))
    )
    assert start  # the centre formula is exercised below
    built = arc_between((0.0, 0.0), (100.0, 0.0), 80.0, right=False, long=False)
    assert built is not None
    assert span.radius_mm == built.radius_mm
    assert span.sweep_deg == pytest.approx(built.sweep_deg)
    length = abs(span.radius_mm * math.radians(span.sweep_deg))
    assert session.segment_length_mm() == pytest.approx(length)


def test_a_radius_below_the_chord_is_refused() -> None:
    session = _plate()
    index = _lane(session, ((0.0, 0.0), (100.0, 0.0)))
    session.select_lane_span(index, 0)
    assert session.convert_to_arc()
    before = session.lane_spans(index)[0]
    assert not session.set_arc(40.0, right=True, long=False)
    assert session.flash == "error.planner.lane.radius"
    assert session.lane_spans(index)[0] == before


def test_moving_a_lane_point_updates_both_segments_and_undo() -> None:
    session = _plate()
    plate = session.points
    index = _lane(session, ((10.0, 10.0), (40.0, 10.0), (40.0, 50.0)))
    session.select_lane_point(index, 1)
    assert session.commit_selected(55.0, 12.0)
    spans = session.lane_spans(index)
    assert (spans[0].x1, spans[0].y1) == (55.0, 12.0)
    assert (spans[1].x0, spans[1].y0) == (55.0, 12.0)
    assert session.undo()
    assert session.lane_vertices(index)[1] == (40.0, 10.0)
    assert session.points == plate
    assert session.redo()
    assert session.lane_vertices(index)[1] == (55.0, 12.0)
    assert session.points == plate


def test_an_arc_point_move_that_breaks_the_radius_is_kept() -> None:
    session = _plate()
    index = _lane(session, ((0.0, 50.0), (100.0, 50.0), (160.0, 50.0)))
    session.select_lane_span(index, 0)
    assert session.convert_to_arc()
    assert session.set_arc(60.0, right=True, long=False)
    before = session.lane_spans(index)
    session.select_lane_point(index, 1)
    assert not session.commit_selected(400.0, 50.0)
    assert session.flash == "error.planner.lane.radius"
    assert session.lane_spans(index) == before
    assert session.lane_spans(index)[0].kind == SPAN_ARC


def test_lengths_of_line_quarter_half_and_a_chain() -> None:
    radius = 100.0
    quarter = arc_between((radius, 0.0), (0.0, radius), radius, right=True, long=False)
    half = arc_between((radius, 0.0), (-radius, 0.0), radius, right=True, long=False)
    assert quarter is not None and half is not None
    assert quarter.sweep_deg == pytest.approx(90.0)
    assert half.sweep_deg == pytest.approx(180.0)
    assert spans_length_mm((quarter,)) == pytest.approx(radius * math.pi / 2.0)
    assert spans_length_mm((half,)) == pytest.approx(radius * math.pi)
    session = _plate()
    index = _lane(session, ((0.0, 0.0), (100.0, 0.0), (100.0, 40.0)))
    line = spans_length_mm(session.lane_spans(index))
    assert line == pytest.approx(140.0)
    session.select_lane_span(index, 1)
    assert session.convert_to_arc()
    assert session.lane_length_mm(index) != pytest.approx(line)


def test_undo_restores_lane_edits_without_touching_the_plate() -> None:
    session = _plate()
    plate = session.points
    _lane(session, ((10.0, 10.0), (30.0, 10.0), (30.0, 40.0)))
    session.select_lane_span(0, 0)
    assert session.convert_to_arc()
    assert session.set_arc(40.0, right=False, long=False)
    session.select_lane_point(0, 2)
    assert session.commit_selected(36.0, 48.0)
    assert session.remove_lane()
    assert session.lanes == ()
    assert session.undo()
    assert len(session.lanes) == 1
    assert session.undo()
    assert session.lane_vertices(0)[2] == (30.0, 40.0)
    assert session.undo()
    assert session.lane_spans(0)[0].radius_mm == pytest.approx(20.0)
    assert session.lane_spans(0)[0].sweep_deg > 0.0
    assert session.undo()
    assert session.lane_spans(0)[0].kind == SPAN_LINE
    assert session.points == plate


def test_a_sketch_roundtrip_keeps_the_arc(env: Env) -> None:
    session = _plate()
    _lane(session, ((20.0, 30.0), (140.0, 30.0), (140.0, 90.0)))
    _lane(session, ((40.0, 120.0), (200.0, 150.0), (80.0, 170.0)))
    session.select_lane_span(0, 0)
    assert session.convert_to_arc()
    assert session.set_arc(90.0, right=True, long=False)
    spec = stored_sketch(
        session.points,
        session.sketch_lanes(),
        article_number="EB-LANE",
        scale="1:24",
        name="Zwei Spuren",
        manufacturer="",
        description="",
    )
    assert spec.connectors == ()
    assert len(driving_paths(spec)) == 2
    record = _planner(env).add_part(spec)
    loaded = _planner(env).library.require(record.id).spec
    assert plate_corners(loaded) == _RECT
    assert draft_lanes(loaded) == session.sketch_lanes()
    assert same_geometry(loaded, record.spec)
    assert loaded.slot_paths[0].spans[0].kind == SPAN_ARC


def test_four_lanes_are_the_limit_and_an_open_lane_is_not_stored() -> None:
    session = _plate()
    for lane in range(4):
        offset = 20.0 + lane * 30.0
        _lane(session, ((10.0, offset), (40.0, offset)))
    assert not session.add_lane()
    assert session.flash == "error.planner.lane.limit"
    assert session.add_lane() is False
    open_session = _plate()
    assert open_session.add_lane()
    assert open_session.add_lane_point(10.0, 10.0)
    assert open_session.add_lane_point(40.0, 10.0)
    assert open_session.save_issue() == "error.planner.lane.open"
    with pytest.raises(ValidationError):
        stored_sketch(
            ((0.0, 0.0), (10.0, 0.0)),
            (),
            article_number="EB-NO",
            scale="1:24",
            name="Offen",
            manufacturer="",
            description="",
        )


def test_insert_and_remove_apply_to_lines_only() -> None:
    session = _plate()
    _lane(session, ((10.0, 10.0), (50.0, 10.0), (50.0, 40.0)))
    session.select_lane_span(0, 0)
    assert session.insert_point()
    assert len(session.lane_spans(0)) == 3
    assert session.lane_vertices(0)[1] == (30.0, 10.0)
    session.select_lane_point(0, 1)
    assert session.remove_lane_point()
    assert len(session.lane_spans(0)) == 2
    session.select_lane_span(0, 0)
    assert session.convert_to_arc()
    session.select_lane_point(0, 1)
    assert not session.can_remove_lane_point()
    assert not session.can_insert_point()


def test_a_lane_outside_the_plate_is_not_a_drivable_part(env: Env) -> None:
    session = _plate()
    _lane(session, ((-30.0, 40.0), (80.0, 40.0)))
    assert session.warning_key() == "error.planner.lane.outside"
    assert session.save_issue() is None
    spec = stored_sketch(
        session.points,
        session.sketch_lanes(),
        article_number="EB-OUT",
        scale="1:24",
        name="Ausserhalb",
        manufacturer="",
        description="",
    )
    planner = _planner(env)
    record = planner.add_part(spec)
    track = env.track("Aussen", lanes=2)
    with pytest.raises(ValidationError):
        place_instance(
            empty_plan(track.id),
            record.id,
            record.spec,
            10.0,
            10.0,
            0.0,
            {record.id: record.spec},
        )


def test_other_lane_distance_is_point_to_path() -> None:
    session = _plate()
    _lane(session, ((0.0, 0.0), (100.0, 0.0)))
    _lane(session, ((0.0, 25.0), (100.0, 25.0)))
    session.select_lane_point(1, 0)
    assert session.point_to_other_lanes_mm() == pytest.approx(25.0)
    assert session.point_to_plate_edge_mm() == pytest.approx(0.0)
