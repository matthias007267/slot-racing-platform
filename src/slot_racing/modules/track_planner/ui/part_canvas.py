"""Canvas of the graphical part editor: view, painting and mouse events.

The widget only translates events. It turns a pixel into millimetres (``+y`` down, like the
plan), hands the position and a zoom-aware pick tolerance to :class:`EditController`, and
paints what the controller holds. No database access, no timers, no threads.
"""

from __future__ import annotations

import math
from itertools import pairwise

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from slot_racing.core.errors import ValidationError
from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.edit_session import (
    KIND_CONNECTOR,
    KIND_PATH,
    KIND_POINT,
    KIND_SEGMENT,
    Target,
)
from slot_racing.modules.track_planner.edit_tools import DRAW_TOOLS, EditController
from slot_racing.modules.track_planner.geometry import POSITION_GAP_MM, TANGENT_KINK_DEG
from slot_racing.modules.track_planner.parts import (
    SPAN_ARC,
    SPAN_BEZIER,
    TRACK,
    PartSpec,
    SlotSpan,
    as_segment,
)
from slot_racing.modules.track_planner.ui.track_paint import paint_part
from slot_racing.uikit.theme import COLORS

LANE_COLORS = ("#4FA3F7", "#F2994A", "#BB6BD9", "#5BD1B5")
CONTOUR_COLOR = "#C9D1D9"
CENTER_COLOR = "#6E7681"
SELECTION_COLOR = "#FFFFFF"
GAP_COLOR = "#FF5C5C"
KINK_COLOR = "#E2B340"
SMOOTH_COLOR = "#2FBF71"

HIT_PX = 8.0
ARROW_PX = 38.0
MIN_SCALE = 0.04
MAX_SCALE = 60.0
ZOOM_STEP = 1.2
_SAMPLES = 40


def lane_color(lane: int | None) -> str:
    if not lane:
        return CENTER_COLOR
    return LANE_COLORS[(lane - 1) % len(LANE_COLORS)]


class PartCanvas(QWidget):
    """Draws one part in millimetres and forwards the mouse to the controller."""

    view_changed = Signal()

    def __init__(self, controller: EditController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("part-canvas")
        self.setMinimumSize(520, 380)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.controller = controller
        self.scale = 1.0
        self.origin = QPointF(0.0, 0.0)
        self.read_only = False
        self.show_grid = True
        self._fitted = False
        self._panning: QPointF | None = None
        self._left_down = False

    # ------------------------------------------------------------------ view

    def set_controller(self, controller: EditController) -> None:
        self.controller = controller
        self.fit_view()

    def to_world(self, point: QPointF) -> tuple[float, float]:
        return (
            (point.x() - self.origin.x()) / self.scale,
            (point.y() - self.origin.y()) / self.scale,
        )

    def to_screen(self, x_mm: float, y_mm: float) -> QPointF:
        return QPointF(self.origin.x() + x_mm * self.scale, self.origin.y() + y_mm * self.scale)

    def tolerance_mm(self) -> float:
        return HIT_PX / self.scale

    def arrow_mm(self) -> float:
        return ARROW_PX / self.scale

    def zoom_at(self, anchor: QPointF, factor: float) -> None:
        if not math.isfinite(factor) or factor <= 0.0:
            return
        scale = min(max(self.scale * factor, MIN_SCALE), MAX_SCALE)
        factor = scale / self.scale
        self.origin = QPointF(
            anchor.x() - (anchor.x() - self.origin.x()) * factor,
            anchor.y() - (anchor.y() - self.origin.y()) * factor,
        )
        self.scale = scale
        self._fitted = True
        self.view_changed.emit()
        self.update()

    def pan_by(self, dx: float, dy: float) -> None:
        self.origin = QPointF(self.origin.x() + dx, self.origin.y() + dy)
        self._fitted = True
        self.view_changed.emit()
        self.update()

    def fit_view(self) -> None:
        """Show the whole part with a margin."""
        points = _extent(self.controller.spec)
        width = max(self.width(), 80)
        height = max(self.height(), 80)
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        span_x = max(max(xs) - min(xs), 1.0)
        span_y = max(max(ys) - min(ys), 1.0)
        margin = 48.0
        scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
        self.scale = min(max(scale, MIN_SCALE), MAX_SCALE)
        centre_x = (min(xs) + max(xs)) / 2.0
        centre_y = (min(ys) + max(ys)) / 2.0
        self.origin = QPointF(
            width / 2.0 - centre_x * self.scale, height / 2.0 - centre_y * self.scale
        )
        self._fitted = False
        self.view_changed.emit()
        self.update()

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)  # type: ignore[arg-type]
        if not self._fitted:
            self.fit_view()

    # ------------------------------------------------------------------ mouse and keys

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        self.zoom_at(event.position(), ZOOM_STEP ** (delta / 120.0))
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.setFocus()
        button = event.button()
        if button == Qt.MouseButton.MiddleButton:
            self._panning = event.position()
            return
        if button == Qt.MouseButton.RightButton:
            if self.controller.drawing:
                self.controller.finish_stroke()
                self.update()
            else:
                self._panning = event.position()
            return
        if button != Qt.MouseButton.LeftButton or self.read_only:
            return
        self._left_down = True
        x, y = self.to_world(event.position())
        self.controller.press(x, y, self.tolerance_mm(), self.arrow_mm())
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._panning is not None:
            here = event.position()
            self.pan_by(here.x() - self._panning.x(), here.y() - self._panning.y())
            self._panning = here
            return
        if self.read_only:
            return
        x, y = self.to_world(event.position())
        pressed = self._left_down or bool(event.buttons() & Qt.MouseButton.LeftButton)
        self.controller.move(x, y, self.tolerance_mm(), pressed=pressed)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._panning is not None and event.button() in {
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.RightButton,
        }:
            self._panning = None
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._left_down = False
        if self.read_only:
            return
        x, y = self.to_world(event.position())
        self.controller.release(x, y, self.tolerance_mm())
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.controller.drawing:
            self.controller.finish_stroke()
            self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.controller.cancel()
        elif key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.controller.finish_stroke()
        elif key in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace} and not self.read_only:
            self.controller.delete_selection()
        else:
            super().keyPressEvent(event)
            return
        self.update()

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self.controller.hover = None
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ painting

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS.surface))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        spec = self.controller.spec
        self._grid(painter)
        painter.save()
        painter.translate(self.origin)
        painter.scale(self.scale, self.scale)
        self._body(painter, spec)
        painter.restore()
        painter.save()
        painter.translate(self.origin)
        painter.scale(self.scale, self.scale)
        self._paths(painter, spec)
        self._rubber(painter)
        painter.restore()
        self._markers(painter, spec)
        painter.end()

    def _grid(self, painter: QPainter) -> None:
        step = self.controller.snapping.grid_mm
        if step <= 0.0 or not self.show_grid:
            return
        while step * self.scale < 9.0:
            step *= 5.0
        faint = QPen(QColor(COLORS.border))
        faint.setWidthF(1.0)
        left, top = self.to_world(QPointF(0.0, 0.0))
        right, bottom = self.to_world(QPointF(float(self.width()), float(self.height())))
        painter.setPen(faint)
        x = math.floor(left / step) * step
        while x <= right:
            screen = self.to_screen(x, 0.0).x()
            painter.drawLine(QPointF(screen, 0.0), QPointF(screen, float(self.height())))
            x += step
        y = math.floor(top / step) * step
        while y <= bottom:
            screen = self.to_screen(0.0, y).y()
            painter.drawLine(QPointF(0.0, screen), QPointF(float(self.width()), screen))
            y += step
        axis = QPen(QColor(COLORS.text_muted))
        axis.setWidthF(1.0)
        painter.setPen(axis)
        origin = self.to_screen(0.0, 0.0)
        painter.drawLine(QPointF(origin.x(), 0.0), QPointF(origin.x(), float(self.height())))
        painter.drawLine(QPointF(0.0, origin.y()), QPointF(float(self.width()), origin.y()))

    def _body(self, painter: QPainter, spec: PartSpec) -> None:
        try:
            paint_part(
                painter,
                spec,
                color_coding=False,
                selected=False,
                start_straight=False,
                attachment_slot=_slot(spec),
            )
        except (ValidationError, ValueError, ArithmeticError):
            self._fallback_body(painter, spec)

    def _fallback_body(self, painter: QPainter, spec: PartSpec) -> None:
        if len(spec.outline) < 3:
            return
        polygon = QPolygonF([QPointF(x, y) for x, y in spec.outline])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS.border))
        painter.drawPolygon(polygon)

    def _paths(self, painter: QPainter, spec: PartSpec) -> None:
        controller = self.controller
        selected = controller.selection
        for ref in ed.path_refs(spec):
            try:
                spans = ed.spans_of(spec, ref)
            except ValidationError:
                continue
            on_path = (
                selected is not None
                and selected.path == ref
                and selected.kind in {KIND_PATH, KIND_SEGMENT, KIND_POINT}
            )
            if on_path and selected is not None and selected.kind == KIND_PATH:
                self._stroke(painter, spans, QColor(SELECTION_COLOR), 7.0, alpha=110)
            color = self._path_color(spec, ref)
            width = 3.0 if ref.layer == ed.LAYER_GROOVE else 2.0
            style = Qt.PenStyle.DashLine if ref.layer == ed.LAYER_CENTER else Qt.PenStyle.SolidLine
            self._stroke(painter, spans, QColor(color), width, style=style)
            if (
                on_path
                and selected is not None
                and selected.kind in {KIND_SEGMENT, KIND_POINT}
                and 0 <= selected.segment < len(spans)
            ):
                self._stroke(
                    painter, (spans[selected.segment],), QColor(SELECTION_COLOR), 5.0, alpha=170
                )
                self._stroke(painter, (spans[selected.segment],), QColor(color), width)

    def _path_color(self, spec: PartSpec, ref: ed.PathRef) -> str:
        if ref.layer == ed.LAYER_GROOVE:
            return lane_color(spec.slot_paths[ref.index].lane)
        if ref.layer == ed.LAYER_CENTER:
            return CENTER_COLOR
        if ref.layer == ed.LAYER_CONTOUR:
            return CONTOUR_COLOR
        if ref.layer == ed.LAYER_EDGE:
            return COLORS.info
        return COLORS.warning

    def _stroke(
        self,
        painter: QPainter,
        spans: tuple[SlotSpan, ...],
        color: QColor,
        width: float,
        *,
        alpha: int = 255,
        style: Qt.PenStyle = Qt.PenStyle.SolidLine,
    ) -> None:
        path = QPainterPath()
        for span in spans:
            _add_span(path, span)
        tinted = QColor(color)
        tinted.setAlpha(alpha)
        pen = QPen(tinted)
        pen.setCosmetic(True)
        pen.setWidthF(width)
        pen.setStyle(style)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _rubber(self, painter: QPainter) -> None:
        spans = self.controller.rubber_band()
        if spans:
            self._stroke(
                painter,
                spans,
                QColor(COLORS.accent),
                2.0,
                alpha=200,
                style=Qt.PenStyle.DashLine,
            )

    def _markers(self, painter: QPainter, spec: PartSpec) -> None:
        controller = self.controller
        selected = controller.selection
        for ref in ed.path_refs(spec):
            try:
                spans = ed.spans_of(spec, ref)
            except ValidationError:
                continue
            self._lane_label(painter, spec, ref, spans)
            self._handles(painter, ref, spans, selected)
            self._joints(painter, spans)
        self._connectors(painter, spec, selected)
        for edge in spec.border_edges:
            self._edge_label(painter, edge.spans, edge.slot, COLORS.info)
        profile = spec.attachment
        if profile is not None and profile.edge:
            self._edge_label(painter, profile.edge, ",".join(profile.slots), COLORS.warning)
        for point in controller.pending:
            self._dot(painter, self.to_screen(*point), COLORS.accent, 4.0)
        hover = controller.hover
        if hover is not None and controller.tool in DRAW_TOOLS:
            where = self.to_screen(hover.x, hover.y)
            color = COLORS.accent if hover.kind else COLORS.text_muted
            self._dot(painter, where, color, 5.0 if hover.kind else 3.0)
            if hover.kind:
                painter.setPen(QColor(COLORS.text_secondary))
                painter.drawText(where + QPointF(8.0, -8.0), hover.kind)

    def _handles(
        self,
        painter: QPainter,
        ref: ed.PathRef,
        spans: tuple[SlotSpan, ...],
        selected: Target | None,
    ) -> None:
        for index, span in enumerate(spans):
            points = ed.handle_points(span)
            if span.kind == SPAN_BEZIER:
                for anchor, handle_name in (("p0", "p1"), ("p3", "p2")):
                    pen = QPen(QColor(COLORS.text_muted))
                    pen.setStyle(Qt.PenStyle.DotLine)
                    painter.setPen(pen)
                    painter.drawLine(
                        self.to_screen(*points[anchor]), self.to_screen(*points[handle_name])
                    )
            for name, (x, y) in points.items():
                chosen = (
                    selected is not None
                    and selected.kind == KIND_POINT
                    and selected.path == ref
                    and selected.segment == index
                    and selected.handle == name
                )
                where = self.to_screen(x, y)
                control = name in {"p1", "p2", "center", "mid"}
                color = COLORS.accent if chosen else CONTOUR_COLOR
                if control:
                    self._ring(painter, where, color, 4.5, filled=chosen)
                else:
                    self._square(painter, where, color, 4.0, filled=chosen)

    def _joints(self, painter: QPainter, spans: tuple[SlotSpan, ...]) -> None:
        for first, second in pairwise(spans):
            end = ed.span_end(first)
            start = ed.span_start(second)
            gap = math.hypot(end[0] - start[0], end[1] - start[1])
            if gap > POSITION_GAP_MM:
                self._dot(painter, self.to_screen(*end), GAP_COLOR, 5.0)
                self._dot(painter, self.to_screen(*start), GAP_COLOR, 5.0)
                continue
            delta = _tangent_delta(first, second)
            color = KINK_COLOR if delta > TANGENT_KINK_DEG else SMOOTH_COLOR
            self._dot(painter, self.to_screen(*end), color, 3.0)

    def _lane_label(
        self,
        painter: QPainter,
        spec: PartSpec,
        ref: ed.PathRef,
        spans: tuple[SlotSpan, ...],
    ) -> None:
        if ref.layer != ed.LAYER_GROOVE:
            return
        lane = spec.slot_paths[ref.index].lane
        middle = as_segment(spans[len(spans) // 2]).point_at(0.5)
        where = self.to_screen(*middle) + QPointF(-4.0, -9.0)
        painter.setPen(QColor(lane_color(lane)))
        painter.drawText(where, str(lane or "?"))

    def _edge_label(
        self,
        painter: QPainter,
        spans: tuple[SlotSpan, ...],
        text: str,
        color: str,
    ) -> None:
        if not spans:
            return
        middle = as_segment(spans[len(spans) // 2]).point_at(0.5)
        where = self.to_screen(*middle) + QPointF(4.0, 14.0)
        painter.setPen(QColor(color))
        painter.drawText(where, text)

    def _connectors(self, painter: QPainter, spec: PartSpec, selected: Target | None) -> None:
        length = ARROW_PX
        for index, item in enumerate(spec.connectors):
            rail = item.kind == TRACK
            color = COLORS.accent if rail else COLORS.warning
            chosen = (
                selected is not None
                and selected.kind == KIND_CONNECTOR
                and selected.connector == index
            )
            origin = self.to_screen(item.x_mm, item.y_mm)
            radians = math.radians(item.direction_deg)
            tip = QPointF(
                origin.x() + math.cos(radians) * length, origin.y() + math.sin(radians) * length
            )
            pen = QPen(QColor(SELECTION_COLOR if chosen else color))
            pen.setWidthF(3.0 if chosen else 2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(origin, 7.0, 7.0)
            painter.drawLine(origin, tip)
            for side in (-0.45, 0.45):
                wing = QPointF(
                    tip.x() - math.cos(radians + side) * 9.0,
                    tip.y() - math.sin(radians + side) * 9.0,
                )
                painter.drawLine(tip, wing)
            painter.setPen(QColor(color))
            lanes = ",".join(str(lane) for lane in item.lanes)
            painter.drawText(origin + QPointF(10.0, -10.0), f"{item.name} [{lanes}]")

    def _dot(self, painter: QPainter, where: QPointF, color: str, radius: float) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(where, radius, radius)

    def _ring(
        self, painter: QPainter, where: QPointF, color: str, radius: float, *, filled: bool
    ) -> None:
        pen = QPen(QColor(color))
        pen.setWidthF(1.6)
        painter.setPen(pen)
        painter.setBrush(QColor(color) if filled else QColor(COLORS.surface))
        painter.drawEllipse(where, radius, radius)

    def _square(
        self, painter: QPainter, where: QPointF, color: str, half: float, *, filled: bool
    ) -> None:
        pen = QPen(QColor(color))
        pen.setWidthF(1.6)
        painter.setPen(pen)
        painter.setBrush(QColor(color) if filled else QColor(COLORS.surface))
        painter.drawRect(QRectF(where.x() - half, where.y() - half, half * 2.0, half * 2.0))


def _slot(spec: PartSpec) -> str | None:
    profile = spec.attachment
    if profile is None or not profile.slots:
        return None
    return profile.slots[0]


def _extent(spec: PartSpec) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = list(spec.outline)
    points.extend((item.x_mm, item.y_mm) for item in spec.connectors)
    for ref in ed.path_refs(spec):
        try:
            spans = ed.spans_of(spec, ref)
        except ValidationError:
            continue
        for span in spans:
            points.extend(ed.handle_points(span).values())
    if not points:
        return [(-60.0, -40.0), (60.0, 40.0)]
    return points


def _tangent_delta(first: SlotSpan, second: SlotSpan) -> float:
    left = as_segment(first).tangent_deg(1.0)
    right = as_segment(second).tangent_deg(0.0)
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _add_span(path: QPainterPath, span: SlotSpan) -> None:
    segment = as_segment(span)
    if span.kind == SPAN_BEZIER:
        path.moveTo(span.x0, span.y0)
        path.cubicTo(span.c1x, span.c1y, span.c2x, span.c2y, span.x1, span.y1)
        return
    if span.kind == SPAN_ARC:
        start = segment.point_at(0.0)
        path.moveTo(*start)
        for step in range(1, _SAMPLES + 1):
            path.lineTo(*segment.point_at(step / _SAMPLES))
        return
    path.moveTo(span.x0, span.y0)
    path.lineTo(span.x1, span.y1)
