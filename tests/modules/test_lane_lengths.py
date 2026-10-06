"""Lane lengths follow each groove across joined parts and ignore loose ones."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QPointF
from pytestqt.qtbot import QtBot

from slot_racing.modules.track_planner.lane_length import (
    MIN_LENGTH_PARTS,
    LaneLength,
    connected_runs,
    display_lane_lengths,
    format_length_m,
    span_length_mm,
)
from slot_racing.modules.track_planner.parts import (
    LANE_PITCH_MM,
    STRAIGHT,
    TRACK,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    SlotSpan,
    curve_lane_radius,
    join_pose,
    rectangle,
    standard_catalog,
)
from slot_racing.modules.track_planner.ui.page import PlannerPage
from tests.modules.conftest import Env
from tests.modules.test_track_extend_arrows import _arrow_facing, _click, _open, _select_instance
from tests.modules.test_track_parts import _library_row


def _article(article: str) -> PartSpec:
    return next(spec for spec in standard_catalog() if spec.article_number == article)


def _chain(spec: PartSpec, count: int, part_id: int = 1) -> tuple[PartInstance, ...]:
    current = PartInstance("p0", part_id, 0.0, 0.0)
    placed = [current]
    for index in range(1, count):
        pose = join_pose(current, spec.connectors[1], spec.connectors[0])
        current = PartInstance(
            f"p{index}",
            part_id,
            pose.x_mm,
            pose.y_mm,
            rotation_z_deg=pose.rotation_z_deg,
        )
        placed.append(current)
    return tuple(placed)


def _wide(lanes: int, length: float = 100.0) -> PartSpec:
    numbers = tuple(range(1, lanes + 1))
    half = length / 2.0
    return PartSpec(
        article_number=f"W{lanes}",
        scale="1:24",
        name="Breit",
        category=STRAIGHT,
        length_mm=length,
        width_mm=lanes * LANE_PITCH_MM,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=lanes,
        connectors=(
            ConnectorSpec("a", -half, 0.0, 0.0, 180.0, TRACK, numbers),
            ConnectorSpec("b", half, 0.0, 0.0, 0.0, TRACK, numbers),
        ),
        outline=rectangle(length, lanes * LANE_PITCH_MM),
    )


def _rows(spec: PartSpec, count: int, part_id: int = 1) -> tuple[LaneLength, ...]:
    catalog = {part_id: spec}
    return display_lane_lengths(_chain(spec, count, part_id), catalog)


def test_an_arc_length_is_radius_times_radians() -> None:
    span = SlotSpan.arc(0.0, 0.0, 350.0, -30.0, 60.0)
    assert span_length_mm(span) == pytest.approx(350.0 * math.radians(60.0))
    assert span_length_mm(SlotSpan.line(0.0, 0.0, 345.0, 0.0)) == pytest.approx(345.0)


def test_four_straights_report_each_lane_from_the_groove() -> None:
    spec = _article("20020601")
    assert spec.length_mm is not None
    rows = _rows(spec, 4)
    assert [row.lane for row in rows] == [1, 2]
    assert rows[0].length_mm == pytest.approx(4 * spec.length_mm)
    assert rows[1].length_mm == pytest.approx(rows[0].length_mm)
    assert _rows(spec, 3) == ()
    assert MIN_LENGTH_PARTS == 4


def test_more_than_four_lanes_are_measured_individually() -> None:
    spec = _wide(6)
    rows = _rows(spec, 4)
    assert [row.lane for row in rows] == [1, 2, 3, 4, 5, 6]
    assert [row.length_mm for row in rows] == pytest.approx([400.0] * 6)


def test_inner_and_outer_curve_lanes_differ() -> None:
    straight = _article("20020601")
    curve = _article("20020571")
    assert straight.length_mm is not None
    assert curve.radius_mm is not None and curve.angle_deg is not None
    catalog = {1: straight, 2: curve}
    placed: list[PartInstance] = [PartInstance("s0", 1, 0.0, 0.0)]
    sequence = (2, 1, 2)
    for index, part_id in enumerate(sequence, start=1):
        host = placed[-1]
        host_spec = catalog[host.part_id]
        spec = catalog[part_id]
        pose = join_pose(host, host_spec.connectors[1], spec.connectors[0])
        placed.append(
            PartInstance(
                f"n{index}",
                part_id,
                pose.x_mm,
                pose.y_mm,
                rotation_z_deg=pose.rotation_z_deg,
            )
        )
    rows = display_lane_lengths(placed, catalog)
    outer = curve_lane_radius(curve.radius_mm, 0, 2)
    inner = curve_lane_radius(curve.radius_mm, 1, 2)
    arc = math.radians(curve.angle_deg)
    assert outer > inner
    assert rows[0].length_mm == pytest.approx(2 * straight.length_mm + 2 * outer * arc)
    assert rows[1].length_mm == pytest.approx(2 * straight.length_mm + 2 * inner * arc)
    assert rows[0].length_mm > rows[1].length_mm


def test_a_loose_part_is_not_added_and_a_loop_is_walked_once() -> None:
    straight = _article("20020601")
    assert straight.length_mm is not None
    chain = _chain(straight, 4)
    loose = PartInstance("loose", 1, 5000.0, 5000.0)
    catalog = {1: straight}
    with_loose = display_lane_lengths((*chain, loose), catalog)
    without = display_lane_lengths(chain, catalog)
    assert with_loose == without
    runs = connected_runs((*chain, loose), catalog)
    assert sorted(run.part_count for run in runs) == [1, 4]

    curve = _article("20020571")
    assert curve.radius_mm is not None and curve.angle_deg is not None
    loop = _chain(curve, 6, part_id=2)
    loop_rows = display_lane_lengths(loop, {2: curve})
    outer = curve_lane_radius(curve.radius_mm, 0, 2) * math.tau
    inner = curve_lane_radius(curve.radius_mm, 1, 2) * math.tau
    assert [row.lane for row in loop_rows] == [1, 2]
    assert loop_rows[0].length_mm == pytest.approx(outer)
    assert loop_rows[1].length_mm == pytest.approx(inner)
    assert connected_runs(loop, {2: curve})[0].part_count == 6


def test_a_lane_change_does_not_invent_one_route() -> None:
    straight = _article("20020601")
    change = _article("20030343")
    catalog = {1: straight, 2: change}
    placed = list(_chain(straight, 3))
    pose = join_pose(placed[-1], straight.connectors[1], change.connectors[0])
    placed.append(PartInstance("x", 2, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg))
    assert display_lane_lengths(placed, catalog) == ()
    assert connected_runs(placed, catalog)[0].part_count == 4


def test_lengths_are_rounded_only_when_formatted() -> None:
    assert format_length_m(4.0) == "0,00 m"
    assert format_length_m(8.0) == "0,01 m"
    assert format_length_m(1380.0) == "1,38 m"
    assert format_length_m(4200.0) == "4,20 m"


def test_rotation_that_breaks_a_joint_drops_the_length() -> None:
    spec = _article("20020601")
    chain = list(_chain(spec, 4))
    catalog = {1: spec}
    before = display_lane_lengths(chain, catalog)
    chain[3] = PartInstance(chain[3].id, 1, chain[3].x_mm, chain[3].y_mm, rotation_z_deg=90.0)
    assert display_lane_lengths(chain, catalog) == ()
    assert before


def test_the_length_panel_follows_extend_undo_redo_and_load(qtbot: QtBot, env: Env) -> None:
    page = _open(qtbot, env, "Laenge")
    assert isinstance(page, PlannerPage)
    assert page.length_title.isHidden()
    page.library.setCurrentRow(_library_row(page, "20020601"))
    page.place_part.click()
    straight = page.plan().instances[-1]
    spec = page._parts[straight.part_id]
    assert spec.length_mm is not None
    for _step in range(3):
        arrow = _arrow_facing(page, "straight", 0)
        _click(qtbot, page, arrow)
    shown = page.length_body.text()
    assert not page.length_title.isHidden()
    assert "Spur 1" in shown and "Spur 2" in shown
    assert format_length_m(4 * spec.length_mm) in shown
    assert page.length_body.text() == shown

    page.canvas.zoom_at(QPointF(20, 20), 240)
    assert page.length_body.text() == shown

    page.undo()
    assert page.length_title.isHidden()
    page.redo()
    assert page.length_body.text() == shown

    page.save()
    loaded = page._planner.load(page.plan().track_id)
    assert display_lane_lengths(loaded.instances, page._parts) == display_lane_lengths(
        page.plan().instances, page._parts
    )

    _select_instance(page, page.plan().instances[-1].id)
    page.delete_selected()
    assert page.length_title.isHidden()
    page.undo()
    assert page.length_body.text() == shown
