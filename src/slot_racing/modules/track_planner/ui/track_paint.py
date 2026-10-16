"""Paint one track part. The plan, the library and both editors call this.

A part that carries source fills is drawn from those drawings: areas at the
back, strokes in front, in the order stored on the part. Colour coding swaps
``fill`` for ``fill_alt`` and does not rebuild geometry. Parts without a source
fill keep the generic figure. A closed contour clips that body. Selection,
shortage and the short label are painted afterwards, so they are not clipped.
Zoom and pan are view transforms.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF, QTransform

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
from slot_racing.modules.track_planner.parts import (
    PATH_CENTER,
    PATH_DRAW,
    PATH_SLOT,
    PATH_START,
    SPAN_ARC,
    SPAN_BEZIER,
    PartSpec,
    SlotPath,
    SlotSpan,
    as_segment,
)
from slot_racing.uikit.theme import COLORS

_MIN_PX = 1.2
# Red and white are paint, not the identity of a border strip.
STRIP_WHITE = "#f4f4f4"
STRIP_RED = "#d0121a"


@dataclass(slots=True)
class _Drawn:
    roadway: QPainterPath
    slots: QPainterPath
    centers: QPainterPath
    edges: QPainterPath
    outer_shoulder: QPainterPath
    inner_shoulder: QPainterPath
    stripes: QPainterPath


_CAPS = {
    "butt": Qt.PenCapStyle.FlatCap,
    "round": Qt.PenCapStyle.RoundCap,
    "square": Qt.PenCapStyle.SquareCap,
}


def paint_part(
    painter: QPainter,
    spec: PartSpec,
    *,
    color_coding: bool,
    selected: bool,
    start_straight: bool,
    shortage: bool = False,
    attachment_slot: str | None = None,
    show_short_label: bool = False,
) -> None:
    """Draw ``spec`` in local millimetres. The caller sets the transform.

    ``show_short_label`` is the plan's Kurzbezeichnung switch. The library and
    the editors leave it off. The label does not change the hit shape.
    """
    accessory = spec.attachment is not None
    sourced = _has_source_fill(spec) and not accessory
    figure: TrackFigure | None = None
    painter.save()
    clip = _clip_path(spec)
    if clip is not None:
        painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
    if sourced:
        _paint_visuals(painter, spec, color_coding=color_coding, styled_only=False)
    else:
        figure = track_figure(spec, attachment_slot)
        paint_figure(
            painter,
            figure,
            fill=STRIP_WHITE if accessory else roadway_fill(spec, coded=color_coding),
            selected=selected,
            start_straight=start_straight and not accessory,
            part_width=spec.width_mm or 0.0,
            shortage=shortage and not accessory,
            mark=False,
        )
        _paint_start_paths(painter, spec)
        _paint_visuals(painter, spec, color_coding=color_coding, styled_only=True)
    painter.restore()
    if sourced:
        _paint_status(painter, spec, selected=selected, shortage=shortage)
    elif figure is not None:
        _paint_road_status(
            painter,
            _drawn(figure).roadway,
            selected=selected,
            shortage=shortage and not accessory,
        )
    if show_short_label:
        _paint_short_label(painter, spec)


def _has_source_fill(spec: PartSpec) -> bool:
    return any(path.fill for path in spec.slot_paths)


def _styled(path: SlotPath) -> bool:
    return path.kind in {PATH_DRAW, PATH_CENTER, PATH_START} and (
        path.stroke is not None or path.fill is not None or path.dashed
    )


def _paint_visuals(
    painter: QPainter, spec: PartSpec, *, color_coding: bool, styled_only: bool
) -> None:
    """Source drawings. The last file entry is painted first so index 0 stays on top."""
    visuals = [path for path in spec.slot_paths if path.kind != PATH_SLOT]
    sourced = [path for path in visuals if path.z_order >= 0]
    custom = [path for path in visuals if path.z_order < 0]
    sourced.sort(key=lambda path: path.z_order, reverse=True)
    for path in (*sourced, *custom):
        if path.kind == PATH_SLOT:
            continue
        if styled_only and not _styled(path):
            continue
        _paint_drawing(painter, path, color_coding=color_coding)


def _paint_drawing(painter: QPainter, path: SlotPath, *, color_coding: bool) -> None:
    shape = _shape(path)
    if shape.isEmpty():
        return
    fill = path.fill_alt if color_coding and path.fill_alt else path.fill
    fill_color = None if fill is None else _color(fill)
    if fill_color is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill_color)
        painter.drawPath(shape)
    width = path.width_mm
    stroke = path.stroke
    stroke_color = None if stroke is None else _color(stroke)
    if stroke is None or stroke_color is None or width is None or width <= 0.0:
        return
    pen = QPen(stroke_color)
    pen.setWidthF(width)
    pen.setCapStyle(_CAPS.get(path.linecap or "", Qt.PenCapStyle.FlatCap))
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    if path.dashed:
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([2.0, 1.4])
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(shape)


def _paint_status(painter: QPainter, spec: PartSpec, *, selected: bool, shortage: bool) -> None:
    _paint_road_status(painter, _polygon(spec.outline), selected=selected, shortage=shortage)


def _paint_road_status(
    painter: QPainter, roadway: QPainterPath, *, selected: bool, shortage: bool
) -> None:
    """Selection and shortage sit outside the clip, on the roadway or the outline."""
    if shortage:
        _stroke(
            painter,
            roadway,
            COLORS.error,
            _halo_mm(painter, EDGE_WIDTH_MM * 1.6, 4.0),
            round_cap=False,
        )
    if selected:
        _stroke(painter, roadway, COLORS.accent, EDGE_WIDTH_MM * 1.6, round_cap=False)


def _clip_path(spec: PartSpec) -> QPainterPath | None:
    """Closed contours only. An open edge is not a paint bound."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    for contour in spec.contours:
        if not _closed_contour(contour):
            continue
        region = QPainterPath()
        region.setFillRule(Qt.FillRule.WindingFill)
        for span in contour.spans:
            _append_span(region, span)
        if region.isEmpty():
            continue
        region.closeSubpath()
        path.addPath(region)
    path.setFillRule(Qt.FillRule.WindingFill)
    if path.isEmpty() or path.boundingRect().isEmpty():
        return None
    return path


def _closed_contour(path: SlotPath) -> bool:
    if path.closed:
        return bool(path.spans)
    if not path.spans:
        return False
    start = as_segment(path.spans[0]).start()
    end = as_segment(path.spans[-1]).end()
    return _near(QPointF(*start), end)


def short_label_rect(spec: PartSpec) -> QRectF | None:
    """Axis-aligned badge in local millimetres. ``None`` when there is no label."""
    label = _label_metrics(spec)
    if label is None:
        return None
    transform = QTransform()
    transform.translate(spec.center_x_mm, spec.center_y_mm)
    transform.rotate(spec.center_angle_deg)
    return transform.mapRect(label.badge)


def _paint_short_label(painter: QPainter, spec: PartSpec) -> None:
    label = _label_metrics(spec)
    if label is None:
        return
    painter.save()
    painter.translate(spec.center_x_mm, spec.center_y_mm)
    painter.rotate(spec.center_angle_deg)
    pen = QPen(QColor(0, 0, 0, 38))
    pen.setCosmetic(True)
    pen.setWidthF(0.5)
    painter.setPen(pen)
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(label.badge, 1.0, 1.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#000000"))
    painter.drawPath(label.text)
    painter.restore()


@dataclass(frozen=True, slots=True)
class _Label:
    badge: QRectF
    text: QPainterPath


def _label_metrics(spec: PartSpec) -> _Label | None:
    """Softyroyal badge. The size follows the clip, and it never exceeds 40 mm."""
    text = spec.short_label.strip()
    if not text:
        return None
    clip = _clip_path(spec)
    if clip is None:
        span_x = span_y = math.inf
    else:
        bounds = clip.boundingRect()
        span_x = bounds.width()
        span_y = bounds.height()
    size = min(40.0, span_x * 0.9 / (len(text) * 0.6 + 0.8), span_y * 0.9 / 1.4)
    if not math.isfinite(size) or size <= 0.0:
        return None
    pad = size * 0.4
    badge_w = len(text) * size * 0.6 + pad * 2.0
    badge_h = size * 1.4
    font = QFont()
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(1000)
    font.setWeight(QFont.Weight.DemiBold)
    glyphs = QPainterPath()
    glyphs.addText(QPointF(0, 0), font, text)
    ink = glyphs.boundingRect()
    if ink.isEmpty():
        return None
    glyphs.translate(-ink.center().x(), -ink.center().y())
    drawn = QTransform().scale(size / 1000.0, size / 1000.0).map(glyphs)
    return _Label(QRectF(-badge_w / 2.0, -badge_h / 2.0, badge_w, badge_h), drawn)


def _color(text: str) -> QColor | None:
    color = QColor(text)
    if not color.isValid():
        return None
    return color


def _shape(path: SlotPath) -> QPainterPath:
    shape = QPainterPath()
    shape.setFillRule(Qt.FillRule.WindingFill)
    for span in path.spans:
        _append_span(shape, span)
    if path.closed or path.fill:
        shape.closeSubpath()
    return shape


def _append_span(path: QPainterPath, span: SlotSpan) -> None:
    start = as_segment(span).start()
    continuing = not path.isEmpty() and _near(path.currentPosition(), start)
    if span.kind == SPAN_ARC:
        _add_arc(
            path,
            span.x0,
            span.y0,
            span.radius_mm,
            span.start_deg,
            span.sweep_deg,
            move=not continuing,
        )
        return
    if not continuing:
        path.moveTo(start[0], start[1])
    if span.kind == SPAN_BEZIER:
        path.cubicTo(span.c1x, span.c1y, span.c2x, span.c2y, span.x1, span.y1)
        return
    path.lineTo(span.x1, span.y1)


def _near(current: QPointF, point: tuple[float, float]) -> bool:
    return math.hypot(current.x() - point[0], current.y() - point[1]) <= 0.05


def _paint_start_paths(painter: QPainter, spec: PartSpec) -> None:
    """An unstyled start stroke. Source marks carry their own colour and width."""
    pen = QPen(QColor(START_DARK))
    pen.setCosmetic(True)
    pen.setWidthF(2.4)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for path in spec.slot_paths:
        if path.kind != PATH_START or path.stroke is not None:
            continue
        for span in path.spans:
            segment = as_segment(span)
            steps = 16
            previous = segment.point_at(0.0)
            for index in range(1, steps + 1):
                point = segment.point_at(index / steps)
                painter.drawLine(QPointF(*previous), QPointF(*point))
                previous = point


def paint_figure(
    painter: QPainter,
    figure: TrackFigure,
    *,
    fill: str,
    selected: bool,
    start_straight: bool,
    part_width: float,
    shortage: bool = False,
    mark: bool = True,
) -> None:
    drawn = _drawn(figure)
    painter.save()
    _fill(painter, drawn.outer_shoulder, SHOULDER)
    _fill(painter, drawn.roadway, fill)
    _fill(painter, drawn.stripes, STRIP_RED)
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
    # ``paint_part`` draws the mark after the clip, so the halo is not cut.
    if mark:
        _paint_road_status(painter, drawn.roadway, selected=selected, shortage=shortage)
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
        stripes=_polygons(figure.stripes),
    )


def _polygons(groups: tuple[tuple[tuple[float, float], ...], ...]) -> QPainterPath:
    path = QPainterPath()
    for polygon in groups:
        path.addPolygon(_points(polygon))
    return path


def _points(polygon: tuple[tuple[float, float], ...]) -> QPolygonF:
    return QPolygonF([QPointF(x_mm, y_mm) for x_mm, y_mm in polygon])


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
    if span.kind == SPAN_BEZIER:
        path.moveTo(span.x0, span.y0)
        path.cubicTo(span.c1x, span.c1y, span.c2x, span.c2y, span.x1, span.y1)
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
