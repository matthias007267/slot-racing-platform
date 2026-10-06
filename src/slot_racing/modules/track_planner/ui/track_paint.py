"""Paint one track part from its figure. The plan and the library both call this.

Paths are built once per figure and reused. Colour coding only changes the
roadway brush, so a toggle does not rebuild geometry. Zoom and pan are view
transforms; they do not create new paths.

Layers, from back to front: outer shoulder, roadway, inner shoulder, edges,
slots, centre line, start line. Shoulders are drawn when the figure has them.
No current part does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

from slot_racing.modules.track_planner.appearance import (
    CENTER_LINE,
    CENTER_WIDTH_MM,
    EDGE_WIDTH_MM,
    ROADWAY_EDGE,
    SHOULDER,
    SLOT_CORE,
    SLOT_CORE_MM,
    SLOT_RIM,
    SLOT_RIM_MM,
    START_BAND_MM,
    START_DARK,
    START_LIGHT,
    START_SQUARE_MM,
    roadway_fill,
)
from slot_racing.modules.track_planner.figure import RoadArc, TrackFigure, track_figure
from slot_racing.modules.track_planner.parts import SPAN_ARC, PartSpec, SlotPath, SlotSpan
from slot_racing.uikit.theme import COLORS

_MIN_PX = 1.2


@dataclass(slots=True)
class _Drawn:
    roadway: QPainterPath
    slots: QPainterPath
    centers: QPainterPath
    edges: QPainterPath
    outer_shoulder: QPainterPath
    inner_shoulder: QPainterPath


def paint_part(
    painter: QPainter,
    spec: PartSpec,
    *,
    color_coding: bool,
    selected: bool,
    start_straight: bool,
    shortage: bool = False,
) -> None:
    """Draw ``spec`` in local millimetres. The caller sets the transform."""
    paint_figure(
        painter,
        track_figure(spec),
        fill=roadway_fill(spec, coded=color_coding),
        selected=selected,
        start_straight=start_straight,
        part_width=spec.width_mm or 0.0,
        shortage=shortage,
    )


def paint_figure(
    painter: QPainter,
    figure: TrackFigure,
    *,
    fill: str,
    selected: bool,
    start_straight: bool,
    part_width: float,
    shortage: bool = False,
) -> None:
    drawn = _drawn(figure)
    painter.save()
    _fill(painter, drawn.outer_shoulder, SHOULDER)
    _fill(painter, drawn.roadway, fill)
    _fill(painter, drawn.inner_shoulder, SHOULDER)
    _stroke(painter, drawn.edges, ROADWAY_EDGE, EDGE_WIDTH_MM, round_cap=False)
    # Flat caps end on the span point. A round cap would stick out by half the stroke.
    _stroke(painter, drawn.slots, SLOT_RIM, SLOT_RIM_MM, round_cap=False)
    _stroke(painter, drawn.slots, SLOT_CORE, SLOT_CORE_MM, round_cap=False)
    _stroke(painter, drawn.centers, CENTER_LINE, CENTER_WIDTH_MM, round_cap=False)
    if start_straight and part_width > 0.0:
        _start_line(painter, part_width)
    # The shortage stroke is the roadway itself. It sits under the selection
    # stroke and stays a few screen pixels wider, so a selected extra part keeps
    # a green centre and a red rim. Neither stroke is a bounding box.
    if shortage:
        _stroke(
            painter,
            drawn.roadway,
            COLORS.error,
            _halo_mm(painter, EDGE_WIDTH_MM * 1.6, 4.0),
            round_cap=False,
        )
    if selected:
        _stroke(painter, drawn.roadway, COLORS.accent, EDGE_WIDTH_MM * 1.6, round_cap=False)
    painter.restore()


def apply_preview_transform(painter: QPainter, spec: PartSpec, width: int, height: int) -> None:
    """Fit the part's outline into a preview widget. Painting stays in millimetres."""
    points = spec.outline or ((-20.0, -10.0), (20.0, -10.0), (20.0, 10.0), (-20.0, 10.0))
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    margin = 4.0
    scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
    center_x = (min(xs) + max(xs)) / 2.0
    center_y = (min(ys) + max(ys)) / 2.0
    painter.translate(width / 2.0, height / 2.0)
    painter.scale(scale, scale)
    painter.translate(-center_x, -center_y)


@lru_cache(maxsize=512)
def _drawn(figure: TrackFigure) -> _Drawn:
    roadway = _annulus(figure.road_arc) if figure.road_arc is not None else _polygon(figure.roadway)
    return _Drawn(
        roadway=roadway,
        slots=_grooves(figure.slots),
        centers=_grooves(figure.centerlines),
        edges=_grooves(figure.edges),
        outer_shoulder=_polygon(figure.outer_shoulder),
        inner_shoulder=_polygon(figure.inner_shoulder),
    )


def _fill(painter: QPainter, path: QPainterPath, color: str) -> None:
    if path.isEmpty():
        return
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPath(path)


def _stroke(
    painter: QPainter, path: QPainterPath, color: str, width_mm: float, *, round_cap: bool
) -> None:
    if path.isEmpty():
        return
    pen = QPen(QColor(color))
    pen.setWidthF(_width_mm(painter, width_mm))
    cap = Qt.PenCapStyle.RoundCap if round_cap else Qt.PenCapStyle.FlatCap
    pen.setCapStyle(cap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def _halo_mm(painter: QPainter, inner_mm: float, extra_px: float) -> float:
    """Millimetre width that stays ``extra_px`` wider on screen than ``inner_mm``."""
    transform = painter.transform()
    scale = math.hypot(transform.m11(), transform.m12())
    if scale <= 1e-6:
        return inner_mm
    inner = max(inner_mm, _MIN_PX / scale)
    return inner + extra_px / scale


def _width_mm(painter: QPainter, nominal_mm: float) -> float:
    transform = painter.transform()
    scale = math.hypot(transform.m11(), transform.m12())
    if scale <= 1e-6:
        return nominal_mm
    return max(nominal_mm, _MIN_PX / scale)


def _start_line(painter: QPainter, width_mm: float) -> None:
    """Checker across the roadway. It is painted after the grooves so it stays visible."""
    half = START_BAND_MM / 2.0
    y = -width_mm / 2.0
    index = 0
    painter.setPen(Qt.PenStyle.NoPen)
    while y < width_mm / 2.0 - 0.01:
        height = min(START_SQUARE_MM, width_mm / 2.0 - y)
        color = START_LIGHT if index % 2 == 0 else START_DARK
        painter.fillRect(QRectF(-half, y, START_BAND_MM, height), QColor(color))
        y += height
        index += 1


def _polygon(points: tuple[tuple[float, float], ...]) -> QPainterPath:
    path = QPainterPath()
    if len(points) < 3:
        return path
    polygon = QPolygonF([QPointF(x, y) for x, y in points])
    path.addPolygon(polygon)
    path.closeSubpath()
    return path


def _annulus(arc: RoadArc) -> QPainterPath:
    path = QPainterPath()
    if arc.outer_mm <= 0.0 or arc.angle_deg <= 0.0:
        return path
    _add_arc(path, 0.0, 0.0, arc.outer_mm, arc.start_deg, arc.angle_deg, move=True)
    if arc.inner_mm > 0.5:
        _add_arc(
            path,
            0.0,
            0.0,
            arc.inner_mm,
            arc.start_deg + arc.angle_deg,
            -arc.angle_deg,
            move=False,
        )
    path.closeSubpath()
    return path


def _grooves(paths: tuple[SlotPath, ...]) -> QPainterPath:
    path = QPainterPath()
    for slot in paths:
        for span in slot.spans:
            _add_span(path, span)
    return path


def _add_span(path: QPainterPath, span: SlotSpan) -> None:
    if span.kind == SPAN_ARC:
        _add_arc(path, span.x0, span.y0, span.radius_mm, span.start_deg, span.sweep_deg, move=True)
        return
    path.moveTo(span.x0, span.y0)
    path.lineTo(span.x1, span.y1)


def _add_arc(
    path: QPainterPath,
    cx: float,
    cy: float,
    radius: float,
    start_deg: float,
    sweep_deg: float,
    *,
    move: bool,
) -> None:
    """Qt angles grow the other way from the part coordinates. Negate them."""
    if radius <= 0.0 or sweep_deg == 0.0:
        return
    left = cx - radius
    top = cy - radius
    size = radius * 2.0
    qt_start = -start_deg
    qt_sweep = -sweep_deg
    if move:
        path.arcMoveTo(left, top, size, size, qt_start)
    path.arcTo(left, top, size, size, qt_start, qt_sweep)
