"""Rail drawing: roadway, slots, centre line, colour coding and specials."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import QGraphicsScene
from pytestqt.qtbot import QtBot

from slot_racing.app.runtime import Runtime
from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.config import AppConfig, load_config
from slot_racing.core.config.models import TRACK_PLANNER_COLOR_CODING_DEFAULT
from slot_racing.modules.track_planner.appearance import (
    CENTER_LINE,
    CODED,
    ROADWAY,
    SLOT_CORE,
    START_LIGHT,
    curve_class,
    roadway_fill,
)
from slot_racing.modules.track_planner.figure import TrackFigure, track_figure
from slot_racing.modules.track_planner.parts import (
    CROSSING,
    LANE_CHANGE,
    LANE_PITCH_MM,
    PITLANE,
    SPAN_ARC,
    SPAN_LINE,
    STRAIGHT,
    SWITCH,
    PartInstance,
    PartSpec,
    SlotPath,
    build_part,
    curve_lane_radius,
    join_pose,
    pit_end,
    rotate_xy,
    span_point,
    standard_catalog,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.canvas import MM, InstanceItem
from slot_racing.modules.track_planner.ui.library_view import PartPreview
from slot_racing.modules.track_planner.ui.page import PlannerPage
from slot_racing.modules.track_planner.ui.track_paint import _drawn, paint_part
from tests.database import migrated_database
from tests.modules.conftest import Env
from tests.modules.test_track_parts import _library_row, _planner, _select
from tests.modules.test_ui_management import open_page

_R1 = 300.0
_R2 = 500.0
_R3 = 700.0
_R4 = 900.0


def _catalog(article: str) -> PartSpec:
    return next(spec for spec in standard_catalog() if spec.article_number == article)


def _straight(length: float = 345.0) -> PartSpec:
    return build_part(
        article_number="EB-DRAW",
        scale="1:32",
        name="Gerade",
        category=STRAIGHT,
        length_mm=length,
        width_mm=None,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=2,
    )


def test_a_straight_has_a_road_two_slots_and_a_centre_line() -> None:
    spec = _straight()
    figure = track_figure(spec)
    assert figure.road_arc is None
    assert len(figure.roadway) == 4
    assert len(figure.slots) == 2
    assert all(span.kind == SPAN_LINE for path in figure.slots for span in path.spans)
    ys = sorted(span_point(path.spans[0], 0.0)[1] for path in figure.slots)
    assert ys == pytest.approx([-LANE_PITCH_MM / 2, LANE_PITCH_MM / 2])
    assert figure.centerlines
    assert all(path.kind == "center" for path in figure.centerlines)
    dash = figure.centerlines[0].spans[0]
    assert dash.kind == SPAN_LINE
    assert abs(span_point(dash, 0.5)[1]) < 0.01
    # Both ends of the centre pattern stay off the joint.
    length = spec.length_mm
    assert length is not None
    first = min(span.x0 for span in figure.centerlines[0].spans)
    last = max(span.x1 for span in figure.centerlines[0].spans)
    assert first > -length / 2
    assert last < length / 2
    assert figure.outer_shoulder == ()
    assert figure.inner_shoulder == ()


def test_curves_follow_their_radius_and_the_slots_are_arcs() -> None:
    expected = {
        "20020577": (_R1, 30.0),
        "20020571": (_R1, 60.0),
        "20020572": (_R2, 30.0),
        "20020573": (_R3, 30.0),
        "20020578": (_R4, 15.0),
    }
    for article, (radius, angle) in expected.items():
        spec = _catalog(article)
        figure = track_figure(spec)
        assert figure.road_arc is not None
        assert figure.road_arc.radius_mm == pytest.approx(radius)
        assert figure.road_arc.angle_deg == pytest.approx(angle)
        assert len(figure.slots) == 2
        radii = sorted(path.spans[0].radius_mm for path in figure.slots)
        assert radii == pytest.approx(
            [curve_lane_radius(radius, 1, 2), curve_lane_radius(radius, 0, 2)]
        )
        for path in figure.slots:
            span = path.spans[0]
            assert span.kind == SPAN_ARC
            assert span.sweep_deg == pytest.approx(angle)
            mid = span_point(span, 0.5)
            start = span_point(span, 0.0)
            end = span_point(span, 1.0)
            chord = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
            assert math.hypot(*mid) == pytest.approx(span.radius_mm)
            assert math.hypot(*mid) > math.hypot(*chord) + 1.0
        assert figure.centerlines
        assert all(span.kind == SPAN_ARC for path in figure.centerlines for span in path.spans)


def test_a_curve_slot_meets_the_straight_it_is_joined_to() -> None:
    straight = _catalog("20020601")
    curve = _catalog("20020571")
    host = PartInstance("host", 1, 0.0, 0.0)
    pose = join_pose(host, straight.connectors[1], curve.connectors[0])
    guest = PartInstance("guest", 2, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg)
    curve_slots = track_figure(curve).slots
    for index, lane in enumerate((1, 2)):
        span = curve_slots[index].spans[0]
        local = span_point(span, 0.0)
        turned = rotate_xy(*local, guest.rotation_z_deg)
        world = (guest.x_mm + turned[0], guest.y_mm + turned[1])
        offset = (lane - 1.5) * LANE_PITCH_MM
        assert straight.length_mm is not None
        assert world[0] == pytest.approx(straight.length_mm / 2, abs=0.05)
        assert world[1] == pytest.approx(offset, abs=0.05)


def test_rotation_and_scale_move_the_slot_without_changing_the_part(qtbot: QtBot) -> None:
    spec = _catalog("20020577")
    span = track_figure(spec).slots[0].spans[0]
    mid = span_point(span, 0.5)
    turned = rotate_xy(*mid, 90.0)
    assert turned[0] == pytest.approx(0.0, abs=0.05)
    assert turned[1] == pytest.approx(span.radius_mm)
    assert (turned[0] * 2.0, turned[1] * 2.0) == pytest.approx((0.0, span.radius_mm * 2.0))
    item = InstanceItem(
        PartInstance("a", 1, 10.0, 20.0),
        spec,
        lambda _item_id, _moves: None,
    )
    item.setRotation(90.0)
    scene = QGraphicsScene()
    scene.addItem(item)
    mapped = item.mapToScene(QPointF(mid[0] * MM, mid[1] * MM))
    assert mapped.x() == pytest.approx(10.0 * MM, abs=0.05)
    assert mapped.y() == pytest.approx(20.0 * MM + span.radius_mm * MM, abs=0.05)
    assert item.spec is spec
    assert item.spec.outline is spec.outline


def test_the_renderer_does_not_change_the_model(qtbot: QtBot) -> None:
    spec = _straight()
    outline = spec.outline
    connectors = spec.connectors
    paths = spec.slot_paths
    figure = track_figure(spec)
    _render(spec, color_coding=True, start_straight=True)
    assert spec.outline is outline
    assert spec.connectors is connectors
    assert spec.slot_paths is paths
    assert figure.slots
    assert spec.category == STRAIGHT


def test_the_same_definition_reuses_its_figure() -> None:
    spec = _straight()
    assert track_figure(spec) is track_figure(spec)
    longer = _straight(200.0)
    assert track_figure(longer) is not track_figure(spec)


def test_a_curve_is_filled_as_an_arc_not_as_the_outline_polygon(qtbot: QtBot) -> None:
    spec = _catalog("20020571")
    figure = track_figure(spec)
    assert figure.road_arc is not None
    path = _drawn(figure).roadway
    assert _has_curve(path)
    radius = figure.road_arc.outer_mm
    angle = math.radians(-3.75)
    inside = QPointF((radius - 0.4) * math.cos(angle), (radius - 0.4) * math.sin(angle))
    outside = QPointF((radius + 2.0) * math.cos(angle), (radius + 2.0) * math.sin(angle))
    assert path.contains(inside)
    assert not path.contains(outside)
    polygon = QPolygonF([QPointF(x, y) for x, y in spec.outline])
    assert not polygon.containsPoint(inside, Qt.FillRule.OddEvenFill)


def test_colour_classes_follow_radius_and_category() -> None:
    assert curve_class(_R1) == "r1"
    assert curve_class(_R2) == "r2"
    assert curve_class(_R3) == "r3"
    assert curve_class(_R4) == "r4"
    assert curve_class(450.0) == "curve"
    straight = _straight()
    assert roadway_fill(straight, coded=False) == ROADWAY
    assert roadway_fill(straight, coded=True) == CODED["straight"]
    assert roadway_fill(_catalog("20020577"), coded=True) == CODED["r1"]
    assert roadway_fill(_catalog("20020572"), coded=True) == CODED["r2"]
    assert roadway_fill(_catalog("20020573"), coded=True) == CODED["r3"]
    assert roadway_fill(_catalog("20020578"), coded=True) == CODED["r4"]
    assert roadway_fill(_catalog("20020517"), coded=True) == CODED["switch"]
    assert roadway_fill(_catalog("20030343"), coded=True) == CODED["lane_change"]
    assert roadway_fill(_catalog("20020587"), coded=True) == CODED["crossing"]
    assert roadway_fill(_catalog("20030341"), coded=True) == CODED["pitlane"]
    assert roadway_fill(_catalog("20020574"), coded=True) == CODED["special"]
    assert roadway_fill(_catalog("20020577"), coded=False) == ROADWAY


def test_colour_coding_keeps_slots_and_the_centre_line(qtbot: QtBot) -> None:
    spec = _straight()
    plain = _render(spec, color_coding=False, start_straight=False)
    coded = _render(spec, color_coding=True, start_straight=False)
    road = _pixel(plain, 80.0, -25.0)
    coded_road = _pixel(coded, 80.0, -25.0)
    assert road.name() != coded_road.name()
    assert coded_road.red() > road.red() + 40
    for image in (plain, coded):
        slot = _pixel(image, 80.0, -LANE_PITCH_MM / 2)
        assert slot.red() < 40
        dash = track_figure(spec).centerlines[0].spans[0]
        center = _pixel(image, span_point(dash, 0.5)[0], span_point(dash, 0.5)[1])
        assert center.red() > 180


def test_the_start_line_is_drawn_on_top_of_the_road(qtbot: QtBot) -> None:
    spec = _straight()
    plain = _render(spec, color_coding=False, start_straight=False)
    marked = _render(spec, color_coding=True, start_straight=True)
    road = _pixel(plain, 0.0, -90.0)
    start = _pixel(marked, 0.0, -90.0)
    assert start.red() > road.red() + 150
    assert QColor(START_LIGHT).red() - start.red() < 8
    # The grooves on the rest of the straight are still there.
    slot = _pixel(marked, 80.0, -LANE_PITCH_MM / 2)
    assert slot.red() < 40


def test_a_switch_uses_its_branch_and_a_crossing_crosses(qtbot: QtBot) -> None:
    switch = _catalog("20020517")
    assert switch.category == SWITCH
    branch = _branch(track_figure(switch))
    start = span_point(branch.spans[0], 0.0)
    end = span_point(branch.spans[-1], 1.0)
    assert start[1] == pytest.approx(LANE_PITCH_MM / 2)
    assert end[1] == pytest.approx(-LANE_PITCH_MM / 2)
    assert any(span.kind == SPAN_ARC for span in branch.spans)
    left = _catalog("20030343")
    right = _catalog("20030345")
    assert left.category == LANE_CHANGE and right.category == LANE_CHANGE
    assert _branch_end_y(left) == pytest.approx(-LANE_PITCH_MM / 2)
    assert _branch_end_y(right) == pytest.approx(LANE_PITCH_MM / 2)
    both = track_figure(_catalog("20030347"))
    assert len([path for path in both.slots if _moves(path)]) == 2
    crossing = track_figure(_catalog("20020587"))
    assert _catalog("20020587").category == CROSSING
    horizontal = [path for path in crossing.slots if _horizontal(path)]
    vertical = [path for path in crossing.slots if not _horizontal(path)]
    assert len(horizontal) == 2
    assert len(vertical) == 2
    assert crossing.centerlines


def test_pit_entry_and_exit_keep_their_identity_and_draw_a_spur() -> None:
    entry = pit_end("20030356-E", "Pitlane-Einfahrt", pit_side=1.0)
    exit_part = pit_end("20030356-A", "Pitlane-Ausfahrt", pit_side=-1.0)
    assert entry.article_number == "20030356-E"
    assert exit_part.article_number == "20030356-A"
    assert entry.slot_paths == ()
    for spec, side in ((entry, 1.0), (exit_part, -1.0)):
        figure = track_figure(spec)
        assert len(figure.slots) == 3
        spur = _branch(figure)
        end = span_point(spur.spans[-1], 1.0)
        pit = next(joint for joint in spec.connectors if joint.name == "pit")
        assert end[0] == pytest.approx(pit.x_mm, abs=0.05)
        assert end[1] == pytest.approx(pit.y_mm, abs=0.05)
        assert math.copysign(1.0, end[1]) == side
    pit_straight = _catalog("20030341")
    assert pit_straight.category == PITLANE and pit_straight.lane_count == 1
    pit_figure = track_figure(pit_straight)
    assert len(pit_figure.slots) == 1
    assert pit_figure.centerlines == ()
    assert span_point(pit_figure.slots[0].spans[0], 0.5)[1] == pytest.approx(0.0)


def test_stored_slot_paths_survive_and_empty_rows_still_derive(env: Env) -> None:
    planner = _planner(env)
    switch = next(
        record for record in planner.list_parts() if record.spec.article_number == "20020517"
    )
    loaded = planner.library.require(switch.id).spec
    assert _branch(track_figure(loaded)).spans
    straight = next(
        record for record in planner.list_parts() if record.spec.article_number == "20020601"
    )
    plain = planner.library.require(straight.id).spec
    assert plain.slot_paths == ()
    assert len(track_figure(plain).slots) == 2
    entry = next(
        record for record in planner.list_parts() if record.spec.article_number == "20030356-E"
    )
    assert planner.library.require(entry.id).spec.slot_paths == ()
    assert len(track_figure(planner.library.require(entry.id).spec).slots) == 3


def test_a_missing_groove_on_an_old_row_is_filled_from_the_catalogue(env: Env) -> None:
    planner = _planner(env)
    planner.list_parts()
    from sqlalchemy import select

    from slot_racing.modules.track_planner.models import TrackPartDefinition

    with env.runtime.database.session() as session:
        row = session.scalar(
            select(TrackPartDefinition).where(TrackPartDefinition.article_number == "20020517")
        )
        assert row is not None
        row.slot_paths = None
        outline = list(row.outline)
    planner.library.ensure_seed()
    loaded = next(
        record for record in planner.list_parts() if record.spec.article_number == "20020517"
    )
    assert loaded.spec.slot_paths
    assert [list(point) for point in loaded.spec.outline] == outline


def test_colour_coding_defaults_off_and_an_old_file_still_loads(tmp_path: Path) -> None:
    assert TRACK_PLANNER_COLOR_CODING_DEFAULT is False
    assert AppConfig().track_planner_color_coding is False
    path = tmp_path / "config.json"
    path.write_text('{"language": "en", "audio_volume": 40}', encoding="utf-8")
    loaded = load_config(path)
    assert loaded.language == "en"
    assert loaded.audio_volume == 40
    assert loaded.track_planner_color_coding is False
    loaded.track_planner_color_coding = True
    from slot_racing.core.config import save_config

    save_config(loaded, path)
    assert load_config(path).track_planner_color_coding is True


def test_the_toolbar_toggles_coding_on_the_plan_and_in_the_library(
    qtbot: QtBot, env: Env, tmp_path: Path
) -> None:
    track = env.track("Ring", lanes=2)
    window, page = open_page(qtbot, env, "track_planner")
    window.show()
    assert isinstance(page, PlannerPage)
    _select(page, track.id)
    assert page.color_coding.text() == "Farbcodierung"
    assert page.color_coding.isChecked() is False
    row = _library_row(page, "20020577")
    page.library.setCurrentRow(row)
    page.place_part.click()
    item = next(piece for piece in page.canvas.scene().items() if isinstance(piece, InstanceItem))
    assert item._color_coding is False
    assert _preview(page, "20020577").color_coding is False
    kept = item
    page.color_coding.click()
    assert page.color_coding.isChecked() is True
    assert env.runtime.config.track_planner_color_coding is True
    assert _preview(page, "20020577").color_coding is True
    assert kept._color_coding is True
    assert kept is next(
        piece for piece in page.canvas.scene().items() if isinstance(piece, InstanceItem)
    )
    page.color_coding.click()
    assert page.color_coding.isChecked() is False
    assert _preview(page, "20020577").color_coding is False
    assert kept._color_coding is False
    assert env.runtime.config.track_planner_color_coding is False

    path = tmp_path / "config.json"
    runtime = Runtime.create(AppConfig(), config_path=path, database=migrated_database())
    try:
        stored = PlannerPage(
            runtime.translator,
            runtime.services.get(TrackCatalog),
            runtime.services.get(TrackPlannerService),
            runtime.config,
            runtime.config_path,
        )
        qtbot.addWidget(stored)
        stored.color_coding.click()
        assert load_config(path).track_planner_color_coding is True
        stored.color_coding.click()
        assert load_config(path).track_planner_color_coding is False
    finally:
        runtime.shutdown()


def test_a_shoulder_layer_can_be_painted_outside_the_road(qtbot: QtBot) -> None:
    figure = TrackFigure(
        roadway=((0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)),
        road_arc=None,
        slots=(),
        centerlines=(),
        edges=(),
        outer_shoulder=((-8.0, -8.0), (28.0, -8.0), (28.0, 18.0), (-8.0, 18.0)),
    )
    image = QImage(60, 40, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.black)
    painter = QPainter(image)
    painter.translate(20.0, 20.0)
    from slot_racing.modules.track_planner.ui.track_paint import paint_figure

    paint_figure(
        painter,
        figure,
        fill=ROADWAY,
        selected=False,
        start_straight=False,
        part_width=10.0,
    )
    painter.end()
    shoulder = image.pixelColor(20 - 4, 20 - 4)
    road = image.pixelColor(20 + 10, 20 + 5)
    assert shoulder.name() == QColor("#3E4650").name()
    assert road.name() == QColor(ROADWAY).name()


def test_every_catalogue_part_paints(qtbot: QtBot) -> None:
    for spec in standard_catalog():
        _render(spec, color_coding=False, start_straight=spec.category == STRAIGHT)


def _preview(page: PlannerPage, article: str) -> PartPreview:
    card = page.library.itemWidget(page.library.item(_library_row(page, article)))
    assert card is not None
    preview = card.findChild(PartPreview)
    assert preview is not None
    return preview


def _render(spec: PartSpec, *, color_coding: bool, start_straight: bool) -> QImage:
    image = QImage(520, 360, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.black)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.translate(260.0, 180.0)
    paint_part(
        painter,
        spec,
        color_coding=color_coding,
        selected=False,
        start_straight=start_straight,
    )
    painter.end()
    return image


def _pixel(image: QImage, x_mm: float, y_mm: float) -> QColor:
    return image.pixelColor(round(260.0 + x_mm), round(180.0 + y_mm))


def _branch(figure: TrackFigure) -> SlotPath:
    moving = [path for path in figure.slots if _moves(path)]
    assert len(moving) == 1
    return moving[0]


def _branch_end_y(spec: PartSpec) -> float:
    return span_point(_branch(track_figure(spec)).spans[-1], 1.0)[1]


def _moves(path: SlotPath) -> bool:
    start = span_point(path.spans[0], 0.0)
    end = span_point(path.spans[-1], 1.0)
    return abs(start[1] - end[1]) > 1.0


def _horizontal(path: SlotPath) -> bool:
    start = span_point(path.spans[0], 0.0)
    end = span_point(path.spans[-1], 1.0)
    return abs(end[1] - start[1]) < abs(end[0] - start[0])


def _has_curve(path: QPainterPath) -> bool:
    for index in range(path.elementCount()):
        if path.elementAt(index).type == QPainterPath.ElementType.CurveToElement:
            return True
    return False


def test_centre_ink_and_slot_ink_are_the_shared_colours() -> None:
    assert QColor(CENTER_LINE).lightness() > QColor(SLOT_CORE).lightness()
    assert QColor(ROADWAY).lightness() > QColor(SLOT_CORE).lightness()
