"""Grid canvas. Pieces and the start/finish line snap to the grid and stay selectable."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView, QWidget

from slot_racing.modules.track_planner.document import (
    CLOCKWISE,
    CURVE_90,
    GRID_LIMIT,
    START_FINISH,
    STRAIGHT_V,
    Marker,
    Piece,
    TrackPlan,
    lane_centers,
    span,
    travel_vector,
)
from slot_racing.uikit.theme import COLORS

CELL = 16

Moved = Callable[[str, int, int], None]


class PlanCanvas(QGraphicsView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("planner-canvas")
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setMinimumSize(480, 360)
        self._scene = PlanScene(self)
        self._scene.setSceneRect(0, 0, GRID_LIMIT * CELL, 40 * CELL)
        self.setScene(self._scene)
        self._on_moved: Moved | None = None
        self._lane_count = 2
        self._direction = CLOCKWISE
        self._loading = False

    def set_listener(self, listener: Moved) -> None:
        self._on_moved = listener

    def show_plan(self, plan: TrackPlan, lane_count: int, selected: str | None) -> None:
        self._loading = True
        self._lane_count = lane_count
        self._direction = plan.direction
        self._scene.clear()
        for piece in plan.pieces:
            item = PieceItem(piece, lane_count, plan.direction, self._report)
            self._scene.addItem(item)
            item.setSelected(item.item_id == selected)
        for marker in plan.markers:
            if marker.kind != START_FINISH:
                continue
            start = StartItem(marker, self._report)
            self._scene.addItem(start)
            start.setSelected(start.item_id == selected)
        self._loading = False

    def selected_id(self) -> str | None:
        selected = self._scene.selectedItems()
        if not selected:
            return None
        item = selected[0]
        if isinstance(item, (PieceItem, StartItem)):
            return item.item_id
        return None

    def _report(self, item_id: str, x: int, y: int) -> None:
        if self._loading or self._on_moved is None:
            return
        self._on_moved(item_id, x, y)


class PlanScene(QGraphicsScene):
    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:  # noqa: N802
        painter.fillRect(rect, QColor(COLORS.background))
        pen = QPen(QColor(COLORS.border))
        pen.setCosmetic(True)
        painter.setPen(pen)
        bounds = QRectF(rect)
        left = int(bounds.left()) - int(bounds.left()) % CELL
        top = int(bounds.top()) - int(bounds.top()) % CELL
        x = left
        while x < bounds.right():
            painter.drawLine(x, int(bounds.top()), x, int(bounds.bottom()))
            x += CELL
        y = top
        while y < bounds.bottom():
            painter.drawLine(int(bounds.left()), y, int(bounds.right()), y)
            y += CELL


class _GridItem(QGraphicsItem):
    def __init__(self, item_id: str, width: int, height: int, report: Moved) -> None:
        super().__init__()
        self.item_id = item_id
        self._width = width
        self._height = height
        self._report = report
        self._ready = False
        self._rect = QRectF(0, 0, width * CELL, height * CELL)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._rect

    def place(self, x: int, y: int) -> None:
        self._ready = False
        self.setPos(x * CELL, y * CELL)
        self._ready = True

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:  # noqa: N802
        if (
            self._ready
            and change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
            and isinstance(value, QPointF)
        ):
            value = _snap(value, self._width, self._height)
        result = super().itemChange(change, value)
        if self._ready and change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._report(self.item_id, int(self.pos().x() // CELL), int(self.pos().y() // CELL))
        return result


class PieceItem(_GridItem):
    def __init__(self, piece: Piece, lane_count: int, direction: str, report: Moved) -> None:
        width, height = span(piece.piece_type)
        super().__init__(piece.id, width, height, report)
        self._piece = piece
        self._lane_count = lane_count
        self._direction = direction
        self.place(piece.x, piece.y)

    def paint(self, painter: QPainter, _option: object, _widget: object = None) -> None:
        painter.setPen(QPen(QColor(COLORS.accent if self.isSelected() else COLORS.border), 2))
        painter.setBrush(QColor(COLORS.elevated))
        painter.drawRoundedRect(self._rect.adjusted(1, 1, -1, -1), 4, 4)
        painter.setPen(QPen(QColor(COLORS.text_secondary), 1))
        self._lanes(painter)
        self._arrow(painter)

    def _lanes(self, painter: QPainter) -> None:
        if self._piece.piece_type == CURVE_90:
            centers = lane_centers(self._lane_count, self._rect.width() / 2)
            for center in centers:
                painter.drawArc(self._rect.adjusted(center, center, -center, -center), 0, 90 * 16)
            return
        horizontal = self._piece.piece_type != STRAIGHT_V
        span_px = self._rect.height() if horizontal else self._rect.width()
        for center in lane_centers(self._lane_count, span_px):
            if horizontal:
                painter.drawLine(QPointF(4, center), QPointF(self._rect.width() - 4, center))
            else:
                painter.drawLine(QPointF(center, 4), QPointF(center, self._rect.height() - 4))

    def _arrow(self, painter: QPainter) -> None:
        dx, dy = travel_vector(self._piece.piece_type, self._piece.rotation, self._direction)
        center = self._rect.center()
        tip = QPointF(center.x() + dx * 10, center.y() + dy * 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS.accent))
        painter.drawPolygon(_triangle(center, tip))


class StartItem(_GridItem):
    def __init__(self, marker: Marker, report: Moved) -> None:
        super().__init__(marker.id, 2, 2, report)
        self.place(marker.x, marker.y)

    def paint(self, painter: QPainter, _option: object, _widget: object = None) -> None:
        painter.setPen(QPen(QColor(COLORS.warning), 3))
        mid = self._rect.height() / 2
        painter.drawLine(QPointF(2, mid), QPointF(self._rect.width() - 2, mid))
        painter.setPen(QPen(QColor(COLORS.warning)))
        painter.drawText(self._rect, Qt.AlignmentFlag.AlignCenter, "S/Z")


def _snap(value: QPointF, width: int, height: int) -> QPointF:
    x = round(value.x() / CELL) * CELL
    y = round(value.y() / CELL) * CELL
    x = min(max(x, 0), (GRID_LIMIT - width) * CELL)
    y = min(max(y, 0), (GRID_LIMIT - height) * CELL)
    return QPointF(x, y)


def _triangle(origin: QPointF, tip: QPointF) -> QPolygonF:
    dx = tip.x() - origin.x()
    dy = tip.y() - origin.y()
    side = QPointF(-dy, dx)
    scale = 0.35
    return QPolygonF(
        [
            tip,
            QPointF(origin.x() + side.x() * scale, origin.y() + side.y() * scale),
            QPointF(origin.x() - side.x() * scale, origin.y() - side.y() * scale),
        ]
    )
