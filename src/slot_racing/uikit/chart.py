"""A small line chart. Many laps are drawn as a stride, and marked points stay visible.

Race charts pass runs and axis labels. Other charts keep the plain plot.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QToolTip, QWidget

from slot_racing.uikit.theme import COLORS

_SERIES = (
    COLORS.accent,
    COLORS.info,
    COLORS.warning,
    COLORS.error,
    "#C58AF9",
    "#F0A36B",
    "#8FD6C4",
    "#E7A0B4",
)


@dataclass(frozen=True, slots=True)
class ChartPoint:
    x: float
    y: float
    marked: bool = False
    label: str = ""
    tip: str = ""
    run_number: int | None = None


@dataclass(frozen=True, slots=True)
class ChartRun:
    """Inclusive data-x span of one heat. ``label`` is already translated."""

    label: str
    x_start: int
    x_end: int
    run_number: int


@dataclass(frozen=True, slots=True)
class ChartSeries:
    name: str
    points: tuple[ChartPoint, ...]
    color: str = ""


def series_color(index: int) -> str:
    """Color for an anonymous series. Drivers use :func:`driver_color` instead."""
    return _SERIES[index % len(_SERIES)]


def driver_color(participant_id: int) -> str:
    """One stable color per driver. Filtering and order do not move it."""
    if participant_id < 1:
        return series_color(0)
    return _SERIES[(participant_id - 1) % len(_SERIES)]


class _Frame:
    def __init__(self) -> None:
        self.bounds_left = 0
        self.bounds_top = 0
        self.bounds_width = 0
        self.bounds_height = 0
        self.x0 = 0.0
        self.x1 = 1.0
        self.y0 = 0.0
        self.y1 = 1.0

    def place(self, x: float, y: float) -> QPointF:
        span_x = self.x1 - self.x0
        span_y = self.y1 - self.y0
        px = self.bounds_left + (x - self.x0) / span_x * self.bounds_width
        py = self.bounds_top + self.bounds_height - (y - self.y0) / span_y * self.bounds_height
        return QPointF(px, py)

    def data_x(self, px: float) -> float:
        return self.x0 + (px - self.bounds_left) / self.bounds_width * (self.x1 - self.x0)

    def data_y(self, py: float) -> float:
        return self.y1 - (py - self.bounds_top) / self.bounds_height * (self.y1 - self.y0)


class LineChart(QWidget):
    """Plots the series it was given. An empty chart stays blank."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("line-chart")
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self._series: tuple[ChartSeries, ...] = ()
        self._runs: tuple[ChartRun, ...] = ()
        self._empty = ""
        self._axes = False
        self._x_window: tuple[float, float] | None = None
        self._y_window: tuple[float, float] | None = None
        self._drag: QPoint | None = None
        self._hits: tuple[tuple[QPointF, str], ...] = ()
        self._frame: _Frame | None = None

    def set_series(
        self,
        series: Sequence[ChartSeries],
        *,
        empty: str = "",
        runs: Sequence[ChartRun] = (),
        axes: bool = False,
        keep_view: bool = False,
    ) -> None:
        colored = []
        for index, item in enumerate(series):
            color = item.color or series_color(index)
            colored.append(ChartSeries(item.name, item.points, color))
        self._series = tuple(colored)
        self._runs = tuple(runs)
        self._empty = empty
        self._axes = axes
        if not keep_view:
            self._x_window = None
            self._y_window = None
        self.update()

    @property
    def series(self) -> tuple[ChartSeries, ...]:
        return self._series

    @property
    def runs(self) -> tuple[ChartRun, ...]:
        return self._runs

    def x_window(self) -> tuple[float, float] | None:
        return self._x_window

    def y_window(self) -> tuple[float, float] | None:
        return self._y_window

    def reset_view(self) -> None:
        """Show every lap again. The plotted points stay the ones that were passed in."""
        self._x_window = None
        self._y_window = None
        self.update()

    def zoom_horizontal(self, factor: float, anchor: float | None = None) -> None:
        """Zoom the lap axis around ``anchor`` (data x). The series are not copied or edited."""
        frame = self._frame
        if frame is None or factor <= 0:
            return
        center = frame.x0 if anchor is None else anchor
        self._zoom(frame.x0, frame.x1, factor, center, horizontal=True)

    def x_tick_labels(self) -> tuple[str, ...]:
        frame = self._frame
        if frame is None or not self._axes:
            return ()
        return tuple(label for _x, label in _x_labels(self._runs, self._series, frame))

    def y_tick_labels(self) -> tuple[str, ...]:
        frame = self._frame
        if frame is None or not self._axes:
            return ()
        return tuple(_seconds(tick) for tick in _y_ticks(frame.y0, frame.y1))

    def run_labels(self) -> tuple[str, ...]:
        return tuple(run.label for run in self._runs)

    def tip_at(self, pos: QPoint) -> str:
        nearest = ""
        best = 12.0
        for point, tip in self._hits:
            if not tip:
                continue
            distance = math.hypot(point.x() - pos.x(), point.y() - pos.y())
            if distance <= best:
                best = distance
                nearest = tip
        return nearest

    def paintEvent(self, event: object) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(COLORS.surface))
        if self._axes:
            self._paint_axes(painter)
        else:
            self._paint_plain(painter)
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag is not None and self._frame is not None:
            self._pan(event.position().toPoint())
            return
        tip = self.tip_at(event.position().toPoint())
        if tip:
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and (
            self._x_window is not None or self._y_window is not None
        ):
            self._drag = event.position().toPoint()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self._axes or self._frame is None:
            event.ignore()
            return
        steps = event.angleDelta().y()
        if steps == 0:
            return
        factor = 0.8 if steps > 0 else 1.25
        frame = self._frame
        horizontal = not (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if horizontal:
            anchor = frame.data_x(event.position().x())
            self._zoom(frame.x0, frame.x1, factor, anchor, horizontal=True)
        else:
            anchor = frame.data_y(event.position().y())
            self._zoom(frame.y0, frame.y1, factor, anchor, horizontal=False)
        event.accept()

    def _paint_plain(self, painter: QPainter) -> None:
        self._hits = ()
        self._frame = None
        bounds = self.rect().adjusted(44, 12, -12, -22)
        if bounds.width() < 8 or bounds.height() < 8:
            return
        visible = tuple(item for item in self._series if item.points)
        if not visible:
            painter.setPen(QColor(COLORS.text_muted))
            painter.drawText(bounds, int(Qt.AlignmentFlag.AlignCenter), self._empty)
            return
        xs = [point.x for item in visible for point in item.points]
        ys = [point.y for item in visible for point in item.points]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        if x0 == x1:
            x0 -= 1
            x1 += 1
        if y0 == y1:
            y0 -= 1
            y1 += 1
        painter.setPen(QPen(QColor(COLORS.border), 1))
        painter.drawRect(bounds)
        frame = _Frame()
        frame.bounds_left = bounds.left()
        frame.bounds_top = bounds.top()
        frame.bounds_width = bounds.width()
        frame.bounds_height = bounds.height()
        frame.x0, frame.x1, frame.y0, frame.y1 = x0, x1, y0, y1
        for item in visible:
            drawn = _stride(item.points, bounds.width())
            painter.setPen(QPen(QColor(item.color), 2))
            points = [frame.place(point.x, point.y) for point in drawn]
            if len(points) >= 2:
                painter.drawPolyline(points)
            elif points:
                painter.drawPoint(points[0])
            painter.setBrush(QColor(COLORS.warning))
            for point, located in zip(drawn, points, strict=True):
                if point.marked:
                    painter.setPen(QPen(QColor(COLORS.warning), 1))
                    painter.drawRect(int(located.x()) - 3, int(located.y()) - 3, 6, 6)

    def _paint_axes(self, painter: QPainter) -> None:
        top = 28 if self._runs else 12
        bounds = self.rect().adjusted(64, top, -12, -28)
        visible = tuple(item for item in self._series if item.points)
        if bounds.width() < 8 or bounds.height() < 8:
            self._hits = ()
            self._frame = None
            return
        if not visible and not self._runs:
            self._hits = ()
            self._frame = None
            painter.setPen(QColor(COLORS.text_muted))
            painter.drawText(bounds, int(Qt.AlignmentFlag.AlignCenter), self._empty)
            return
        full_x, full_y = _domains(visible, self._runs)
        x0, x1 = self._x_window or full_x
        y0, y1 = self._y_window or full_y
        frame = _Frame()
        frame.bounds_left = bounds.left()
        frame.bounds_top = bounds.top()
        frame.bounds_width = bounds.width()
        frame.bounds_height = bounds.height()
        frame.x0, frame.x1, frame.y0, frame.y1 = x0, x1, y0, y1
        self._frame = frame
        self._paint_runs(painter, frame)
        self._paint_grid(painter, frame)
        hits: list[tuple[QPointF, str]] = []
        for item in visible:
            painter.setPen(QPen(QColor(item.color), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for segment in _segments(item.points):
                drawn = _stride(segment, bounds.width())
                located = [frame.place(point.x, point.y) for point in drawn]
                if len(located) >= 2:
                    painter.drawPolyline(located)
                elif located:
                    painter.drawPoint(located[0])
                painter.setBrush(QColor(item.color))
                for point, dot in zip(drawn, located, strict=True):
                    painter.setPen(QPen(QColor(item.color), 1))
                    painter.drawEllipse(dot, 3, 3)
                    if point.tip:
                        hits.append((dot, point.tip))
                    if point.marked:
                        painter.setBrush(QColor(COLORS.warning))
                        painter.setPen(QPen(QColor(COLORS.warning), 1))
                        painter.drawRect(int(dot.x()) - 3, int(dot.y()) - 3, 6, 6)
                        painter.setBrush(QColor(item.color))
        self._hits = tuple(hits)
        self._paint_labels(painter, frame)

    def _paint_runs(self, painter: QPainter, frame: _Frame) -> None:
        band = QColor(COLORS.elevated)
        band.setAlpha(170)
        for run in self._runs:
            left = frame.place(run.x_start - 0.5, frame.y1)
            right = frame.place(run.x_end + 0.5, frame.y0)
            rect_left = max(frame.bounds_left, int(min(left.x(), right.x())))
            rect_right = min(frame.bounds_left + frame.bounds_width, int(max(left.x(), right.x())))
            if run.run_number % 2 == 1 and rect_right > rect_left:
                painter.fillRect(
                    rect_left,
                    frame.bounds_top,
                    rect_right - rect_left,
                    frame.bounds_height,
                    band,
                )
            edge = frame.place(run.x_end + 0.5, frame.y0)
            if frame.bounds_left < edge.x() < frame.bounds_left + frame.bounds_width:
                painter.setPen(QPen(QColor(COLORS.border), 1, Qt.PenStyle.DashLine))
                painter.drawLine(
                    int(edge.x()),
                    frame.bounds_top,
                    int(edge.x()),
                    frame.bounds_top + frame.bounds_height,
                )
            painter.setPen(QColor(COLORS.text_secondary))
            caption_left = rect_left
            caption_width = max(0, rect_right - rect_left)
            painter.drawText(
                caption_left,
                4,
                caption_width,
                frame.bounds_top - 6,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom),
                run.label,
            )

    def _paint_grid(self, painter: QPainter, frame: _Frame) -> None:
        grid = QColor(COLORS.border)
        grid.setAlpha(180)
        painter.setPen(QPen(grid, 1))
        for tick in _y_ticks(frame.y0, frame.y1):
            y = frame.place(frame.x0, tick).y()
            painter.drawLine(
                frame.bounds_left,
                int(y),
                frame.bounds_left + frame.bounds_width,
                int(y),
            )
        painter.setPen(QPen(QColor(COLORS.border), 1))
        painter.drawRect(
            frame.bounds_left,
            frame.bounds_top,
            frame.bounds_width,
            frame.bounds_height,
        )

    def _paint_labels(self, painter: QPainter, frame: _Frame) -> None:
        painter.setPen(QColor(COLORS.text_muted))
        for tick in _y_ticks(frame.y0, frame.y1):
            y = int(frame.place(frame.x0, tick).y())
            painter.drawText(
                4,
                y - 8,
                frame.bounds_left - 8,
                16,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _seconds(tick),
            )
        labels = _thinned(_x_labels(self._runs, self._series, frame), frame.bounds_width)
        for x, label in labels:
            located = frame.place(x, frame.y0)
            painter.drawText(
                int(located.x()) - 18,
                frame.bounds_top + frame.bounds_height + 2,
                36,
                20,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                label,
            )

    def _zoom(
        self, start: float, end: float, factor: float, anchor: float, *, horizontal: bool
    ) -> None:
        span = max(end - start, 1e-9)
        new_span = span * factor
        full = _domains(
            tuple(item for item in self._series if item.points),
            self._runs,
        )
        limit = full[0] if horizontal else full[1]
        full_span = limit[1] - limit[0]
        minimum = 1.0 if horizontal else full_span / 20
        if new_span >= full_span - 1e-6:
            if horizontal:
                self._x_window = None
            else:
                self._y_window = None
            self.update()
            return
        new_span = max(minimum, min(new_span, full_span))
        ratio = 0.5 if span == 0 else min(1.0, max(0.0, (anchor - start) / span))
        new_start = anchor - ratio * new_span
        new_end = new_start + new_span
        if new_start < limit[0]:
            new_end += limit[0] - new_start
            new_start = limit[0]
        if new_end > limit[1]:
            new_start -= new_end - limit[1]
            new_end = limit[1]
        window = (new_start, new_end)
        if horizontal:
            self._x_window = window
        else:
            self._y_window = window
        self.update()

    def _pan(self, pos: QPoint) -> None:
        frame = self._frame
        origin = self._drag
        if frame is None or origin is None:
            return
        dx = pos.x() - origin.x()
        dy = pos.y() - origin.y()
        self._drag = pos
        if self._x_window is not None and frame.bounds_width > 0:
            shift = -dx / frame.bounds_width * (frame.x1 - frame.x0)
            self._slide(shift, horizontal=True)
        if self._y_window is not None and frame.bounds_height > 0:
            shift = dy / frame.bounds_height * (frame.y1 - frame.y0)
            self._slide(shift, horizontal=False)
        self.update()

    def _slide(self, shift: float, *, horizontal: bool) -> None:
        window = self._x_window if horizontal else self._y_window
        if window is None:
            return
        full = _domains(tuple(item for item in self._series if item.points), self._runs)
        limit = full[0] if horizontal else full[1]
        start, end = window
        if start + shift < limit[0]:
            shift = limit[0] - start
        if end + shift > limit[1]:
            shift = limit[1] - end
        moved = (start + shift, end + shift)
        if horizontal:
            self._x_window = moved
        else:
            self._y_window = moved


def _domains(
    visible: Sequence[ChartSeries], runs: Sequence[ChartRun]
) -> tuple[tuple[float, float], tuple[float, float]]:
    xs = [point.x for item in visible for point in item.points]
    ys = [point.y for item in visible for point in item.points]
    for run in runs:
        xs.append(run.x_start - 0.5)
        xs.append(run.x_end + 0.5)
    if not xs:
        xs = [0.0, 1.0]
    if not ys:
        ys = [0.0, 1.0]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x0 == x1:
        x0 -= 1
        x1 += 1
    if y0 == y1:
        y0 -= 0.5
        y1 += 0.5
    else:
        pad = (y1 - y0) * 0.08
        y0 -= pad
        y1 += pad
    return (x0, x1), (y0, y1)


def _y_ticks(low: float, high: float) -> tuple[float, ...]:
    if high <= low:
        high = low + 1
    raw = (high - low) / 4
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    chosen = 10.0 * magnitude
    for step in (1, 2, 2.5, 5, 10):
        if step * magnitude >= raw - 1e-12:
            chosen = step * magnitude
            break
    start = math.ceil((low + chosen * 1e-6) / chosen) * chosen
    ticks: list[float] = []
    value = start
    while value < high - chosen * 1e-6 and len(ticks) < 6:
        ticks.append(value)
        value += chosen
    if not ticks:
        ticks.append((low + high) / 2)
    return tuple(ticks)


def _seconds(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        text = str(round(value))
    elif abs(value * 10 - round(value * 10)) < 1e-4:
        text = f"{value:.1f}"
    else:
        text = f"{value:.2f}"
    return text.replace(".", ",") + " s"


def _x_labels(
    runs: Sequence[ChartRun], series: Sequence[ChartSeries], frame: _Frame
) -> tuple[tuple[float, str], ...]:
    if runs:
        labels = [
            (float(x), str(x - run.x_start + 1))
            for run in runs
            for x in range(run.x_start, run.x_end + 1)
            if frame.x0 - 1e-6 <= x <= frame.x1 + 1e-6
        ]
        return tuple(labels)
    seen: dict[int, str] = {}
    for item in series:
        for point in item.points:
            if frame.x0 - 1e-6 <= point.x <= frame.x1 + 1e-6:
                key = round(point.x)
                seen.setdefault(key, point.label or str(key))
    return tuple((float(key), label) for key, label in sorted(seen.items()))


def _thinned(labels: Sequence[tuple[float, str]], width: int) -> tuple[tuple[float, str], ...]:
    if len(labels) <= 1:
        return tuple(labels)
    capacity = max(2, width // 32)
    if len(labels) <= capacity:
        return tuple(labels)
    step = math.ceil(len(labels) / capacity)
    kept = [
        label for index, label in enumerate(labels) if index % step == 0 or index == len(labels) - 1
    ]
    return tuple(kept)


def _segments(points: Sequence[ChartPoint]) -> tuple[tuple[ChartPoint, ...], ...]:
    ordered = tuple(sorted(points, key=lambda point: (point.x, point.run_number or 0)))
    if not ordered:
        return ()
    groups: list[list[ChartPoint]] = [[ordered[0]]]
    for point in ordered[1:]:
        previous = groups[-1][-1]
        if point.run_number != previous.run_number:
            groups.append([point])
        else:
            groups[-1].append(point)
    return tuple(tuple(group) for group in groups)


def _stride(points: Sequence[ChartPoint], width: int) -> tuple[ChartPoint, ...]:
    limit = max(2, width)
    if len(points) <= limit:
        return tuple(points)
    step = math.ceil(len(points) / limit)
    kept = [
        point
        for index, point in enumerate(points)
        if point.marked or index % step == 0 or index == len(points) - 1
    ]
    return tuple(kept)
