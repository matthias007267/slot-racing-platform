"""Draw a calibration polygon on the live picture.

Widget coordinates follow the letterboxed preview. Stored points stay fractions
of the camera frame, so a resize does not move the outline.
"""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
    QResizeEvent,
)
from PySide6.QtWidgets import QLabel, QWidget

from slot_racing.modules.timing_camera.calibration_polygon import (
    MAX_POINTS,
    CalibrationPolygon,
)
from slot_racing.modules.timing_camera.configuration import NormalizedRoi, roi_to_pixels
from slot_racing.modules.timing_camera.frames import GrayFrame

_HIT_PX = 10
_PREVIEW_MAX_EDGE = 960


class PolygonStage(QLabel):
    """The picture, the search polygon and optional zone rectangles."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calibration-stage")
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self._frame_size = (640, 480)
        self._image: QImage | None = None
        self._points: list[tuple[float, float]] = []
        self._drag: int | None = None
        self._current: NormalizedRoi | None = None
        self._proposed: NormalizedRoi | None = None
        self._rendering = False

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

    def set_polygon(self, polygon: CalibrationPolygon | None) -> None:
        self._points = [] if polygon is None else list(polygon.points)
        self.changed.emit()
        self._render()

    def polygon(self) -> CalibrationPolygon:
        return CalibrationPolygon(tuple(self._points))

    def set_zones(self, current: NormalizedRoi | None, proposed: NormalizedRoi | None) -> None:
        self._current = current
        self._proposed = proposed
        self._render()

    def add_point(self, x: float, y: float) -> None:
        if len(self._points) >= MAX_POINTS:
            return
        self._points.append((float(x), float(y)))
        self.changed.emit()
        self._render()

    def move_point(self, index: int, x: float, y: float) -> None:
        if index < 0 or index >= len(self._points):
            raise IndexError("point index is outside the polygon")
        self._points[index] = (min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y))))
        self.changed.emit()
        self._render()

    def remove_point(self, index: int) -> None:
        if index < 0 or index >= len(self._points):
            raise IndexError("point index is outside the polygon")
        del self._points[index]
        self.changed.emit()
        self._render()

    def reset(self) -> None:
        self._points.clear()
        self._drag = None
        self.changed.emit()
        self._render()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        point = self._image_point(event.position().toPoint())
        if point is None:
            return
        nearest = self._nearest(event.position().toPoint())
        if event.button() == Qt.MouseButton.RightButton and nearest is not None:
            self.remove_point(nearest)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if nearest is not None:
            self._drag = nearest
            return
        width, height = self._frame_size
        self.add_point(point[0] / width, point[1] / height)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag is None:
            return
        point = self._image_point(event.position().toPoint())
        if point is None:
            return
        width, height = self._frame_size
        self.move_point(self._drag, point[0] / width, point[1] / height)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._render()

    def viewport_rect(self) -> QRect:
        width, height = self._frame_size
        if width < 1 or height < 1 or self.width() < 1 or self.height() < 1:
            return QRect(self.rect())
        scaled_width = self.width()
        scaled_height = int(self.width() * height / width)
        if scaled_height > self.height():
            scaled_height = self.height()
            scaled_width = int(self.height() * width / height)
        x_value = (self.width() - scaled_width) // 2
        y_value = (self.height() - scaled_height) // 2
        return QRect(x_value, y_value, max(1, scaled_width), max(1, scaled_height))

    def _nearest(self, widget_point: QPoint) -> int | None:
        best: int | None = None
        best_distance = _HIT_PX * _HIT_PX
        for index, (x_value, y_value) in enumerate(self._points):
            placed = self._to_widget(x_value, y_value)
            distance = (placed.x() - widget_point.x()) ** 2 + (placed.y() - widget_point.y()) ** 2
            if distance <= best_distance:
                best = index
                best_distance = distance
        return best

    def _image_point(self, widget_point: QPoint) -> tuple[int, int] | None:
        rect = self.viewport_rect()
        if not rect.contains(widget_point):
            return None
        frame_width, frame_height = self._frame_size
        x_value = int((widget_point.x() - rect.x()) * frame_width / rect.width())
        y_value = int((widget_point.y() - rect.y()) * frame_height / rect.height())
        return min(max(0, x_value), frame_width - 1), min(max(0, y_value), frame_height - 1)

    def _to_widget(self, x_value: float, y_value: float) -> QPoint:
        rect = self.viewport_rect()
        return QPoint(
            rect.x() + int(x_value * rect.width()),
            rect.y() + int(y_value * rect.height()),
        )

    def _render(self) -> None:
        if self._rendering or self.width() < 1 or self.height() < 1:
            return
        self._rendering = True
        try:
            canvas = QImage(self.size(), QImage.Format.Format_RGB32)
            canvas.fill(QColor("#1b1b1b"))
            painter = QPainter(canvas)
            rect = self.viewport_rect()
            if self._image is not None:
                painter.drawImage(rect, self._image)
            else:
                painter.fillRect(rect, QColor("#263238"))
            self._paint_roi(painter, self._current, "#1565c0", dashed=True)
            self._paint_roi(painter, self._proposed, "#2FBF71", dashed=False)
            if len(self._points) >= 2:
                widget_points = [
                    self._to_widget(x_value, y_value) for x_value, y_value in self._points
                ]
                polygon = QPolygon(widget_points)
                color = QColor("#1565c0")
                color.setAlpha(70)
                painter.setBrush(color)
                painter.setPen(QPen(QColor("#1565c0"), 2))
                painter.drawPolygon(polygon)
            painter.setBrush(QColor("#1565c0"))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            for x_value, y_value in self._points:
                painter.drawEllipse(self._to_widget(x_value, y_value), 5, 5)
            painter.end()
            self.setPixmap(QPixmap.fromImage(canvas))
        finally:
            self._rendering = False

    def _paint_roi(
        self, painter: QPainter, roi: NormalizedRoi | None, color: str, *, dashed: bool
    ) -> None:
        if roi is None:
            return
        width, height = self._frame_size
        try:
            pixels = roi_to_pixels(roi, width, height)
        except ValueError:
            return
        top_left = self._to_widget(pixels.x / width, pixels.y / height)
        bottom_right = self._to_widget(
            (pixels.x + pixels.width) / width,
            (pixels.y + pixels.height) / height,
        )
        pen = QPen(QColor(color), 2)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRect(top_left, bottom_right))


def _gray_image(frame: GrayFrame) -> QImage:
    """A grayscale image that owns its pixels. An external buffer would dangle."""
    image = QImage(frame.width, frame.height, QImage.Format.Format_Grayscale8)
    view = cast(memoryview, image.bits())
    raw = frame.to_bytes()
    stride = image.bytesPerLine()
    if stride == frame.width:
        view[: len(raw)] = raw
    else:
        for row in range(frame.height):
            src = row * frame.width
            dst = row * stride
            view[dst : dst + frame.width] = raw[src : src + frame.width]
    return image
