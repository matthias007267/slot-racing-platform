"""Live picture with detection zones the user can draw, move and resize.

Coordinates on screen are pixels of the widget. The stored rectangles are
fractions of the frame, the same numbers the camera configuration keeps.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QWidget

from slot_racing.modules.timing_camera.configuration import (
    NormalizedRoi,
    pixels_to_roi,
    roi_to_pixels,
)
from slot_racing.modules.timing_camera.frames import GrayFrame

MIN_ZONE_PX = 8
_HANDLE_PX = 10
_COLORS = ("#1565c0", "#2e7d32", "#ef6c00", "#6a1b9a", "#00838f")
# The live picture is scaled down to this edge. Zone coordinates stay in the
# full camera frame, so detection is unaffected.
_PREVIEW_MAX_EDGE = 960


class CameraStage(QLabel):
    """The picture and the zones. It does not save anything and opens no camera."""

    selection_changed = Signal(int)
    geometry_changed = Signal()
    zone_drawn = Signal()
    draw_rejected = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("camera-stage")
        self.setMargin(0)
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self._frame_size = (640, 480)
        self._image: QImage | None = None
        self._zones: list[NormalizedRoi] = []
        self._labels: list[str] = []
        self._selected = -1
        self._armed = False
        self._gesture: str | None = None
        self._corner = ""
        self._press = (0, 0)
        self._origin = (0, 0, 0, 0)
        self._draw_to = (0, 0)
        self._rendering = False

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(640, 480)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, 240)

    def set_frame(self, frame: GrayFrame) -> None:
        self._frame_size = (frame.width, frame.height)
        image = _gray_image(frame)
        if image.width() > _PREVIEW_MAX_EDGE or image.height() > _PREVIEW_MAX_EDGE:
            image = image.scaled(
                _PREVIEW_MAX_EDGE,
                _PREVIEW_MAX_EDGE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        self._image = image
        self._render()

    def frame_size(self) -> tuple[int, int]:
        return self._frame_size

    def set_zones(self, zones: Sequence[tuple[NormalizedRoi, str]]) -> None:
        self._zones = [zone for zone, _label in zones]
        self._labels = [label for _zone, label in zones]
        if self._selected >= len(self._zones):
            self._selected = len(self._zones) - 1
        self._render()

    def zones(self) -> tuple[NormalizedRoi, ...]:
        return tuple(self._zones)

    def selected_index(self) -> int:
        return self._selected

    def set_selected(self, index: int) -> None:
        if index < -1 or index >= len(self._zones):
            index = -1
        if index == self._selected:
            return
        self._selected = index
        self._render()
        self.selection_changed.emit(index)

    def set_label(self, index: int, label: str) -> None:
        if 0 <= index < len(self._labels):
            self._labels[index] = label
            self._render()

    def arm_draw(self) -> None:
        self._armed = True
        self._gesture = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def is_armed(self) -> bool:
        return self._armed

    def viewport_rect(self) -> QRect:
        width, height = self._frame_size
        if width < 1 or height < 1 or self.width() < 1 or self.height() < 1:
            return QRect(self.rect())
        scaled_width = self.width()
        scaled_height = int(self.width() * height / width)
        if scaled_height > self.height():
            scaled_height = self.height()
            scaled_width = int(self.height() * width / height)
        x = (self.width() - scaled_width) // 2
        y = (self.height() - scaled_height) // 2
        return QRect(x, y, max(1, scaled_width), max(1, scaled_height))

    def image_to_widget(self, x: int, y: int) -> QPoint:
        rect = self.viewport_rect()
        frame_width, frame_height = self._frame_size
        return QPoint(
            rect.x() + int(x * rect.width() / frame_width),
            rect.y() + int(y * rect.height() / frame_height),
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._image_point(event.position().toPoint())
        if point is None:
            return
        if self._armed:
            self._gesture = "draw"
            self._press = point
            self._draw_to = point
            self._render()
            return
        if self._selected >= 0:
            corner = self._corner_at(event.position().toPoint(), self._selected)
            if corner is not None:
                self._begin_resize(corner, point)
                return
        hit = self._zone_at(point)
        if hit is None:
            self.set_selected(-1)
            return
        self.set_selected(hit)
        self._gesture = "move"
        self._press = point
        self._origin = self._pixels(hit)
        self._render()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        point = self._image_point(event.position().toPoint())
        if point is None or self._gesture is None:
            return
        if self._gesture == "draw":
            self._draw_to = point
            self._render()
            return
        if self._gesture == "move":
            self._move_to(point)
            return
        if self._gesture == "resize":
            self._resize_to(point)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._gesture is None:
            return
        point = self._image_point(event.position().toPoint())
        gesture = self._gesture
        if point is not None:
            if gesture == "draw":
                self._draw_to = point
            elif gesture == "move":
                self._move_to(point)
            elif gesture == "resize":
                self._resize_to(point)
        self._gesture = None
        if gesture == "draw":
            self._finish_draw()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._render()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        """Draw into a pixmap. A painter on this widget crashes under offscreen Qt."""
        if self._rendering or self.width() < 1 or self.height() < 1:
            return
        self._rendering = True
        try:
            canvas = QImage(self.size(), QImage.Format.Format_RGB32)
            canvas.fill(QColor("#1b1b1b"))
            painter = QPainter(canvas)
            rect = self.viewport_rect()
            image = self._image
            if image is not None:
                painter.drawImage(rect, image)
            else:
                painter.fillRect(rect, QColor("#263238"))
            for index, zone in enumerate(self._zones):
                self._paint_zone(painter, index, zone, selected=index == self._selected)
            if self._gesture == "draw":
                self._paint_rubber(painter)
            painter.end()
            self.setPixmap(QPixmap.fromImage(canvas))
        finally:
            self._rendering = False

    def _finish_draw(self) -> None:
        self._armed = False
        rect = _normalize(*self._press, *self._draw_to)
        rect = _clamp(*rect, *self._frame_size)
        _x, _y, width, height = rect
        if width < MIN_ZONE_PX or height < MIN_ZONE_PX:
            self.draw_rejected.emit()
            return
        self._zones.append(pixels_to_roi(*rect, *self._frame_size))
        self._labels.append("")
        self._selected = len(self._zones) - 1
        self.zone_drawn.emit()
        self.selection_changed.emit(self._selected)
        self.geometry_changed.emit()

    def _begin_resize(self, corner: str, point: tuple[int, int]) -> None:
        self._gesture = "resize"
        self._corner = corner
        self._press = point
        self._origin = self._pixels(self._selected)

    def _move_to(self, point: tuple[int, int]) -> None:
        x, y, width, height = self._origin
        frame_width, frame_height = self._frame_size
        moved_x = x + point[0] - self._press[0]
        moved_y = y + point[1] - self._press[1]
        moved_x = min(max(0, moved_x), frame_width - width)
        moved_y = min(max(0, moved_y), frame_height - height)
        if (moved_x, moved_y, width, height) == self._pixels(self._selected):
            return
        self._zones[self._selected] = pixels_to_roi(
            moved_x, moved_y, width, height, frame_width, frame_height
        )
        self.geometry_changed.emit()
        self._render()

    def _resize_to(self, point: tuple[int, int]) -> None:
        x, y, width, height = self._origin
        right = x + width
        bottom = y + height
        px, py = point
        if "l" in self._corner:
            x = px
        else:
            right = px
        if "t" in self._corner:
            y = py
        else:
            bottom = py
        rect = _clamp(*_normalize(x, y, right, bottom), *self._frame_size)
        rect = _enforce_minimum(rect, self._origin, self._corner, self._frame_size)
        if rect == self._pixels(self._selected):
            return
        self._zones[self._selected] = pixels_to_roi(*rect, *self._frame_size)
        self.geometry_changed.emit()
        self._render()

    def _pixels(self, index: int) -> tuple[int, int, int, int]:
        roi = roi_to_pixels(self._zones[index], *self._frame_size)
        return roi.x, roi.y, roi.width, roi.height

    def _zone_at(self, point: tuple[int, int]) -> int | None:
        px, py = point
        for index in range(len(self._zones) - 1, -1, -1):
            x, y, width, height = self._pixels(index)
            if x <= px < x + width and y <= py < y + height:
                return index
        return None

    def _corner_at(self, widget_point: QPoint, index: int) -> str | None:
        x, y, width, height = self._pixels(index)
        corners = {
            "tl": (x, y),
            "tr": (x + width, y),
            "bl": (x, y + height),
            "br": (x + width, y + height),
        }
        for name, (cx, cy) in corners.items():
            center = self.image_to_widget(cx, cy)
            if (
                abs(widget_point.x() - center.x()) <= _HANDLE_PX
                and abs(widget_point.y() - center.y()) <= _HANDLE_PX
            ):
                return name
        return None

    def _image_point(self, widget_point: QPoint) -> tuple[int, int] | None:
        rect = self.viewport_rect()
        if not rect.contains(widget_point):
            return None
        frame_width, frame_height = self._frame_size
        x = int((widget_point.x() - rect.x()) * frame_width / rect.width())
        y = int((widget_point.y() - rect.y()) * frame_height / rect.height())
        x = min(max(0, x), frame_width - 1)
        y = min(max(0, y), frame_height - 1)
        return x, y

    def _paint_zone(
        self, painter: QPainter, index: int, zone: NormalizedRoi, *, selected: bool
    ) -> None:
        x, y, width, height = self._pixels(index)
        top_left = self.image_to_widget(x, y)
        bottom_right = self.image_to_widget(x + width, y + height)
        rect = QRect(top_left, bottom_right)
        color = QColor("#f9a825" if selected else _COLORS[index % len(_COLORS)])
        pen = QPen(color)
        pen.setWidth(3 if selected else 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        painter.drawText(
            rect.adjusted(4, 4, -4, -4),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            self._labels[index],
        )
        if selected:
            for cx, cy in (
                (x, y),
                (x + width, y),
                (x, y + height),
                (x + width, y + height),
            ):
                center = self.image_to_widget(cx, cy)
                painter.fillRect(center.x() - 4, center.y() - 4, 8, 8, color)

    def _paint_rubber(self, painter: QPainter) -> None:
        rect = _normalize(*self._press, *self._draw_to)
        top_left = self.image_to_widget(rect[0], rect[1])
        bottom_right = self.image_to_widget(rect[0] + rect[2], rect[1] + rect[3])
        pen = QPen(QColor("#f9a825"))
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(QRect(top_left, bottom_right))


def _gray_image(frame: GrayFrame) -> QImage:
    """A grayscale image that owns its pixels.

    Building it from an external buffer would leave the image pointing at
    memory that disappears with the ``bytes`` object. Qt then crashes while
    painting.
    """
    image = QImage(frame.width, frame.height, QImage.Format.Format_Grayscale8)
    view = cast(memoryview, image.bits())
    raw = frame.to_bytes()
    stride = image.bytesPerLine()
    try:
        if stride == frame.width:
            view[: len(raw)] = raw
        else:
            for row in range(frame.height):
                src = row * frame.width
                dst = row * stride
                view[dst : dst + frame.width] = raw[src : src + frame.width]
    finally:
        view.release()
    # ``bits()`` locks the image. Paint a copy that nobody has locked, or a
    # later garbage collection destroys it while a frame is on screen.
    return image.copy()


def _normalize(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    return left, top, max(0, right - left), max(0, bottom - top)


def _clamp(
    x: int, y: int, width: int, height: int, frame_width: int, frame_height: int
) -> tuple[int, int, int, int]:
    width = min(max(1, width), frame_width)
    height = min(max(1, height), frame_height)
    x = min(max(0, x), frame_width - width)
    y = min(max(0, y), frame_height - height)
    return x, y, width, height


def _enforce_minimum(
    rect: tuple[int, int, int, int],
    origin: tuple[int, int, int, int],
    corner: str,
    frame: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Keep the dragged corner from collapsing the zone below :data:`MIN_ZONE_PX`."""
    x, y, width, height = rect
    ox, oy, owidth, oheight = origin
    frame_width, frame_height = frame
    minimum_w = min(MIN_ZONE_PX, frame_width)
    minimum_h = min(MIN_ZONE_PX, frame_height)
    if width < minimum_w:
        if "l" in corner:
            x = ox + owidth - minimum_w
        width = minimum_w
    if height < minimum_h:
        if "t" in corner:
            y = oy + oheight - minimum_h
        height = minimum_h
    return _clamp(x, y, width, height, frame_width, frame_height)
