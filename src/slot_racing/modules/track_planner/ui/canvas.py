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
    EXTEND_LEFT,
    EXTEND_STRAIGHT,
    PartInstance,
    PartSpec,
    arrow_heading_deg,
    connector_occupied,
    normalize_deg,
    offered_extend_directions,
    rotate_xy,
    standard_extend_parts,
    world_xy,
)
from slot_racing.modules.track_planner.ui.library_view import PART_MIME
from slot_racing.modules.track_planner.ui.track_paint import paint_part
from slot_racing.uikit.theme import COLORS

CELL = 16
# One millimetre on a part becomes this many pixels. A 345 mm straight is about 70 px.
MM = 0.2
ZOOM_MIN = 0.25
ZOOM_MAX = 4.0
_DRAG_PX = 4
# Screen pixels. The arrows ignore the view transform, so zoom does not resize them.
ARROW_DISTANCE_PX = 34.0
ARROW_GLYPH_PX = 12.0
ARROW_HIT_RADIUS = 12.0
HANDLE_GAP_PX = 14.0
HANDLE_KNOB_RADIUS = 6.0
HANDLE_HIT_RADIUS = 8.0

Moved = Callable[[str, int, int], None]
MovedGroup = Callable[[str, list[tuple[str, float, float]]], None]
Rotated = Callable[[list[str], float, float, float], None]
Dropped = Callable[[int, float, float], None]
Extended = Callable[[str, int, str], None]


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
        self._on_extended: Extended | None = None
        self._expanding = False
        self._placing = False
        self._arrows: list[ExtendArrow] = []
        self._catalog: Mapping[int, PartSpec] = {}
        self._lane_count = 2
        self._direction = CLOCKWISE
        self._loading = False
        self._zoom = 1.0
        self._color_coding = False
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

    def set_extend_listener(self, listener: Extended) -> None:
        self._on_extended = listener

    def set_color_coding(self, enabled: bool) -> None:
        """Restyle the parts already on the plan. The scene is not rebuilt."""
        self._color_coding = enabled
        for item in self._scene.items():
            if isinstance(item, InstanceItem):
                item.set_color_coding(enabled)
        self.viewport().update()

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
        self._arrows = []
        self._catalog = {} if parts is None else parts
        self._scene.clear()
        self._scene.addItem(self.rotation_handle)
        catalog = {} if parts is None else parts
        for instance in plan.instances:
            spec = catalog.get(instance.part_id)
            if spec is None:
                continue
            placed = InstanceItem(instance, spec, self._report_group)
            placed.set_color_coding(self._color_coding)
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
        self._place_extras()

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
            if isinstance(hit, ExtendArrow):
                if self._on_extended is not None:
                    self._on_extended(hit.instance_id, hit.connector_index, hit.direction)
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
        for arrow in self._arrows:
            arrow.hide()

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
        self.rotation_handle.set_knob(self._knob_offset(self._rotating_items()))

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
        self._place_arrows()

    def _place_arrows(self) -> None:
        if self._placing:
            return
        self._placing = True
        self._replace_arrows()
        self._placing = False

    def _replace_arrows(self) -> None:
        for arrow in self._arrows:
            if arrow.scene() is self._scene:
                self._scene.removeItem(arrow)
        self._arrows = []
        if self._loading:
            return
        selected = [item for item in self._scene.selectedItems() if isinstance(item, InstanceItem)]
        if len(selected) != 1:
            return
        item = selected[0]
        placed = [(_live_instance(other), other.spec) for other in self._instances()]
        live = _live_instance(item)
        chosen = standard_extend_parts(self._catalog)
        straight = chosen.get(EXTEND_STRAIGHT)
        curve = chosen.get(EXTEND_LEFT)
        straight_spec = None if straight is None else straight[1]
        curve_spec = None if curve is None else curve[1]
        for index, connector in enumerate(item.spec.connectors):
            if connector_occupied(live, connector, placed):
                continue
            outward = normalize_deg(connector.direction_deg + live.rotation_z_deg)
            point = world_xy(live, connector.x_mm, connector.y_mm)
            for direction in offered_extend_directions(live, connector, straight_spec, curve_spec):
                heading = arrow_heading_deg(outward, direction)
                arrow = ExtendArrow(
                    item.item_id,
                    index,
                    direction,
                    _screen_step(heading, ARROW_DISTANCE_PX),
                    heading,
                )
                arrow.setZValue(92 if direction == EXTEND_STRAIGHT else 90)
                self._scene.addItem(arrow)
                arrow.setPos(point[0] * MM, point[1] * MM)
                self._arrows.append(arrow)

    def _instances(self) -> list[InstanceItem]:
        return [item for item in self._scene.items() if isinstance(item, InstanceItem)]

    def _rotating_items(self) -> list[InstanceItem]:
        wanted = set(self._rotate_ids)
        if not wanted:
            return []
        return [
            item
            for item in self._scene.items()
            if isinstance(item, InstanceItem) and item.item_id in wanted
        ]

    def _place_handle(self) -> None:
        selected = [item for item in self._scene.selectedItems() if isinstance(item, InstanceItem)]
        if not selected or self._loading:
            self.rotation_handle.hide()
            return
        center_x = sum(item.pos().x() for item in selected) / len(selected)
        center_y = sum(item.pos().y() for item in selected) / len(selected)
        self.rotation_handle.setPos(center_x, center_y)
        self.rotation_handle.set_knob(self._knob_offset(selected))
        self.rotation_handle.show()
        self.rotation_handle.setZValue(100)

    def _knob_offset(self, selected: list[InstanceItem]) -> QPointF:
        """Screen offset that sits just outside the parts, away from their joints."""
        if not selected:
            return QPointF(0, -HANDLE_GAP_PX)
        union = selected[0].geometry_scene_rect()
        for item in selected[1:]:
            union = union.united(item.geometry_scene_rect())
        center = QPointF(
            sum(item.pos().x() for item in selected) / len(selected),
            sum(item.pos().y() for item in selected) / len(selected),
        )
        view_center = self.mapFromScene(center)
        edges = (
            (QPointF((union.left() + union.right()) / 2, union.top()), QPointF(0, -1)),
            (QPointF(union.right(), (union.top() + union.bottom()) / 2), QPointF(1, 0)),
            (QPointF((union.left() + union.right()) / 2, union.bottom()), QPointF(0, 1)),
            (QPointF(union.left(), (union.top() + union.bottom()) / 2), QPointF(-1, 0)),
        )
        joints: list[QPointF] = []
        for item in selected:
            live = _live_instance(item)
            for connector in item.spec.connectors:
                point = world_xy(live, connector.x_mm, connector.y_mm)
                mapped = self.mapFromScene(QPointF(point[0] * MM, point[1] * MM))
                joints.append(QPointF(mapped))
        best_rank: tuple[float, float] | None = None
        best = QPointF(0, -HANDLE_GAP_PX)
        for mid, normal in edges:
            view_mid = self.mapFromScene(mid)
            nudged = self.mapFromScene(QPointF(mid.x() + normal.x(), mid.y() + normal.y()))
            vx = float(nudged.x() - view_mid.x())
            vy = float(nudged.y() - view_mid.y())
            length = math.hypot(vx, vy)
            if length < 1e-3:
                continue
            knob_view = QPointF(
                view_mid.x() + vx / length * HANDLE_GAP_PX,
                view_mid.y() + vy / length * HANDLE_GAP_PX,
            )
            if joints:
                clearance = min(
                    math.hypot(knob_view.x() - joint.x(), knob_view.y() - joint.y())
                    for joint in joints
                )
            else:
                clearance = 1_000.0
            rank = (clearance, -knob_view.y())
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = QPointF(knob_view.x() - view_center.x(), knob_view.y() - view_center.y())
        return best


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
        self._start_straight = instance.start_straight
        self._color_coding = False
        self._report = report
        self._ready = False
        self._press: QPointF | None = None
        self._starts: dict[str, QPointF] = {}
        self._polygon = QPolygonF(
            [QPointF(x * MM, y * MM) for x, y in spec.outline]
            or [QPointF(-8, -8), QPointF(8, -8), QPointF(8, 8), QPointF(-8, 8)]
        )
        # Stroke and a later shoulder may sit just outside the roadway outline.
        bounds = self._polygon.boundingRect().adjusted(-4, -4, 4, 4)
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

    def geometry_scene_rect(self) -> QRectF:
        """Axis-aligned bounds of the visible outline, without the stroke padding."""
        return self.mapToScene(self._polygon).boundingRect()

    def set_color_coding(self, enabled: bool) -> None:
        if self._color_coding == enabled:
            return
        self._color_coding = enabled
        self.update()

    def paint(self, painter: QPainter, _option: object, _widget: object = None) -> None:
        painter.save()
        painter.scale(MM, MM)
        paint_part(
            painter,
            self.spec,
            color_coding=self._color_coding,
            selected=False,
            start_straight=self._start_straight,
        )
        painter.restore()

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
        self._knob = QPointF(0, -HANDLE_GAP_PX)
        self._bounds = QRectF(-16, -HANDLE_GAP_PX - 16, 32, HANDLE_GAP_PX + 32)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.hide()

    @property
    def knob_offset(self) -> QPointF:
        """Knob centre in unscaled pixels, relative to the rotation centre."""
        return self._knob

    def set_knob(self, knob: QPointF) -> None:
        self.prepareGeometryChange()
        self._knob = knob
        pad = HANDLE_HIT_RADIUS + 2
        left = min(0.0, knob.x()) - pad
        top = min(0.0, knob.y()) - pad
        right = max(0.0, knob.x()) + pad
        bottom = max(0.0, knob.y()) + pad
        self._bounds = QRectF(left, top, right - left, bottom - top)
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def shape(self) -> QPainterPath:
        """Only the knob starts a rotation. The part body underneath stays a move target."""
        path = QPainterPath()
        path.addEllipse(self._knob, HANDLE_HIT_RADIUS, HANDLE_HIT_RADIUS)
        return path

    def paint(self, painter: QPainter, _option: object, _widget: object = None) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS.accent))
        painter.drawEllipse(self._knob, HANDLE_KNOB_RADIUS, HANDLE_KNOB_RADIUS)

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


class ExtendArrow(QGraphicsItem):
    """One continue direction outside a free joint. The glyph is smaller than the hit area."""

    def __init__(
        self,
        instance_id: str,
        connector_index: int,
        direction: str,
        offset: QPointF,
        heading_deg: float,
    ) -> None:
        super().__init__()
        self.instance_id = instance_id
        self.connector_index = connector_index
        self.direction = direction
        self.offset = offset
        self.heading_deg = heading_deg
        self.glyph_px = ARROW_GLYPH_PX
        self.hit_radius = ARROW_HIT_RADIUS
        pad = ARROW_HIT_RADIUS + 1
        self._bounds = QRectF(offset.x() - pad, offset.y() - pad, pad * 2, pad * 2)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setZValue(90)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(self.offset, self.hit_radius, self.hit_radius)
        return path

    def paint(self, painter: QPainter, _option: object, _widget: object = None) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS.accent))
        painter.drawPolygon(_arrow_polygon(self.offset, self.heading_deg, self.glyph_px))


def _live_instance(item: InstanceItem) -> PartInstance:
    return PartInstance(
        item.item_id,
        0,
        item.pos().x() / MM,
        item.pos().y() / MM,
        rotation_z_deg=item.rotation(),
    )


def _screen_step(heading_deg: float, distance: float) -> QPointF:
    """Unscaled pixels along a plan heading. Scene y and screen y both point down."""
    radians = math.radians(heading_deg)
    return QPointF(math.cos(radians) * distance, math.sin(radians) * distance)


def _arrow_polygon(center: QPointF, heading_deg: float, length: float) -> QPolygonF:
    """A filled arrow and nothing around it. ``length`` is the tip-to-tail size in pixels."""
    radians = math.radians(heading_deg)
    forward_x = math.cos(radians)
    forward_y = math.sin(radians)
    side_x = -forward_y
    side_y = forward_x
    tip = QPointF(center.x() + forward_x * length * 0.5, center.y() + forward_y * length * 0.5)
    tail = QPointF(center.x() - forward_x * length * 0.5, center.y() - forward_y * length * 0.5)
    neck = QPointF(center.x() - forward_x * length * 0.05, center.y() - forward_y * length * 0.05)
    wing = length * 0.34
    shaft = length * 0.14
    return QPolygonF(
        [
            tip,
            QPointF(neck.x() + side_x * wing, neck.y() + side_y * wing),
            QPointF(neck.x() + side_x * shaft, neck.y() + side_y * shaft),
            QPointF(tail.x() + side_x * shaft, tail.y() + side_y * shaft),
            QPointF(tail.x() - side_x * shaft, tail.y() - side_y * shaft),
            QPointF(neck.x() - side_x * shaft, neck.y() - side_y * shaft),
            QPointF(neck.x() - side_x * wing, neck.y() - side_y * wing),
        ]
    )


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
