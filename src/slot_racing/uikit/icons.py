"""Small navigation icons painted in code. No icon font and no external library."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from slot_racing.uikit.theme import COLORS


def nav_icon(kind: str, *, active: bool) -> QIcon:
    color = QColor(COLORS.accent if active else COLORS.text_secondary)
    return QIcon(_pixmap(color, kind))


def _pixmap(color: QColor, kind: str) -> QPixmap:
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    try:
        _stroke(painter, kind)
    finally:
        painter.end()
    return pixmap


def _stroke(painter: QPainter, kind: str) -> None:
    if kind == "dashboard":
        for x, y in ((1, 1), (9, 1), (1, 9), (9, 9)):
            painter.drawRoundedRect(x, y, 6, 6, 1, 1)
    elif kind == "drivers":
        painter.drawEllipse(QRectF(5, 1, 6, 6))
        painter.drawArc(QRectF(2, 8, 12, 12), 20 * 16, 140 * 16)
    elif kind == "vehicles":
        painter.drawRoundedRect(QRectF(1, 6, 14, 5), 1.5, 1.5)
        painter.drawLine(3, 6, 5, 3)
        painter.drawLine(5, 3, 10, 3)
        painter.drawLine(10, 3, 13, 6)
        painter.drawEllipse(QRectF(3, 10, 3, 3))
        painter.drawEllipse(QRectF(10, 10, 3, 3))
    elif kind == "tracks":
        painter.drawEllipse(QRectF(1, 3, 14, 10))
        painter.drawEllipse(QRectF(4, 5.5, 8, 5))
    elif kind == "races":
        painter.drawLine(3, 1, 3, 15)
        painter.drawLine(3, 2, 13, 2)
        painter.drawLine(13, 2, 10, 5)
        painter.drawLine(10, 5, 13, 8)
        painter.drawLine(13, 8, 3, 8)
    elif kind == "timing":
        painter.drawLine(6, 1, 10, 1)
        painter.drawEllipse(QRectF(2, 2, 12, 12))
        painter.drawLine(8, 8, 8, 5)
        painter.drawLine(8, 8, 11, 10)
    elif kind == "camera_setup":
        painter.drawRoundedRect(QRectF(1, 4, 14, 10), 2, 2)
        painter.drawEllipse(QRectF(5, 6, 6, 6))
        painter.drawLine(5, 4, 7, 2)
        painter.drawLine(7, 2, 11, 2)
        painter.drawLine(11, 2, 12, 4)
    elif kind == "statistics":
        painter.drawLine(2, 14, 14, 14)
        painter.drawRect(3, 8, 2, 6)
        painter.drawRect(7, 4, 2, 10)
        painter.drawRect(11, 6, 2, 8)
    elif kind == "track_planner":
        painter.drawLine(2, 12, 6, 6)
        painter.drawLine(6, 6, 10, 9)
        painter.drawLine(10, 9, 14, 3)
    elif kind == "settings":
        painter.drawEllipse(QRectF(4.5, 4.5, 7, 7))
        painter.drawEllipse(QRectF(6.5, 6.5, 3, 3))
        painter.drawLine(8, 1, 8, 3)
        painter.drawLine(8, 13, 8, 15)
        painter.drawLine(1, 8, 3, 8)
        painter.drawLine(13, 8, 15, 8)
    else:
        painter.drawEllipse(QRectF(5, 5, 6, 6))
