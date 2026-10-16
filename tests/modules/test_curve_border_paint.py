"""Curve and border fills stay one contour.

Qt's arc command stops short of the mathematical end. A closed fill then
splits, and the implicit close draws a chord through the part. These tests
use the stored Carrera geometry and check the painter, not a second renderer.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import TrackId
from slot_racing.modules.track_planner.catalog_import import load_carrera_library
from slot_racing.modules.track_planner.document import (
    TrackPlan,
    add_instance,
    complete_auto_connections,
    empty_plan,
)
from slot_racing.modules.track_planner.parts import (
    ACTIVE_CURVE_SOURCE,
    ACTIVE_STRAIGHT_SOURCE,
    PATH_CONTOUR,
    PATH_DRAW,
    SPAN_ARC,
    TRACK,
    ConnectorSpec,
    PartInstance,
    PartSpec,
    SlotPath,
    SlotSpan,
    as_segment,
    join_pose,
)
from slot_racing.modules.track_planner.ui.graphic_editor import GraphicEditor
from slot_racing.modules.track_planner.ui.library_view import PartPreview
from slot_racing.modules.track_planner.ui.track_paint import (
    _add_arc,
    _clip_path,
    _shape,
    apply_preview_transform,
    paint_part,
)
from tests.modules.conftest import Env

_CURVE = "20571"
_OUTER = "20561"
_INNER = "20551"
_STRAIGHT_BORDER = "20560"
_ROAD = "#333333"
_WHITE = "#ffffff"
_RED = "#b51a00"
_PAPER = "#f4f1ea"
_SCALE = 2.0
_SIZE = 1800


def _library() -> dict[int, PartSpec]:
    return {index: spec for index, spec in enumerate(load_carrera_library().parts, start=1)}


def _source(catalog: dict[int, PartSpec], source_id: str) -> tuple[int, PartSpec]:
    return next(item for item in catalog.items() if item[1].source_id == source_id)


def _article(catalog: dict[int, PartSpec], article: str) -> tuple[int, PartSpec]:
    return next(
        item
        for item in catalog.items()
        if item[1].article_number == article and item[1].piece_key == ""
    )


def _part(article: str) -> PartSpec:
    return _article(_library(), article)[1]


def _curve() -> PartSpec:
    return next(
        spec for spec in load_carrera_library().parts if spec.source_id == ACTIVE_CURVE_SOURCE
    )


def _moves(path: QPainterPath) -> int:
    kind = QPainterPath.ElementType.MoveToElement
    return sum(1 for index in range(path.elementCount()) if path.elementAt(index).type == kind)


def _stored_breaks(path: SlotPath) -> int:
    breaks = 0
    previous: tuple[float, float] | None = None
    for span in path.spans:
        start = as_segment(span).start()
        if (
            previous is not None
            and math.hypot(start[0] - previous[0], start[1] - previous[1]) > 0.05
        ):
            breaks += 1
        previous = as_segment(span).end()
    return breaks


def _polar(radius: float, deg: float) -> QPointF:
    angle = math.radians(deg)
    return QPointF(radius * math.cos(angle), radius * math.sin(angle))


def _xy(radius: float, deg: float) -> tuple[float, float]:
    point = _polar(radius, deg)
    return (point.x(), point.y())


def _arcs(path: SlotPath) -> tuple[SlotSpan, ...]:
    return tuple(span for span in path.spans if span.kind == SPAN_ARC)


def _mid_angle(span: SlotSpan) -> float:
    return span.start_deg + span.sweep_deg / 2.0


def _center(spec: PartSpec) -> tuple[float, float]:
    if not spec.outline:
        return (0.0, 0.0)
    xs = [point[0] for point in spec.outline]
    ys = [point[1] for point in spec.outline]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _render(
    spec: PartSpec,
    *,
    rotation: float = 0.0,
    scale: float = _SCALE,
) -> QImage:
    origin_x, origin_y = _center(spec)
    image = QImage(_SIZE, _SIZE, QImage.Format.Format_RGB32)
    image.fill(QColor(_PAPER))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.translate(_SIZE / 2.0, _SIZE / 2.0)
    painter.rotate(rotation)
    painter.scale(scale, scale)
    painter.translate(-origin_x, -origin_y)
    paint_part(painter, spec, color_coding=False, selected=False, start_straight=False)
    painter.end()
    return image


def _screen(
    x_mm: float,
    y_mm: float,
    *,
    rotation: float = 0.0,
    scale: float = _SCALE,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[int, int]:
    local_x = x_mm - origin[0]
    local_y = y_mm - origin[1]
    turned_x = local_x * math.cos(math.radians(rotation)) - local_y * math.sin(
        math.radians(rotation)
    )
    turned_y = local_x * math.sin(math.radians(rotation)) + local_y * math.cos(
        math.radians(rotation)
    )
    return (
        round(_SIZE / 2.0 + turned_x * scale),
        round(_SIZE / 2.0 + turned_y * scale),
    )


def _uniform(image: QImage, x_px: int, y_px: int, radius: int = 2) -> str:
    """One colour for a small block. A single antialiased pixel cannot pass."""
    colors: set[str] = set()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            pixel = image.pixelColor(x_px + dx, y_px + dy)
            colors.add(f"#{pixel.red():02x}{pixel.green():02x}{pixel.blue():02x}")
    assert len(colors) == 1
    return next(iter(colors))


def _ink(spec: PartSpec, x_mm: float, y_mm: float, *, rotation: float = 0.0) -> str:
    image = _render(spec, rotation=rotation)
    return _uniform(image, *_screen(x_mm, y_mm, rotation=rotation, origin=_center(spec)))


def _track(spec: PartSpec) -> tuple[ConnectorSpec, ...]:
    return tuple(connector for connector in spec.connectors if connector.kind == TRACK)


def _nearest(spec: PartSpec, direction: float) -> ConnectorSpec:
    return min(
        _track(spec),
        key=lambda connector: abs(((connector.direction_deg - direction + 180.0) % 360.0) - 180.0),
    )


def _annulus(inner: float, outer: float, start: float, sweep: float) -> tuple[SlotSpan, ...]:
    begin = _xy(outer, start)
    outer_end = _xy(outer, start + sweep)
    inner_end = _xy(inner, start + sweep)
    inner_begin = _xy(inner, start)
    return (
        SlotSpan.arc(0.0, 0.0, outer, start, sweep),
        SlotSpan.line(*outer_end, *inner_end),
        SlotSpan.arc(0.0, 0.0, inner, start + sweep, -sweep),
        SlotSpan.line(*inner_begin, *begin),
    )


def _filled(spans: tuple[SlotSpan, ...], color: str, z_order: int) -> SlotPath:
    return SlotPath(spans, PATH_DRAW, fill=color, closed=True, z_order=z_order)


def _paint_paths(paths: tuple[SlotPath, ...], contours: tuple[SlotPath, ...] = ()) -> QImage:
    spec = PartSpec(
        article_number="T",
        scale="1:24",
        name="T",
        category="curve",
        length_mm=None,
        width_mm=None,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=1,
        connectors=(),
        outline=((-200.0, -200.0), (200.0, -200.0), (200.0, 200.0), (-200.0, 200.0)),
        slot_paths=paths,
        contours=contours,
    )
    return _render(spec, scale=1.0)


def _band(spec: PartSpec, color: str) -> SlotPath:
    return next(path for path in spec.slot_paths if path.fill == color)


def _radii(path: SlotPath) -> tuple[float, float]:
    radii = sorted(span.radius_mm for span in _arcs(path))
    assert len(radii) >= 2
    return (radii[0], radii[-1])


def test_a_curve_without_borders_is_one_sector() -> None:
    curve = _curve()
    road = _band(curve, _ROAD)
    shape = _shape(road)
    assert _moves(shape) == 1
    assert _stored_breaks(road) == 0
    inner, outer = _radii(road)
    assert (inner, outer) == pytest.approx((198.0, 396.0))
    angle = _mid_angle(max(_arcs(road), key=lambda span: span.radius_mm))
    assert shape.contains(_polar((inner + outer) / 2.0, angle))
    assert shape.contains(_polar(outer - 1.0, angle))
    assert not shape.contains(_polar(inner - 8.0, angle))
    assert not shape.contains(_polar(outer + 8.0, angle))
    assert not shape.contains(_polar((inner + outer) / 2.0, angle + 40.0))
    assert _ink(curve, *_xy((inner + outer) / 2.0, angle)) == _ROAD
    assert _ink(curve, *_xy(inner - 20.0, angle)) == _PAPER
    assert _ink(curve, *_xy(outer + 20.0, angle)) == _PAPER


def test_inner_and_outer_borders_keep_separate_bands() -> None:
    outer = _part(_OUTER)
    inner = _part(_INNER)
    assert _radii(_band(outer, _ROAD)) == pytest.approx((396.0, 495.0))
    assert _radii(_band(outer, _WHITE)) == pytest.approx((462.0, 495.0))
    assert _radii(_band(inner, _ROAD)) == pytest.approx((99.0, 198.0))
    assert _radii(_band(inner, _WHITE)) == pytest.approx((99.0, 132.0))
    for spec in (outer, inner):
        for path in spec.slot_paths:
            if path.fill is None or not _arcs(path):
                continue
            shape = _shape(path)
            assert _moves(shape) == 1 + _stored_breaks(path)
            low, high = _radii(path)
            arc = max(_arcs(path), key=lambda span: span.radius_mm)
            angle = _mid_angle(arc)
            assert shape.contains(_polar((low + high) / 2.0, angle))
            assert not shape.contains(_polar(low - 8.0, angle))
            assert not shape.contains(_polar(high + 8.0, angle))
            assert not shape.contains(_polar((low + high) / 2.0, arc.start_deg - 8.0))
        clip = _clip_path(spec)
        assert clip is not None
        assert _moves(clip) == 1
    dark = _xy(430.0, 270.0)
    white = _xy(478.0, 270.6)
    red = _xy(478.0, 268.0)
    assert _ink(outer, *dark) == _ROAD
    assert _ink(outer, *white) == _WHITE
    assert _ink(outer, *red) == _RED
    assert _ink(outer, *_xy(300.0, 270.0)) == _PAPER
    assert _ink(inner, *_xy(160.0, 270.0)) == _ROAD
    assert _ink(inner, *_xy(115.0, 275.0)) == _WHITE
    assert _ink(inner, *_xy(250.0, 270.0)) == _PAPER
    straight = _part(_STRAIGHT_BORDER)
    red_path = _band(straight, _RED)
    assert _arcs(red_path) == ()
    assert _moves(_shape(red_path)) == 1
    assert _ink(straight, 0.0, 0.0) == _ROAD
    assert _ink(straight, 0.0, -30.0) == _WHITE
    # The chevrons are narrower than the block used for the bands. Antialiasing is off.
    chevron = _render(straight)
    stripe = chevron.pixelColor(*_screen(-120.0, -45.0, origin=_center(straight)))
    assert (stripe.red(), stripe.green(), stripe.blue()) == (0xB5, 0x1A, 0x00)


def test_a_straight_to_curve_joint_has_no_wedge() -> None:
    catalog = _library()
    straight_id, straight = _source(catalog, ACTIVE_STRAIGHT_SOURCE)
    curve_id, curve = _source(catalog, ACTIVE_CURVE_SOURCE)
    host = PartInstance("s0", straight_id, 0.0, 0.0, rotation_z_deg=0.0)
    seated = join_pose(
        host, max(_track(straight), key=lambda joint: joint.x_mm), _nearest(curve, 30.0)
    )
    assert (seated.x_mm, seated.y_mm, seated.rotation_z_deg) == pytest.approx(
        (172.5, -297.0, 150.0)
    )
    plan = add_instance(empty_plan(TrackId(1)), host)
    plan = add_instance(
        plan,
        PartInstance(
            "c0", curve_id, seated.x_mm, seated.y_mm, rotation_z_deg=seated.rotation_z_deg
        ),
    )
    plan = complete_auto_connections(plan, catalog, ("s0", "c0"))
    articles = [catalog[item.part_id].article_number for item in plan.instances]
    assert sorted(articles) == ["20509", "20551", "20560", "20560", "20561", "20571"]
    curve_item = next(item for item in plan.instances if item.id == "c0")
    borders = [
        item for item in plan.instances if catalog[item.part_id].article_number in {_OUTER, _INNER}
    ]
    assert len(borders) == 2
    for border in borders:
        assert (border.x_mm, border.y_mm, border.rotation_z_deg) == pytest.approx(
            (curve_item.x_mm, curve_item.y_mm, curve_item.rotation_z_deg)
        )
    straight_borders = [
        item for item in plan.instances if catalog[item.part_id].article_number == _STRAIGHT_BORDER
    ]
    assert {(round(item.y_mm, 1), round(item.rotation_z_deg, 1)) for item in straight_borders} == {
        (-148.5, 0.0),
        (148.5, 180.0),
    }
    image, pixel = _paint_plan(plan, catalog)
    assert pixel(curve_item, *_xy(320.0, 270.0), image) == _ROAD
    assert pixel(curve_item, *_xy(320.0, 296.0), image) == _ROAD
    assert pixel(curve_item, *_xy(430.0, 270.0), image) == _ROAD
    assert pixel(curve_item, *_xy(478.0, 270.6), image) == _WHITE
    assert pixel(curve_item, *_xy(478.0, 268.0), image) == _RED
    assert pixel(curve_item, *_xy(160.0, 270.0), image) == _ROAD
    assert pixel(curve_item, *_xy(40.0, 270.0), image) == _PAPER
    assert pixel(host, 40.0, 30.0, image) == _ROAD


def test_rotation_does_not_add_a_triangle() -> None:
    curve = _curve()
    outer = _part(_OUTER)
    for rotation in (0.0, 30.0, 150.0, 210.0):
        assert _ink(curve, *_xy(320.0, 270.0), rotation=rotation) == _ROAD
        assert _ink(curve, *_xy(80.0, 270.0), rotation=rotation) == _PAPER
        assert _ink(outer, *_xy(430.0, 270.0), rotation=rotation) == _ROAD
        assert _ink(outer, *_xy(478.0, 270.6), rotation=rotation) == _WHITE
        assert _ink(outer, *_xy(478.0, 268.0), rotation=rotation) == _RED
        assert _ink(outer, *_xy(300.0, 270.0), rotation=rotation) == _PAPER


def test_separate_subpaths_stay_separate() -> None:
    left = (
        SlotSpan.line(-40, -20, -10, -20),
        SlotSpan.line(-10, -20, -10, 20),
        SlotSpan.line(-10, 20, -40, 20),
        SlotSpan.line(-40, 20, -40, -20),
    )
    right = (
        SlotSpan.line(10, -20, 40, -20),
        SlotSpan.line(40, -20, 40, 20),
        SlotSpan.line(40, 20, 10, 20),
        SlotSpan.line(10, 20, 10, -20),
    )
    split = _shape(_filled((*left, *right), _RED, 0))
    assert _moves(split) == 2
    assert split.contains(QPointF(-25, 0))
    assert split.contains(QPointF(25, 0))
    assert not split.contains(QPointF(0, 0))
    joined = _shape(_filled(_annulus(40.0, 80.0, 200.0, 80.0), _ROAD, 0))
    assert _moves(joined) == 1
    assert joined.contains(_polar(60.0, 240.0))
    assert not joined.contains(_polar(20.0, 240.0))
    for spec in load_carrera_library().parts:
        for path in (*spec.slot_paths, *spec.contours):
            if not path.spans:
                continue
            shape = _shape(path)
            if shape.isEmpty():
                continue
            assert _moves(shape) == 1 + _stored_breaks(path)
            end = as_segment(path.spans[-1]).end()
            current = shape.currentPosition()
            end_error = math.hypot(current.x() - end[0], current.y() - end[1])
            if path.closed or path.fill:
                start = as_segment(path.spans[0]).start()
                start_error = math.hypot(current.x() - start[0], current.y() - start[1])
                assert min(end_error, start_error) < 1e-4
            else:
                assert end_error < 1e-4


def test_open_contours_stay_open_and_closed_ones_close() -> None:
    arc = SlotSpan.arc(0.0, 0.0, 100.0, 10.0, 70.0)
    opened = _shape(SlotPath((arc,), PATH_DRAW, stroke="#000000", width_mm=1.0))
    assert _moves(opened) == 1
    start = as_segment(arc).start()
    end = as_segment(arc).end()
    current = opened.currentPosition()
    assert math.hypot(current.x() - end[0], current.y() - end[1]) < 1e-6
    assert math.hypot(current.x() - start[0], current.y() - start[1]) > 50.0
    kinds = {opened.elementAt(index).type for index in range(opened.elementCount())}
    assert QPainterPath.ElementType.LineToElement not in kinds
    closed = _shape(_filled((arc,), _RED, 0))
    assert _moves(closed) == 1
    # The close is the chord, so the fill is the cap between the chord and the arc.
    assert closed.contains(_polar(96.0, 45.0))
    assert not closed.contains(_polar(70.0, 45.0))
    assert not closed.contains(_polar(110.0, 45.0))
    open_clip = SlotPath((arc,), PATH_CONTOUR)
    box = SlotPath(
        (
            SlotSpan.line(-10, -10, 10, -10),
            SlotSpan.line(10, -10, 10, 10),
            SlotSpan.line(10, 10, -10, 10),
            SlotSpan.line(-10, 10, -10, -10),
        ),
        PATH_CONTOUR,
        closed=True,
    )
    clip = _clip_path(
        PartSpec(
            article_number="T",
            scale="1:24",
            name="T",
            category="curve",
            length_mm=None,
            width_mm=None,
            height_mm=None,
            radius_mm=None,
            angle_deg=None,
            lane_count=1,
            connectors=(),
            outline=(),
            slot_paths=(),
            contours=(open_clip, box),
        )
    )
    assert clip is not None
    assert _moves(clip) == 1
    assert clip.contains(QPointF(0, 0))
    assert not clip.contains(_polar(40.0, 45.0))


def test_arc_cubics_meet_the_mathematical_end() -> None:
    samples = ((0.0, 60.0, 396.0), (240.0, 90.0, 495.0), (300.0, -60.0, 198.0), (0.0, 180.0, 250.0))
    for start, sweep, radius in samples:
        path = QPainterPath()
        _add_arc(path, 0.0, 0.0, radius, start, sweep, move=True)
        end = _xy(radius, start + sweep)
        current = path.currentPosition()
        assert math.hypot(current.x() - end[0], current.y() - end[1]) < 1e-6
        pieces = math.ceil(abs(sweep) / 90.0)
        assert _moves(path) == 1
        assert path.elementCount() == 1 + pieces * 3
        _assert_cubic_radius(path, radius)
    quarter = QPainterPath()
    _add_arc(quarter, 0.0, 0.0, 100.0, 0.0, 90.0, move=True)
    arm = 100.0 * (4.0 / 3.0) * math.tan(math.radians(22.5))
    assert (quarter.elementAt(1).x, quarter.elementAt(1).y) == pytest.approx((100.0, arm))
    assert (quarter.elementAt(2).x, quarter.elementAt(2).y) == pytest.approx((arm, 100.0))
    assert (quarter.elementAt(3).x, quarter.elementAt(3).y) == pytest.approx((0.0, 100.0))
    full = QPainterPath()
    _add_arc(full, 0.0, 0.0, 80.0, 15.0, 360.0, move=True)
    assert full.elementCount() == 13
    assert full.contains(QPointF(0, 0))
    assert not full.contains(_polar(90.0, 40.0))


def test_clip_follows_a_curved_contour() -> None:
    outer = _part(_OUTER)
    clip = _clip_path(outer)
    assert clip is not None and _moves(clip) == 1
    assert clip.contains(_polar(430.0, 270.0))
    assert clip.contains(_polar(478.0, 270.6))
    assert not clip.contains(_polar(300.0, 270.0))
    assert not clip.contains(_polar(520.0, 270.0))
    assert not clip.contains(_polar(450.0, 180.0))
    annulus = SlotPath(_annulus(50.0, 90.0, 180.0, 70.0), PATH_CONTOUR, closed=True)
    apart = SlotPath(
        (
            SlotSpan.line(200, 200, 260, 200),
            SlotSpan.line(260, 200, 260, 260),
            SlotSpan.line(260, 260, 200, 260),
            SlotSpan.line(200, 260, 200, 200),
        ),
        PATH_CONTOUR,
        closed=True,
    )
    wide = _filled(
        (
            SlotSpan.line(-300, -300, 400, -300),
            SlotSpan.line(400, -300, 400, 400),
            SlotSpan.line(400, 400, -300, 400),
            SlotSpan.line(-300, 400, -300, -300),
        ),
        _RED,
        0,
    )
    spec = PartSpec(
        article_number="T",
        scale="1:24",
        name="T",
        category="curve",
        length_mm=None,
        width_mm=None,
        height_mm=None,
        radius_mm=None,
        angle_deg=None,
        lane_count=1,
        connectors=(),
        outline=(),
        slot_paths=(wide,),
        contours=(annulus, apart),
    )
    region = _clip_path(spec)
    assert region is not None and _moves(region) == 2
    assert region.contains(_polar(70.0, 215.0))
    assert region.contains(QPointF(230, 230))
    assert not region.contains(QPointF(140, 140))
    assert not region.contains(_polar(30.0, 215.0))
    image = _render(spec, scale=1.0)
    assert _uniform(image, *_screen(*_xy(70.0, 215.0), scale=1.0), radius=1) == _RED
    assert _uniform(image, *_screen(230.0, 230.0, scale=1.0), radius=1) == _RED
    assert _uniform(image, *_screen(140.0, 140.0, scale=1.0), radius=1) == _PAPER
    assert _uniform(image, *_screen(*_xy(30.0, 215.0), scale=1.0), radius=1) == _PAPER


def test_arc_fills_keep_paint_order_and_transparency() -> None:
    back = _filled(_annulus(20.0, 80.0, 0.0, 90.0), "#0000ff", 1)
    front = _filled(_annulus(40.0, 60.0, 10.0, 40.0), _RED, 0)
    ordered = _paint_paths((back, front))
    assert _uniform(ordered, *_screen(*_xy(50.0, 30.0), scale=1.0), radius=1) == _RED
    assert _uniform(ordered, *_screen(*_xy(30.0, 30.0), scale=1.0), radius=1) == "#0000ff"
    glass = SlotPath(
        _annulus(40.0, 60.0, 10.0, 40.0),
        PATH_DRAW,
        fill="#80ff0000",
        closed=True,
        z_order=0,
    )
    blended = _paint_paths((back, glass))
    pixel = blended.pixelColor(*_screen(*_xy(50.0, 30.0), scale=1.0))
    assert pixel.red() > 100
    assert pixel.blue() > 80
    assert pixel.green() < 30
    outside = blended.pixelColor(*_screen(*_xy(30.0, 30.0), scale=1.0))
    assert (outside.red(), outside.green(), outside.blue()) == (0, 0, 255)


def test_auto_borders_on_an_oval_keep_their_place_and_colour() -> None:
    catalog = _library()
    straight_id, straight = _source(catalog, ACTIVE_STRAIGHT_SOURCE)
    curve_id, curve = _source(catalog, ACTIVE_CURVE_SOURCE)
    plan, far = _oval(catalog, straight_id, straight, curve_id, curve)
    assert (far.x_mm, far.y_mm, far.rotation_z_deg) == pytest.approx((0.0, -594.0, 180.0))
    posed = tuple((item.id, item.x_mm, item.y_mm, item.rotation_z_deg) for item in plan.instances)
    completed = complete_auto_connections(plan, catalog)
    hosts = {(item.id, item.x_mm, item.y_mm, item.rotation_z_deg) for item in completed.instances}
    assert set(posed) <= hosts
    counts: dict[str, int] = {}
    for item in completed.instances:
        article = catalog[item.part_id].article_number
        counts[article] = counts.get(article, 0) + 1
    assert counts == {"20509": 2, "20571": 6, "20560": 4, "20561": 6, "20551": 6}
    first = next(item for item in completed.instances if item.id == "c0")
    assert (first.x_mm, first.y_mm, first.rotation_z_deg) == pytest.approx((172.5, -297.0, 150.0))
    curves = [
        item
        for item in completed.instances
        if catalog[item.part_id].source_id == ACTIVE_CURVE_SOURCE
    ]
    for curve_item in curves:
        mates = [
            item
            for item in completed.instances
            if catalog[item.part_id].article_number in {_OUTER, _INNER}
            and math.isclose(item.x_mm, curve_item.x_mm, abs_tol=1e-6)
            and math.isclose(item.y_mm, curve_item.y_mm, abs_tol=1e-6)
            and math.isclose(item.rotation_z_deg, curve_item.rotation_z_deg, abs_tol=1e-6)
        ]
        assert len(mates) == 2
    frozen = tuple(
        (item.id, item.x_mm, item.y_mm, item.rotation_z_deg) for item in completed.instances
    )
    image, pixel = _paint_plan(completed, catalog)
    assert pixel(first, *_xy(320.0, 270.0), image) == _ROAD
    assert pixel(first, *_xy(430.0, 270.0), image) == _ROAD
    assert pixel(first, *_xy(478.0, 270.6), image) == _WHITE
    assert pixel(first, *_xy(478.0, 268.0), image) == _RED
    assert pixel(first, *_xy(160.0, 270.0), image) == _ROAD
    assert pixel(first, *_xy(115.0, 275.0), image) == _WHITE
    assert pixel(first, *_xy(40.0, 270.0), image) == _PAPER
    assert (
        tuple((item.id, item.x_mm, item.y_mm, item.rotation_z_deg) for item in completed.instances)
        == frozen
    )


def test_plan_preview_and_editor_paint_the_same_border(qtbot: QtBot, env: Env) -> None:
    outer = _part(_OUTER)
    dark = _xy(430.0, 270.0)
    white = _xy(478.0, 270.6)
    assert _ink(outer, *dark) == _ROAD
    assert _ink(outer, *white) == _WHITE
    preview = _preview(outer)
    assert (
        _uniform(preview, *_preview_pixel(outer, *dark, preview.width(), preview.height())) == _ROAD
    )
    assert (
        _uniform(preview, *_preview_pixel(outer, *white, preview.width(), preview.height()))
        == _WHITE
    )
    widget = PartPreview(outer)
    widget.setFixedSize(720, 480)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: widget.width() == 720)
    grabbed = widget.grab().toImage()
    assert (
        _uniform(grabbed, *_preview_pixel(outer, *dark, widget.width(), widget.height())) == _ROAD
    )
    editor = GraphicEditor(env.runtime.translator)
    qtbot.addWidget(editor)
    editor.session.load(outer)
    editor.session.grid_mm = 0
    editor.spec_provider = lambda: outer
    editor.resize(1100, 800)
    editor.show()
    qtbot.waitUntil(lambda: editor.canvas.width() > 400)
    assert _editor_color(editor, *dark) == _ROAD
    assert _editor_color(editor, *white) == _WHITE


def test_painting_leaves_poses_centers_and_connectors() -> None:
    catalog = _library()
    straight_id, straight = _source(catalog, ACTIVE_STRAIGHT_SOURCE)
    curve_id, curve = _source(catalog, ACTIVE_CURVE_SOURCE)
    outer = _part(_OUTER)
    connectors = tuple((item.x_mm, item.y_mm, item.direction_deg) for item in curve.connectors)
    center = (curve.center_x_mm, curve.center_y_mm, curve.center_angle_deg, curve.short_label)
    radii = tuple(span.radius_mm for span in _band(outer, _ROAD).spans if span.kind == SPAN_ARC)
    host = PartInstance("s0", straight_id, 0.0, 0.0, rotation_z_deg=0.0)
    pose = join_pose(
        host, max(_track(straight), key=lambda joint: joint.x_mm), _nearest(curve, 30.0)
    )
    plan = complete_auto_connections(
        add_instance(
            add_instance(empty_plan(TrackId(1)), host),
            PartInstance("c0", curve_id, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg),
        ),
        catalog,
        ("s0", "c0"),
    )
    before = tuple((item.x_mm, item.y_mm, item.rotation_z_deg) for item in plan.instances)
    _ink(curve, 0.0, -320.0)
    _ink(outer, 0.0, -430.0)
    _render(straight)
    assert (
        tuple((item.x_mm, item.y_mm, item.direction_deg) for item in curve.connectors) == connectors
    )
    assert (
        curve.center_x_mm,
        curve.center_y_mm,
        curve.center_angle_deg,
        curve.short_label,
    ) == center
    assert (
        tuple(span.radius_mm for span in _band(outer, _ROAD).spans if span.kind == SPAN_ARC)
        == radii
    )
    assert tuple((item.x_mm, item.y_mm, item.rotation_z_deg) for item in plan.instances) == before
    assert curve.center_y_mm == pytest.approx(-297.0)
    assert curve.center_angle_deg == pytest.approx(0.0)
    assert straight.center_x_mm == pytest.approx(0.0)
    assert straight.center_y_mm == pytest.approx(0.0)


def _assert_cubic_radius(path: QPainterPath, radius: float) -> None:
    index = 0
    while index < path.elementCount():
        element = path.elementAt(index)
        if element.type != QPainterPath.ElementType.CurveToElement:
            index += 1
            continue
        start = path.elementAt(index - 1)
        control_1 = element
        control_2 = path.elementAt(index + 1)
        end = path.elementAt(index + 2)
        for step in range(1, 8):
            point = _bezier(
                (start.x, start.y),
                (control_1.x, control_1.y),
                (control_2.x, control_2.y),
                (end.x, end.y),
                step / 8.0,
            )
            assert abs(math.hypot(point[0], point[1]) - radius) < radius * 0.001
        index += 3


def _bezier(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    rest = 1.0 - t
    return (
        rest**3 * p0[0] + 3 * rest**2 * t * p1[0] + 3 * rest * t**2 * p2[0] + t**3 * p3[0],
        rest**3 * p0[1] + 3 * rest**2 * t * p1[1] + 3 * rest * t**2 * p2[1] + t**3 * p3[1],
    )


def _oval(
    catalog: dict[int, PartSpec],
    straight_id: int,
    straight: PartSpec,
    curve_id: int,
    curve: PartSpec,
) -> tuple[TrackPlan, PartInstance]:
    positive = max(_track(straight), key=lambda joint: joint.x_mm)
    negative = min(_track(straight), key=lambda joint: joint.x_mm)
    curve_in = _nearest(curve, 30.0)
    curve_out = _nearest(curve, 150.0)
    plan = empty_plan(TrackId(1))
    host = PartInstance("s0", straight_id, 0.0, 0.0, rotation_z_deg=0.0)
    plan = add_instance(plan, host)
    free_host: PartInstance = host
    free_joint = positive
    for index in range(3):
        pose = join_pose(free_host, free_joint, curve_in)
        free_host = PartInstance(
            f"c{index}", curve_id, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg
        )
        plan = add_instance(plan, free_host)
        free_joint = curve_out
    far_pose = join_pose(free_host, free_joint, negative)
    far = PartInstance(
        "s1", straight_id, far_pose.x_mm, far_pose.y_mm, rotation_z_deg=far_pose.rotation_z_deg
    )
    plan = add_instance(plan, far)
    free_host = host
    free_joint = negative
    for index in range(3):
        pose = join_pose(free_host, free_joint, curve_out)
        free_host = PartInstance(
            f"d{index}", curve_id, pose.x_mm, pose.y_mm, rotation_z_deg=pose.rotation_z_deg
        )
        plan = add_instance(plan, free_host)
        free_joint = curve_in
    closing = join_pose(free_host, free_joint, positive)
    assert math.hypot(closing.x_mm - far.x_mm, closing.y_mm - far.y_mm) < 1e-6
    assert closing.rotation_z_deg == pytest.approx(far.rotation_z_deg)
    return plan, far


def _paint_plan(
    plan: TrackPlan, catalog: dict[int, PartSpec]
) -> tuple[QImage, Callable[[PartInstance, float, float, QImage], str]]:
    instances = plan.instances
    xs: list[float] = []
    ys: list[float] = []
    for item in instances:
        spec = catalog[item.part_id]
        theta = math.radians(item.rotation_z_deg)
        cosine, sine = math.cos(theta), math.sin(theta)
        for x_mm, y_mm in spec.outline:
            xs.append(item.x_mm + x_mm * cosine - y_mm * sine)
            ys.append(item.y_mm + x_mm * sine + y_mm * cosine)
    pad = 30.0
    scale = 0.8
    min_x, min_y = min(xs) - pad, min(ys) - pad
    width = math.ceil((max(xs) + pad - min_x) * scale)
    height = math.ceil((max(ys) + pad - min_y) * scale)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(_PAPER))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.scale(scale, scale)
    painter.translate(-min_x, -min_y)
    order = sorted(
        instances,
        key=lambda item: catalog[item.part_id].article_number in {_OUTER, _INNER, _STRAIGHT_BORDER},
    )
    for item in order:
        painter.save()
        painter.translate(item.x_mm, item.y_mm)
        painter.rotate(item.rotation_z_deg)
        paint_part(
            painter,
            catalog[item.part_id],
            color_coding=False,
            selected=False,
            start_straight=False,
        )
        painter.restore()
    painter.end()

    def pixel(item: PartInstance, x_mm: float, y_mm: float, painted: QImage) -> str:
        theta = math.radians(item.rotation_z_deg)
        cosine, sine = math.cos(theta), math.sin(theta)
        world_x = item.x_mm + x_mm * cosine - y_mm * sine
        world_y = item.y_mm + x_mm * sine + y_mm * cosine
        return _uniform(
            painted,
            round((world_x - min_x) * scale),
            round((world_y - min_y) * scale),
            radius=1,
        )

    return image, pixel


def _preview(spec: PartSpec) -> QImage:
    image = QImage(720, 480, QImage.Format.Format_RGB32)
    image.fill(QColor(_PAPER))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    apply_preview_transform(painter, spec, image.width(), image.height())
    paint_part(painter, spec, color_coding=False, selected=False, start_straight=False)
    painter.end()
    return image


def _preview_pixel(
    spec: PartSpec, x_mm: float, y_mm: float, width: int, height: int
) -> tuple[int, int]:
    points = spec.outline or ((-20.0, -10.0), (20.0, -10.0), (20.0, 10.0), (-20.0, 10.0))
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    margin = 4.0
    scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
    center_x = (min(xs) + max(xs)) / 2.0
    center_y = (min(ys) + max(ys)) / 2.0
    return (
        round(width / 2.0 + (x_mm - center_x) * scale),
        round(height / 2.0 + (y_mm - center_y) * scale),
    )


def _editor_color(editor: GraphicEditor, x_mm: float, y_mm: float) -> str:
    canvas = editor.canvas
    scale = 2.0
    canvas.set_view(
        scale, canvas.width() / 2.0 - x_mm * scale, canvas.height() / 2.0 - y_mm * scale
    )
    image = canvas.grab().toImage()
    return _uniform(image, canvas.width() // 2, canvas.height() // 2, radius=2)
