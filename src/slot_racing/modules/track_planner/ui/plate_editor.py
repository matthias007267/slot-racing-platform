"""Point-and-click floor plate. Later elements stay disabled until they exist."""

from __future__ import annotations

import math
from collections.abc import Callable
from itertools import pairwise

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.parts import PartSpec
from slot_racing.modules.track_planner.plate_session import (
    PlateSession,
    plate_corners,
    plate_part,
    stored_plate,
)
from slot_racing.modules.track_planner.ui.track_paint import paint_part
from slot_racing.uikit.theme import COLORS, set_role, set_tone

_SELECT = "select"
_DRAW = "draw"
_HIT_PX = 10.0
_LATER = (
    ("plate-lane", "planner.plate.lane"),
    ("plate-center", "planner.plate.center"),
    ("plate-start", "planner.plate.start"),
    ("plate-border", "planner.plate.border"),
    ("plate-rail", "planner.plate.rail"),
    ("plate-edge", "planner.plate.edge"),
)


class PlateEditor(QWidget):
    """Sidebar, canvas and properties for one floor plate."""

    changed = Signal()

    def __init__(self, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("plate-editor")
        self._translator = translator
        self._mode = _SELECT
        self._editable = True
        self._reference: PartSpec | None = None
        self._filling = False
        self._last_token: tuple[object, ...] = ()
        self.session = PlateSession()
        translate = translator.translate
        self.canvas = PlateCanvas(self)
        self.select_button = self._mode_button("plate-select", "planner.plate.select")
        self.floor_button = self._mode_button("plate-floor", "planner.plate.floor")
        self.select_button.setChecked(True)
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        modes.addButton(self.select_button)
        modes.addButton(self.floor_button)
        self.select_button.toggled.connect(self._mode_changed)
        self.close_button = self._action("plate-close", "planner.plate.close", self._close)
        self.replace_button = self._action("plate-replace", "planner.plate.replace", self._replace)
        self.undo_button = self._action("plate-undo", "planner.plate.undo", self._undo)
        self.redo_button = self._action("plate-redo", "planner.plate.redo", self._redo)
        self.fit_button = self._action("plate-fit", "planner.plate.fit", self.canvas.fit)
        self.count_label = QLabel("0")
        self.count_label.setObjectName("plate-count")
        self.state_label = QLabel(translate("planner.plate.open_state"))
        self.state_label.setObjectName("plate-state")
        self.perimeter_label = QLabel("—")
        self.perimeter_label.setObjectName("plate-perimeter")
        self.x_spin = self._coord("plate-x")
        self.y_spin = self._coord("plate-y")
        self.x_spin.valueChanged.connect(self._preview_spin)
        self.y_spin.valueChanged.connect(self._preview_spin)
        self.x_spin.editingFinished.connect(self._commit_spin)
        self.y_spin.editingFinished.connect(self._commit_spin)
        self.issue = QLabel()
        self.issue.setObjectName("plate-issue")
        self.issue.setWordWrap(True)
        self.cursor_label = QLabel("—")
        self.cursor_label.setObjectName("plate-cursor")
        side = QVBoxLayout()
        heading = QLabel(translate("planner.plate.elements"))
        set_role(heading, "section")
        side.addWidget(heading)
        side.addWidget(self.select_button)
        side.addWidget(self.floor_button)
        for name, key in _LATER:
            button = QPushButton(translate(key))
            button.setObjectName(name)
            button.setEnabled(False)
            button.setToolTip(translate("planner.plate.later"))
            side.addWidget(button)
        side.addStretch(1)
        side.addWidget(self.close_button)
        side.addWidget(self.replace_button)
        side.addWidget(self.undo_button)
        side.addWidget(self.redo_button)
        side.addWidget(self.fit_button)
        form = QFormLayout()
        form.addRow(translate("planner.plate.count"), self.count_label)
        form.addRow(translate("planner.plate.state"), self.state_label)
        form.addRow(translate("planner.plate.perimeter"), self.perimeter_label)
        form.addRow(translate("planner.plate.x"), self.x_spin)
        form.addRow(translate("planner.plate.y"), self.y_spin)
        properties = QWidget()
        properties.setObjectName("plate-properties")
        column = QVBoxLayout(properties)
        column.addLayout(form)
        column.addWidget(self.issue)
        column.addWidget(self.cursor_label)
        column.addStretch(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        host = QWidget()
        host.setLayout(side)
        host.setFixedWidth(196)
        layout.addWidget(host)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(properties)
        self._refresh()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def editable(self) -> bool:
        return self._editable

    @property
    def reference(self) -> PartSpec | None:
        return self._reference

    def load(self, spec: PartSpec | None) -> None:
        """Blank plate, an editable stored plate, or a read-only existing part."""
        corners = None if spec is None else plate_corners(spec)
        if spec is None:
            self.session = PlateSession()
            self._editable = True
            self._reference = None
        elif corners is not None:
            self.session = PlateSession(corners, closed=True)
            self._editable = True
            self._reference = None
        else:
            self.session = PlateSession()
            self._editable = False
            self._reference = spec
        self.session.capture()
        self._last_token = self.session.geometry_token()
        self.select_button.setChecked(True)
        self._mode = _SELECT
        self._refresh()
        self.canvas.fit()

    def capture(self) -> None:
        self.session.capture()
        self._last_token = self.session.geometry_token()

    def geometry_token(self) -> tuple[object, ...]:
        return self.session.geometry_token()

    def geometry_dirty(self) -> bool:
        return self._editable and self.session.geometry_dirty()

    def commit_pending(self) -> None:
        self.canvas.release_drag()
        self._commit_spin()

    def to_spec(
        self,
        article_number: str,
        scale: str,
        name: str,
        manufacturer: str,
        description: str,
    ) -> PartSpec:
        self.commit_pending()
        issue = self.session.save_issue()
        if issue is not None:
            raise ValidationError(issue)
        return stored_plate(
            self.session.points,
            article_number=article_number,
            scale=scale,
            name=name,
            manufacturer=manufacturer,
            description=description,
        )

    def set_cursor(self, point: tuple[float, float] | None) -> None:
        if point is None:
            self.cursor_label.setText("—")
            return
        self.cursor_label.setText(f"X {point[0]:.1f} mm    Y {point[1]:.1f} mm")

    def note_change(self) -> None:
        self._refresh()

    def _mode_button(self, name: str, key: str) -> QPushButton:
        button = QPushButton(self._translator.translate(key))
        button.setObjectName(name)
        button.setCheckable(True)
        return button

    def _action(self, name: str, key: str, slot: Callable[[], None]) -> QPushButton:
        button = QPushButton(self._translator.translate(key))
        button.setObjectName(name)
        button.clicked.connect(lambda _checked=False, call=slot: call())
        return button

    def _coord(self, name: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setObjectName(name)
        spin.setRange(-100_000, 100_000)
        spin.setDecimals(3)
        spin.setSingleStep(1)
        return spin

    def _mode_changed(self, _checked: bool = False) -> None:
        self._mode = _DRAW if self.floor_button.isChecked() else _SELECT
        self.canvas.update()

    def _close(self) -> None:
        self.session.close()
        self.note_change()

    def _replace(self) -> None:
        self.session.replace()
        self.floor_button.setChecked(True)
        self.note_change()

    def _undo(self) -> None:
        self.session.undo()
        self.note_change()

    def _redo(self) -> None:
        self.session.redo()
        self.note_change()

    def _preview_spin(self) -> None:
        if self._filling or self.session.selected is None:
            return
        self.session.preview_selected(self.x_spin.value(), self.y_spin.value())
        self.canvas.update()
        self._show_status()

    def _commit_spin(self) -> None:
        if self._filling or not self._editable or self.session.selected is None:
            return
        self.session.commit_selected(self.x_spin.value(), self.y_spin.value())
        self.note_change()

    def _refresh(self) -> None:
        session = self.session
        translate = self._translator.translate
        drawing = self._editable
        self._filling = True
        self.select_button.setEnabled(drawing)
        self.floor_button.setEnabled(drawing)
        self.close_button.setEnabled(drawing and session.can_close())
        self.replace_button.setEnabled(drawing and session.can_replace())
        self.undo_button.setEnabled(drawing and session.can_undo())
        self.redo_button.setEnabled(drawing and session.can_redo())
        self.count_label.setText(str(len(session.display_points())))
        state_key = "planner.plate.closed_state" if session.closed else "planner.plate.open_state"
        self.state_label.setText(translate(state_key))
        perimeter = session.perimeter_mm()
        self.perimeter_label.setText("—" if perimeter is None else f"{perimeter:.3f}")
        selected = session.selected
        points = session.display_points()
        has_point = selected is not None and 0 <= selected < len(points)
        self.x_spin.setEnabled(drawing and has_point)
        self.y_spin.setEnabled(drawing and has_point)
        if has_point and selected is not None:
            self.x_spin.setValue(points[selected][0])
            self.y_spin.setValue(points[selected][1])
        self._filling = False
        self._show_status()
        self.canvas.update()
        token = session.geometry_token()
        if token != self._last_token:
            self._last_token = token
            self.changed.emit()

    def _show_status(self) -> None:
        if not self._editable and self._reference is not None:
            self.issue.setText(self._translator.translate("planner.plate.locked"))
            set_tone(self.issue, "warning")
            return
        key = self.session.status_key()
        self.issue.setText("" if key is None else self._translator.translate(key))
        set_tone(self.issue, "danger" if key else "")


class PlateCanvas(QWidget):
    """Millimetre canvas. The session decides what a click changes."""

    def __init__(self, editor: PlateEditor) -> None:
        super().__init__(editor)
        self.setObjectName("plate-canvas")
        self.setMinimumSize(360, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._editor = editor
        self._scale = 1.0
        self._pan_x = 40.0
        self._pan_y = 40.0
        self._hold_view = False
        self._dragging = False
        self._panning = False
        self._pan_from = QPointF()
        self._pan_origin = (0.0, 0.0)

    def set_view(self, scale: float, pan_x: float, pan_y: float) -> None:
        self._scale = min(40.0, max(0.05, scale))
        self._pan_x = pan_x
        self._pan_y = pan_y
        self._hold_view = True
        self.update()

    def widget_point(self, x_mm: float, y_mm: float) -> QPoint:
        return QPoint(
            round(x_mm * self._scale + self._pan_x),
            round(y_mm * self._scale + self._pan_y),
        )

    def fit_rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        if self.width() < 2 or self.height() < 2:
            return
        width = self.width()
        height = self.height()
        span_x = max(abs(x1 - x0), 1.0)
        span_y = max(abs(y1 - y0), 1.0)
        margin = 36.0
        scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
        center_x = (min(x0, x1) + max(x0, x1)) / 2.0
        center_y = (min(y0, y1) + max(y0, y1)) / 2.0
        self.set_view(scale, width / 2.0 - center_x * scale, height / 2.0 - center_y * scale)

    def fit(self) -> None:
        session = self._editor.session
        points = list(session.display_points())
        reference = self._editor.reference
        if not points and reference is not None and reference.outline:
            points = list(reference.outline)
        if not points:
            self.fit_rect(-20.0, -20.0, 400.0, 250.0)
            return
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        self.fit_rect(min(xs), min(ys), max(xs), max(ys))

    def release_drag(self) -> None:
        if not self._dragging:
            return
        self._dragging = False
        if self.mouseGrabber() is self:
            self.releaseMouse()
        self._editor.session.commit_move()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._hold_view:
            self.fit()

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS.surface))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(self._pan_x, self._pan_y)
        painter.scale(self._scale, self._scale)
        self._grid(painter)
        self._body(painter)
        self._handles(painter)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            self._dragging = False
            self._pan_from = event.position()
            self._pan_origin = (self._pan_x, self._pan_y)
            self._panning = True
            event.accept()
            return
        self._panning = False
        if event.button() != Qt.MouseButton.LeftButton or not self._editor.editable:
            return
        self.setFocus()
        x_mm, y_mm = self._world(event.position())
        session = self._editor.session
        tolerance = _HIT_PX / self._scale
        if self._editor.mode == _DRAW and not session.closed:
            if session.near_start(x_mm, y_mm, tolerance):
                session.close()
            else:
                session.add_point(x_mm, y_mm)
            self._editor.note_change()
            return
        if self._editor.mode == _DRAW and session.closed:
            session.reject_second()
        index = session.hit(x_mm, y_mm, tolerance)
        session.select(index)
        if index is not None and self._editor.mode == _SELECT:
            session.begin_move(index)
            self._dragging = True
            self.grabMouse()
        self._panning = False
        self._editor.note_change()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if getattr(self, "_panning", False) and event.buttons() & Qt.MouseButton.MiddleButton:
            delta = event.position() - self._pan_from
            self._pan_x = self._pan_origin[0] + delta.x()
            self._pan_y = self._pan_origin[1] + delta.y()
            self._hold_view = True
            self.update()
            return
        x_mm, y_mm = self._world(event.position())
        self._editor.set_cursor((x_mm, y_mm))
        session = self._editor.session
        if self._dragging:
            session.preview_move(x_mm, y_mm)
            self._editor.note_change()
            return
        drawing = self._editor.editable and self._editor.mode == _DRAW
        if drawing and not session.closed and session.points:
            session.set_hover((x_mm, y_mm))
        else:
            session.set_hover(None)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.release_drag()
            self._editor.note_change()

    def mouseLeaveEvent(self, _event: QMouseEvent) -> None:  # noqa: N802
        self._editor.session.set_hover(None)
        self._editor.set_cursor(None)
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120.0
        if steps == 0.0:
            return
        before = self._world(event.position())
        self._scale = min(40.0, max(0.05, self._scale * (1.1**steps)))
        self._pan_x = event.position().x() - before[0] * self._scale
        self._pan_y = event.position().y() - before[1] * self._scale
        self._hold_view = True
        self.update()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self._editor.editable:
            if self._dragging:
                self._dragging = False
                if self.mouseGrabber() is self:
                    self.releaseMouse()
                self._editor.session.cancel_move()
            else:
                self._editor.session.cancel_open()
            self._editor.note_change()
            return
        control = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        if control and self._editor.editable:
            if event.key() == Qt.Key.Key_Z and shift:
                self._editor.session.redo()
                self._editor.note_change()
                return
            if event.key() == Qt.Key.Key_Z:
                self._editor.session.undo()
                self._editor.note_change()
                return
            if event.key() == Qt.Key.Key_Y:
                self._editor.session.redo()
                self._editor.note_change()
                return
        super().keyPressEvent(event)

    def _world(self, pos: QPointF) -> tuple[float, float]:
        return ((pos.x() - self._pan_x) / self._scale, (pos.y() - self._pan_y) / self._scale)

    def _grid(self, painter: QPainter) -> None:
        width = max(self.width(), 1)
        height = max(self.height(), 1)
        x0, y0 = self._world(QPointF(0, 0))
        x1, y1 = self._world(QPointF(width, height))
        left, right = min(x0, x1), max(x0, x1)
        top, bottom = min(y0, y1), max(y0, y1)
        step = self._grid_step()
        pen = QPen(QColor(COLORS.border))
        pen.setCosmetic(True)
        painter.setPen(pen)
        start_x = math.floor(left / step) * step
        start_y = math.floor(top / step) * step
        x_mm = start_x
        while x_mm <= right:
            painter.drawLine(QPointF(x_mm, top), QPointF(x_mm, bottom))
            x_mm += step
        y_mm = start_y
        while y_mm <= bottom:
            painter.drawLine(QPointF(left, y_mm), QPointF(right, y_mm))
            y_mm += step
        axis = QPen(QColor(COLORS.text_muted))
        axis.setCosmetic(True)
        painter.setPen(axis)
        painter.drawLine(QPointF(left, 0.0), QPointF(right, 0.0))
        painter.drawLine(QPointF(0.0, top), QPointF(0.0, bottom))

    def _grid_step(self) -> float:
        target = 80.0 / self._scale
        for step in (1.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0):
            if step >= target:
                return step
        return 500.0

    def _body(self, painter: QPainter) -> None:
        session = self._editor.session
        spec = self._paint_spec()
        if spec is not None:
            paint_part(
                painter,
                spec,
                color_coding=False,
                selected=False,
                start_straight=False,
            )
        points = session.display_points()
        if len(points) >= 2 and spec is None:
            pen = QPen(QColor(COLORS.error if session.closed else COLORS.info))
            pen.setCosmetic(True)
            pen.setWidthF(1.4)
            painter.setPen(pen)
            for start, end in pairwise(points):
                painter.drawLine(QPointF(*start), QPointF(*end))
            if session.closed:
                painter.drawLine(QPointF(*points[-1]), QPointF(*points[0]))
        hover = session.hover
        if hover is not None and points and not session.closed:
            pen = QPen(QColor(COLORS.info))
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(*points[-1]), QPointF(*hover))

    def _paint_spec(self) -> PartSpec | None:
        if not self._editor.editable and self._editor.reference is not None:
            return self._editor.reference
        session = self._editor.session
        if not session.closed:
            return None
        try:
            return plate_part(session.display_points())
        except ValidationError:
            return None

    def _handles(self, painter: QPainter) -> None:
        if not self._editor.editable:
            return
        session = self._editor.session
        points = session.display_points()
        radius = 6.0 / self._scale
        for index, point in enumerate(points):
            selected = index == session.selected
            start = index == 0 and not session.closed
            color = COLORS.accent if selected else COLORS.warning if start else COLORS.elevated
            painter.setBrush(QColor(color))
            pen = QPen(QColor(COLORS.text))
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawEllipse(QPointF(*point), radius * (1.35 if start else 1.0), radius)
