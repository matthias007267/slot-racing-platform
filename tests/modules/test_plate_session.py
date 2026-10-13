"""Floor-plate session: clicks become geometry, and a sketch is not a rail."""

from __future__ import annotations

import math

import pytest

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner.document import empty_plan, place_instance
from slot_racing.modules.track_planner.figure import track_figure
from slot_racing.modules.track_planner.parts import driving_paths, same_geometry
from slot_racing.modules.track_planner.plate_session import (
    PlateSession,
    plate_corners,
    stored_plate,
)
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _planner

_RECT = ((0.0, 0.0), (345.0, 0.0), (345.0, 200.0), (0.0, 200.0))
_BOWTIE = ((0.0, 0.0), (100.0, 100.0), (0.0, 100.0), (100.0, 0.0))


def _rect() -> PlateSession:
    session = PlateSession()
    for point in _RECT:
        assert session.add_point(*point)
    assert session.close()
    return session


def test_four_clicks_close_a_rectangle() -> None:
    session = _rect()
    assert session.points == _RECT
    assert session.closed
    assert session.save_issue() is None
    spec = stored_plate(
        session.points,
        article_number="EB-R",
        scale="1:24",
        name="Platte",
        manufacturer="",
        description="",
    )
    assert plate_corners(spec) == _RECT
    assert spec.connectors == ()
    assert spec.slot_paths == ()
    assert driving_paths(spec) == ()
    assert track_figure(spec).slots == ()
    assert track_figure(spec).roadway == _RECT


def test_hover_is_not_stored() -> None:
    session = PlateSession()
    assert session.add_point(0.0, 0.0)
    session.set_hover((40.0, 15.0))
    assert session.hover == (40.0, 15.0)
    assert session.points == ((0.0, 0.0),)
    assert session.geometry_token() == ((((0.0, 0.0),), False))
    session.set_hover(None)
    assert session.hover is None
    assert session.points == ((0.0, 0.0),)


def test_numeric_move_and_undo_restore_the_corner() -> None:
    session = _rect()
    session.select(1)
    assert session.commit_selected(300.0, 0.0)
    assert session.points[1] == (300.0, 0.0)
    assert session.undo()
    assert session.points[1] == (345.0, 0.0)
    assert session.redo()
    assert session.points[1] == (300.0, 0.0)


def test_invalid_plates_are_refused() -> None:
    short = PlateSession()
    assert short.add_point(0.0, 0.0)
    assert short.add_point(10.0, 0.0)
    assert not short.close()
    assert short.flash == "error.planner.plate.short"
    assert short.save_issue() == "error.planner.plate.short"
    zero = PlateSession()
    assert zero.add_point(0.0, 0.0)
    assert not zero.add_point(0.0, 0.0)
    assert zero.flash == "error.planner.plate.zero"
    assert zero.points == ((0.0, 0.0),)
    nan = PlateSession()
    assert not nan.add_point(math.nan, 0.0)
    assert nan.flash == "error.planner.plate.coords"
    assert not nan.add_point(math.inf, 1.0)
    open_chain = PlateSession()
    for point in _RECT:
        assert open_chain.add_point(*point)
    assert open_chain.save_issue() == "error.planner.plate.open"
    bow = PlateSession()
    for point in _BOWTIE:
        assert bow.add_point(*point)
    assert not bow.close()
    assert bow.flash == "error.planner.plate.crossing"
    flat = PlateSession()
    for point in ((0.0, 0.0), (30.0, 0.0), (60.0, 0.0)):
        assert flat.add_point(*point)
    assert not flat.close()
    assert flat.flash == "error.planner.plate.degenerate"
    for points in (_BOWTIE, ((0.0, 0.0), (30.0, 0.0), (60.0, 0.0)), _RECT[:2]):
        with pytest.raises(ValidationError):
            stored_plate(
                points,
                article_number="EB-X",
                scale="1:24",
                name="Nein",
                manufacturer="",
                description="",
            )


def test_undo_and_redo_replay_the_plate() -> None:
    session = PlateSession()
    assert session.add_point(0.0, 0.0)
    assert session.add_point(10.0, 0.0)
    assert session.add_point(10.0, 8.0)
    assert session.close()
    session.select(0)
    assert session.commit_selected(1.0, 1.0)
    session.replace()
    emptied = session.points
    assert emptied == ()
    assert session.undo()
    assert session.closed
    restored = session.points
    assert restored[0] == (1.0, 1.0)
    assert session.undo()
    reopened = session.points
    assert reopened[0] == (0.0, 0.0)
    assert session.closed
    assert session.undo()
    assert not session.closed
    assert len(session.points) == 3
    assert session.undo()
    assert len(session.points) == 2
    assert session.selected is None or session.selected < len(session.points)
    assert session.redo()
    assert len(session.points) == 3
    assert session.redo()
    assert session.closed
    assert session.points[0] == (0.0, 0.0)


def test_a_closed_plate_cannot_grow_a_second_contour() -> None:
    session = _rect()
    assert not session.add_point(10.0, 10.0)
    assert session.flash == "error.planner.plate.exists"
    before = session.points
    assert before == _RECT
    session.replace()
    emptied = session.points
    assert emptied == ()
    assert not session.closed
    assert session.undo()
    assert session.points == _RECT
    assert session.closed


def test_dragging_a_corner_is_one_undo_step() -> None:
    session = _rect()
    session.begin_move(0)
    session.preview_move(-5.0, 4.0)
    assert session.points[0] == (0.0, 0.0)
    assert session.display_points()[0] == (-5.0, 4.0)
    session.commit_move()
    assert session.points[0] == (-5.0, 4.0)
    assert session.undo()
    assert session.points[0] == (0.0, 0.0)


def test_hit_tests_follow_millimetres_not_the_widget() -> None:
    session = _rect()
    assert session.hit(0.0, 0.0, 2.0) == 0
    assert session.hit(345.0, 200.0, 2.0) == 2
    assert session.hit(0.0, 200.0, 2.0) == 3
    assert session.hit(10.0, 10.0, 2.0) is None
    assert session.hit(0.0, 0.0, 2.0) == 0


def test_a_plate_draft_cannot_be_placed(env: Env) -> None:
    planner = _planner(env)
    spec = stored_plate(
        _RECT,
        article_number="EB-PLACE",
        scale="1:24",
        name="Nicht fahrbar",
        manufacturer="",
        description="",
    )
    record = planner.add_part(spec)
    again = plate_corners(planner.library.require(record.id).spec)
    assert again == _RECT
    assert same_geometry(planner.library.require(record.id).spec, record.spec)
    with pytest.raises(ValidationError):
        place_instance(
            empty_plan(env.track("Entwurf", lanes=2).id),
            record.id,
            record.spec,
            0,
            0,
            0,
            {record.id: record.spec},
        )
