"""A small line chart. Many laps are drawn as a stride, and marked points stay visible."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

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


@dataclass(frozen=True, slots=True)
class ChartSeries:
    name: str
    points: tuple[ChartPoint, ...]
    color: str = ""


def series_color(index: int) -> str:
    return _SERIES[index % len(_SERIES)]


class LineChart(QWidget):
    """Plots the series it was given. An empty chart stays blank."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("line-chart")
        self.setMinimumHeight(180)
        self._series: tuple[ChartSeries, ...] = ()
        self._empty = ""

    def set_series(self, series: Sequence[ChartSeries], *, empty: str = "") -> None:
        colored = []
        for index, item in enumerate(series):
            color = item.color or series_color(index)
            colored.append(ChartSeries(item.name, item.points, color))
        self._series = tuple(colored)
        self._empty = empty
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(COLORS.surface))
        bounds = self.rect().adjusted(44, 12, -12, -22)
        if bounds.width() < 8 or bounds.height() < 8:
            painter.end()
            return
        visible = tuple(item for item in self._series if item.points)
        if not visible:
            painter.setPen(QColor(COLORS.text_muted))
            painter.drawText(bounds, int(Qt.AlignmentFlag.AlignCenter), self._empty)
            painter.end()
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

        def place(x: float, y: float) -> QPointF:
            px = bounds.left() + (x - x0) / (x1 - x0) * bounds.width()
            py = bounds.bottom() - (y - y0) / (y1 - y0) * bounds.height()
            return QPointF(px, py)

        for item in visible:
            drawn = _stride(item.points, bounds.width())
            painter.setPen(QPen(QColor(item.color), 2))
            points = [place(point.x, point.y) for point in drawn]
            if len(points) >= 2:
                painter.drawPolyline(points)
            elif points:
                painter.drawPoint(points[0])
            painter.setBrush(QColor(COLORS.warning))
            for point, located in zip(drawn, points, strict=True):
                if point.marked:
                    painter.setPen(QPen(QColor(COLORS.warning), 1))
                    painter.drawRect(int(located.x()) - 3, int(located.y()) - 3, 6, 6)
        painter.end()


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
