"""Plan canvas. Pan and zoom move the view; part coordinates stay on the plan."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QRubberBand,
    QWidget,
)

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
from slot_racing.modules.track_planner.parts import (
    ConnectorSpec,
    PartInstance,
    PartSpec,
    connector_occupied,
    connectors_compatible,
    join_pose,
    rotate_xy,
    world_xy,
)
from slot_racing.modules.track_planner.ui.library_view import PART_MIME
from slot_racing.uikit.theme import COLORS

CELL = 16
# One millimetre on a part becomes this many pixels. A 345 mm straight is about 70 px.
MM = 0.2
ZOOM_MIN = 0.25
ZOOM_MAX = 4.0
_DRAG_PX = 4

Moved = Callable[[str, int, int], None]
MovedGroup = Callable[[str, list[tuple[str, float, float]]], None]
Rotated = Callable[[list[str], float, float, float], None]
Dropped = Callable[[int, float, float], None]
Docked = Callable[[str, int], None]


def rect_fully_inside(inner: QRectF, outer: QRectF) -> bool:
    """True when every edge of ``inner`` lies inside ``outer``."""
    return (
        inner.left() >= outer.left()
        and inner.top() >= outer.top()
        and inner.right() <= outer.right()
        and inner.bottom() <= outer.bottom()
    )


def pointer_angle_deg(origin: QPointF, point: QPointF) -> float:
    """Clockwise degrees from the +x axis in the canvas's y-down coordinates."""
    return math.degrees(math.atan2(point.y() - origin.y(), point.x() - origin.x()))


class PlanCanvas(QGraphicsView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("planner-canvas")
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setMinimumSize(480, 360)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._scene = PlanScene(self)
        self._scene.setSceneRect(-8000, -8000, 20000, 20000)
        self.setScene(self._scene)
        self.centerOn(0, 0)
        self._scene.selectionChanged.connect(self._on_selection)
        self._on_moved: Moved | None = None
        self._on_group: MovedGroup | None = None
        self._on_rotated: Rotated | None = None
        self._on_dropped: Dropped | None = None
        self._on_docked: Docked | None = None
        self._expanding = False
        self._placing = False
        self._pluses: list[PlusItem] = []
        self._lane_count = 2
        self._direction = CLOCKWISE
        self._loading = False
        self._zoom = 1.0
        self._panning = False
        self._pan_at = QPointF()
        self._rubber_at: QPoint | None = None
        self._band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._band.setObjectName("planner-rubber-band")
        self.rotation_handle = RotationHandle(
            self._begin_rotate, self._preview_rotate, self._end_rotate
        )
        self._scene.addItem(self.rotation_handle)
        self._rotate_ids: list[str] = []
        self._rotate_center = (0.0, 0.0)
        self._rotate_start = 0.0
        self._rotate_starts: dict[str, tuple[float, float, float]] = {}

    def set_listener(self, listener: Moved) -> None:
        self._on_moved = listener

    def set_group_listener(self, listener: MovedGroup) -> None:
        self._on_group = listener

    def set_rotation_listener(self, listener: Rotated) -> None:
        self._on_rotated = listener

    def set_drop_listener(self, listener: Dropped) -> None:
        self._on_dropped = listener

    def set_dock_listener(self, listener: Docked) -> None:
        self._on_docked = listener

    def center_on_mm(self, x_mm: float, y_mm: float) -> None:
        """Move the view so one plan point sits in the middle. Coordinates stay put."""
        self.centerOn(x_mm * MM, y_mm * MM)

    def show_plan(
        self,
        plan: TrackPlan,
        lane_count: int,
        selected: str | set[str] | None,
        parts: Mapping[int, PartSpec] | None = None,
    ) -> None:
        chosen = {selected} if isinstance(selected, str) else set(selected or ())
        self._loading = True
        self._lane_count = lane_count
        self._direction = plan.direction
        self._scene.grid_enabled = plan.grid_enabled
        self._scene.grid_px = max(plan.grid_mm * MM, 4.0)
        self.rotation_handle.hide()
        if self.rotation_handle.scene() is self._scene:
            self._scene.removeItem(self.rotation_handle)
        self._pluses = []
        self._scene.clear()
        self._scene.addItem(self.rotation_handle)
        catalog = {} if parts is None else parts
        for instance in plan.instances:
            spec = catalog.get(instance.part_id)
            if spec is None:
                continue
            placed = InstanceItem(instance, spec, self._report_group)
            self._scene.addItem(placed)
            placed.setSelected(placed.item_id in chosen)
        for piece in plan.pieces:
            piece_item = PieceItem(piece, lane_count, plan.direction, self._report)
            self._scene.addItem(piece_item)
            piece_item.setSelected(piece_item.item_id in chosen)
        for marker in plan.markers:
            if marker.kind != START_FINISH:
                continue
            start = StartItem(marker, self._report)
            self._scene.addItem(start)
            start.setSelected(start.item_id in chosen)
        self._loading = False
        self.expand_groups()
        self._place_extras()
        self._scene.invalidate(self._scene.sceneRect(), QGraphicsScene.SceneLayer.BackgroundLayer)
        self.viewport().update()

    def selected_id(self) -> str | None:
        ids = self.selected_ids()
        return ids[0] if ids else None

    def selected_ids(self) -> list[str]:
        found: list[str] = []
        for item in self._scene.selectedItems():
            if isinstance(item, (PieceItem, StartItem, InstanceItem)):
                found.append(item.item_id)
        return found

    def select_fully_inside(self, area: QRectF) -> None:
        """Select every part whose bounds lie completely inside ``area``."""
        self._scene.clearSelection()
        for item in self._scene.items():
            if not isinstance(item, (InstanceItem, PieceItem, StartItem)):
                continue
            if rect_fully_inside(item.sceneBoundingRect(), area):
                item.setSelected(True)
        self.expand_groups()
        self._place_extras()

    def zoom_at(self, view_pos: QPointF, delta_y: int) -> None:
        """Scale around a viewport point. The scene point under it stays put."""
        if delta_y == 0:
            return
        factor = 1.15 if delta_y > 0 else 1 / 1.15
        proposed = self._zoom * factor
        if proposed < ZOOM_MIN or proposed > ZOOM_MAX:
            return
        before = self.mapToScene(view_pos.toPoint())
        self._zoom = proposed
        self.scale(factor, factor)
        after = self.mapToScene(view_pos.toPoint())
        shift = after - before
        self.translate(shift.x(), shift.y())

    def pan_by(self, dx: float, dy: float) -> None:
        """Move the view. Part coordinates are not part of this change."""
        self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - dx))
        self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - dy))

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_at(event.position(), event.angleDelta().y())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._panning = True
            self._pan_at = event.position()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self.itemAt(event.position().toPoint())
            if isinstance(hit, PlusItem):
                if self._on_docked is not None:
                    self._on_docked(hit.instance_id, hit.connector_index)
                event.accept()
                return
            if isinstance(hit, RotationHandle):
                self.rotation_handle.begin(self.mapToScene(event.position().toPoint()))
                event.accept()
                return
            if not isinstance(hit, (InstanceItem, PieceItem, StartItem)):
                self._rubber_at = event.position().toPoint()
                self._band.setGeometry(QRect(self._rubber_at, self._rubber_at))
                self._band.show()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._panning:
            delta = event.position() - self._pan_at
            self._pan_at = event.position()
            self.pan_by(delta.x(), delta.y())
            event.accept()
            return
        if self.rotation_handle.dragging:
            self.rotation_handle.drag(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        if self._rubber_at is not None:
            self._band.setGeometry(QRect(self._rubber_at, event.position().toPoint()).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)
        self._place_extras()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton and self._panning:
            self._panning = False
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.rotation_handle.dragging:
            self.rotation_handle.finish(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._rubber_at is not None:
            origin = self._rubber_at
            self._rubber_at = None
            self._band.hide()
            end = event.position().toPoint()
            if (end - origin).manhattanLength() < _DRAG_PX:
                self._scene.clearSelection()
            else:
                area = QRectF(self.mapToScene(origin), self.mapToScene(end)).normalized()
                self.select_fully_inside(area)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._place_extras()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasFormat(PART_MIME):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if event.mimeData().hasFormat(PART_MIME):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(PART_MIME) or self._on_dropped is None:
            event.ignore()
            return
        part_id = int(bytes(event.mimeData().data(PART_MIME).data()).decode("ascii"))
        position = self.mapToScene(event.position().toPoint())
        self._on_dropped(part_id, position.x() / MM, position.y() / MM)
        event.acceptProposedAction()

    def _report(self, item_id: str, x: int, y: int) -> None:
        if self._loading or self._on_moved is None:
            return
        self._on_moved(item_id, x, y)

    def _report_group(self, anchor: str, moves: list[tuple[str, float, float]]) -> None:
        if self._loading or self._on_group is None:
            return
        self._on_group(anchor, moves)

    def _begin_rotate(self, scene_pos: QPointF) -> None:
        selected = [item for item in self._scene.selectedItems() if isinstance(item, InstanceItem)]
        if not selected:
            return
        center_x = sum(item.pos().x() for item in selected) / len(selected)
        center_y = sum(item.pos().y() for item in selected) / len(selected)
        self._rotate_ids = [item.item_id for item in selected]
        self._rotate_center = (center_x / MM, center_y / MM)
        self._rotate_start = pointer_angle_deg(QPointF(center_x, center_y), scene_pos)
        self._rotate_starts = {
            item.item_id: (item.pos().x() / MM, item.pos().y() / MM, item.rotation())
            for item in selected
        }

    def _preview_rotate(self, scene_pos: QPointF) -> None:
        if not self._rotate_ids:
            return
        center = QPointF(self._rotate_center[0] * MM, self._rotate_center[1] * MM)
        delta = pointer_angle_deg(center, scene_pos) - self._rotate_start
        for item in self._scene.items():
            if not isinstance(item, InstanceItem) or item.item_id not in self._rotate_starts:
                continue
            x_mm, y_mm, rotation = self._rotate_starts[item.item_id]
            dx, dy = rotate_xy(x_mm - self._rotate_center[0], y_mm - self._rotate_center[1], delta)
            item.preview(self._rotate_center[0] + dx, self._rotate_center[1] + dy, rotation + delta)
        self.rotation_handle.setPos(center)

    def _end_rotate(self, scene_pos: QPointF) -> None:
        if not self._rotate_ids or self._on_rotated is None:
            self._rotate_ids = []
            return
        center = QPointF(self._rotate_center[0] * MM, self._rotate_center[1] * MM)
        delta = pointer_angle_deg(center, scene_pos) - self._rotate_start
        ids = list(self._rotate_ids)
        origin = self._rotate_center
        self._rotate_ids = []
        self._on_rotated(ids, origin[0], origin[1], delta)

    def expand_groups(self) -> None:
        """Selecting one member selects the whole persistent group."""
        if self._expanding:
            return
        groups = {
            item.group_id
            for item in self._scene.items()
            if isinstance(item, InstanceItem) and item.isSelected() and item.group_id
        }
        if not groups:
            return
        self._expanding = True
        for item in self._scene.items():
            if isinstance(item, InstanceItem) and item.group_id in groups:
                item.setSelected(True)
        self._expanding = False

    def _on_selection(self) -> None:
        if self._loading or self._expanding or self._placing:
            return
        self._place_extras()

    def _place_extras(self) -> None:
        self._place_handle()
        self._place_pluses()

    def _place_pluses(self) -> None:
        if self._placing:
            return
        self._placing = True
        self._replace_pluses()
        self._placing = False

    def _replace_pluses(self) -> None:
        for plus in self._pluses:
            if plus.scene() is self._scene:
                self._scene.removeItem(plus)
        self._pluses = []
        if self._loading:
            return
        selected = [item for item in self._scene.selectedItems() if isinstance(item, InstanceItem)]
        if len(selected) != 1:
            return
        item = selected[0]
        placed = [(_live_instance(other), other.spec) for other in self._instances()]
        live = _live_instance(item)
        for index, connector in enumerate(item.spec.connectors):
            if connector_occupied(live, connector, placed):
                continue
            if not _same_part_can_join(item.spec, live, connector):
                continue
            point = world_xy(live, connector.x_mm, connector.y_mm)
            plus = PlusItem(item.item_id, index, _plus_offset(live, connector))
            self._scene.addItem(plus)
            plus.setPos(point[0] * MM, point[1] * MM)
            self._pluses.append(plus)

    def _instances(self) -> list[InstanceItem]:
        return [item for item in self._scene.items() if isinstance(item, InstanceItem)]

    def _place_handle(self) -> None:
        selected = [item for item in self._scene.selectedItems() if isinstance(item, InstanceItem)]
        if not selected or self._loading:
            self.rotation_handle.hide()
            return
        center_x = sum(item.pos().x() for item in selected) / len(selected)
        center_y = sum(item.pos().y() for item in selected) / len(selected)
        self.rotation_handle.setPos(center_x, center_y)
        self.rotation_handle.show()
        self.rotation_handle.setZValue(100)


class PlanScene(QGraphicsScene):
    def __init__(self, parent: QGraphicsView) -> None:
        super().__init__(parent)
        self.grid_enabled = False
        self.grid_px = float(CELL)

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:  # noqa: N802
        painter.fillRect(rect, QColor(COLORS.background))
        if not self.grid_enabled:
            return
        pen = QPen(QColor(COLORS.border))
        pen.setCosmetic(True)
        painter.setPen(pen)
        bounds = QRectF(rect)
        step = max(int(self.grid_px), 4)
        left = int(bounds.left()) - int(bounds.left()) % step
        top = int(bounds.top()) - int(bounds.top()) % step
        x = left
        while x < bounds.right():
            painter.drawLine(x, int(bounds.top()), x, int(bounds.bottom()))
            x += step
        y = top
        while y < bounds.bottom():
            painter.drawLine(int(bounds.left()), y, int(bounds.right()), y)
            y += step


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
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if self._ready:
            self._report(self.item_id, int(self.pos().x() // CELL), int(self.pos().y() // CELL))


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


class InstanceItem(QGraphicsItem):
    """Top view of one library part. Dragging a selection moves every selected part."""

    def __init__(self, instance: PartInstance, spec: PartSpec, report: MovedGroup) -> None:
        super().__init__()
        self.item_id = instance.id
        self.spec = spec
        self.group_id = instance.group_id
        self._report = report
        self._ready = False
        self._press: QPointF | None = None
        self._starts: dict[str, QPointF] = {}
        self._polygon = QPolygonF(
            [QPointF(x * MM, y * MM) for x, y in spec.outline]
            or [QPointF(-8, -8), QPointF(8, -8), QPointF(8, 8), QPointF(-8, 8)]
        )
        bounds = self._polygon.boundingRect().adjusted(-2, -2, 2, 2)
        self._bounds = bounds
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setTransformOriginPoint(0, 0)
        self.setPos(instance.x_mm * MM, instance.y_mm * MM)
        self.setRotation(instance.rotation_z_deg)
        self._ready = True

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, _option: object, _widget: object = None) -> None:
        width = 3 if self.isSelected() else 1.5
        painter.setPen(QPen(QColor(COLORS.accent if self.isSelected() else COLORS.border), width))
        painter.setBrush(QColor(COLORS.elevated))
        painter.drawPolygon(self._polygon)
        if self.isSelected():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._bounds)

    def preview(self, x_mm: float, y_mm: float, rotation: float) -> None:
        self._ready = False
        self.setPos(x_mm * MM, y_mm * MM)
        self.setRotation(rotation)
        self._ready = True

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        super().mousePressEvent(event)
        view = self.scene().views()
        if view and isinstance(view[0], PlanCanvas):
            view[0].expand_groups()
        self._press = self.pos()
        self._starts = {
            item.item_id: item.pos()
            for item in self.scene().selectedItems()
            if isinstance(item, InstanceItem)
        }

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        if self._press is None:
            return
        delta = self.pos() - self._press
        for item in self.scene().selectedItems():
            if isinstance(item, InstanceItem) and item is not self and item.item_id in self._starts:
                item.preview(
                    (self._starts[item.item_id].x() + delta.x()) / MM,
                    (self._starts[item.item_id].y() + delta.y()) / MM,
                    item.rotation(),
                )

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if not self._ready or self._press is None:
            return
        delta = self.pos() - self._press
        moves: list[tuple[str, float, float]] = []
        selected = [item for item in self.scene().selectedItems() if isinstance(item, InstanceItem)]
        if not any(item is self for item in selected):
            selected = [self]
        for item in selected:
            if item is self:
                moves.append((item.item_id, item.pos().x() / MM, item.pos().y() / MM))
            else:
                origin = self._starts.get(item.item_id, item.pos())
                moves.append(
                    (item.item_id, (origin.x() + delta.x()) / MM, (origin.y() + delta.y()) / MM)
                )
        self._press = None
        self._report(self.item_id, moves)


class RotationHandle(QGraphicsItem):
    """Knob used to turn the current selection by any angle, in either direction."""

    def __init__(
        self,
        begin: Callable[[QPointF], None],
        preview: Callable[[QPointF], None],
        finish: Callable[[QPointF], None],
    ) -> None:
        super().__init__()
        self._begin = begin
        self._preview = preview
        self._finish = finish
        self.dragging = False
        self._knob = QPointF(0, -36)
        self._bounds = QRectF(-14, -50, 28, 64)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.hide()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def shape(self) -> QPainterPath:
        """Only the knob starts a rotation. The part body underneath stays a move target."""
        path = QPainterPath()
        path.addEllipse(self._knob, 8, 8)
        return path

    def paint(self, painter: QPainter, _option: object, _widget: object = None) -> None:
        painter.setPen(QPen(QColor(COLORS.accent), 2))
        painter.drawLine(QPointF(0, 0), self._knob)
        painter.setBrush(QColor(COLORS.accent))
        painter.drawEllipse(self._knob, 7, 7)

    def begin(self, scene_pos: QPointF) -> None:
        self.dragging = True
        self._begin(scene_pos)

    def drag(self, scene_pos: QPointF) -> None:
        if self.dragging:
            self._preview(scene_pos)

    def finish(self, scene_pos: QPointF) -> None:
        if not self.dragging:
            return
        self.dragging = False
        self._finish(scene_pos)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self.begin(event.scenePos())
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self.drag(event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self.finish(event.scenePos())
        event.accept()


class PlusItem(QGraphicsItem):
    """Click target outside one free joint. The part body underneath stays a move target."""

    def __init__(self, instance_id: str, connector_index: int, offset: QPointF) -> None:
        super().__init__()
        self.instance_id = instance_id
        self.connector_index = connector_index
        self.offset = offset
        self._bounds = QRectF(offset.x() - 10, offset.y() - 10, 20, 20)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setZValue(90)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(self.offset, 8, 8)
        return path

    def paint(self, painter: QPainter, _option: object, _widget: object = None) -> None:
        painter.setPen(QPen(QColor(COLORS.accent), 2))
        painter.setBrush(QColor(COLORS.elevated))
        painter.drawEllipse(self.offset, 7, 7)
        painter.drawLine(self.offset + QPointF(-5, 0), self.offset + QPointF(5, 0))
        painter.drawLine(self.offset + QPointF(0, -5), self.offset + QPointF(0, 5))


def _live_instance(item: InstanceItem) -> PartInstance:
    return PartInstance(
        item.item_id,
        0,
        item.pos().x() / MM,
        item.pos().y() / MM,
        rotation_z_deg=item.rotation(),
    )


def _plus_offset(instance: PartInstance, connector: ConnectorSpec) -> QPointF:
    """Pixels outside the joint, so the symbol does not cover the part body."""
    direction = math.radians(connector.direction_deg + instance.rotation_z_deg)
    return QPointF(math.cos(direction) * 22, math.sin(direction) * 22)


def _same_part_can_join(spec: PartSpec, live: PartInstance, connector: ConnectorSpec) -> bool:
    for source in spec.connectors:
        if not connectors_compatible(source, connector):
            continue
        pose = join_pose(live, connector, source)
        distance = ((pose.x_mm - live.x_mm) ** 2 + (pose.y_mm - live.y_mm) ** 2) ** 0.5
        if distance >= 1.0:
            return True
    return False


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
