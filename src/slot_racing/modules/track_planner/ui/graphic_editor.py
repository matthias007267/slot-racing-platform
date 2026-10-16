"""Canvas, tool bar, element list and properties for one part.

Coordinates are millimetres. The session decides what a click changes. The
painter is the same one the plan uses once a body can be compiled.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.geometry import CubicBezier
from slot_racing.modules.track_planner.graphic_session import (
    KIND_CENTER,
    KIND_JOINT,
    KIND_LANE,
    KIND_START,
    TOOL_ARC,
    TOOL_BEZIER,
    TOOL_CENTER,
    TOOL_JOINT,
    TOOL_LANE,
    TOOL_LINE,
    TOOL_PLATE,
    TOOL_SELECT,
    TOOL_START,
    EditorElement,
    GraphicSession,
)
from slot_racing.modules.track_planner.parts import (
    SPAN_ARC,
    PartSpec,
    SlotSpan,
    as_segment,
)
from slot_racing.modules.track_planner.ui.track_paint import paint_part
from slot_racing.uikit.theme import COLORS, set_role

_HIT_PX = 10.0
_TOOLS = (
    (TOOL_SELECT, "graphic-select", "planner.editor.tool.select"),
    (TOOL_LINE, "graphic-line", "planner.editor.tool.line"),
    (TOOL_ARC, "graphic-arc", "planner.editor.tool.arc"),
    (TOOL_BEZIER, "graphic-bezier", "planner.editor.tool.bezier"),
    (TOOL_PLATE, "graphic-plate", "planner.editor.tool.plate"),
    (TOOL_LANE, "graphic-lane", "planner.editor.tool.lane"),
    (TOOL_JOINT, "graphic-joint", "planner.editor.tool.joint"),
    (TOOL_START, "graphic-start", "planner.editor.tool.start"),
    (TOOL_CENTER, "graphic-center", "planner.editor.tool.center"),
)


def _offscreen() -> bool:
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return True
    app = QGuiApplication.instance()
    return isinstance(app, QGuiApplication) and app.platformName() == "offscreen"


class GraphicEditor(QWidget):
    """Tools and the drawing of one part. Metadata stays in the dialog."""

    changed = Signal()

    def __init__(self, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("graphic-editor")
        self._translator = translator
        self.session = GraphicSession()
        self.editable = True
        self._filling = False
        self.spec_provider: Callable[[], PartSpec | None] = lambda: None
        translate = translator.translate
        self.canvas = GraphicCanvas(self)
        self.issue = QLabel()
        self.issue.setObjectName("graphic-issue")
        self.issue.setWordWrap(True)
        self.tools = QButtonGroup(self)
        self.tools.setExclusive(True)
        tool_row = QHBoxLayout()
        tool_row.setContentsMargins(0, 0, 0, 0)
        self._tool_buttons: dict[str, QPushButton] = {}
        for tool, object_name, key in _TOOLS:
            button = QPushButton(translate(key))
            button.setObjectName(object_name)
            button.setCheckable(True)
            button.setToolTip(translate(key))
            self.tools.addButton(button)
            self._tool_buttons[tool] = button
            tool_row.addWidget(button)
        self._tool_buttons[TOOL_SELECT].setChecked(True)
        self.tools.buttonClicked.connect(self._tool_clicked)
        self.finish_button = self._button("graphic-finish", "planner.editor.finish", self._finish)
        tool_row.addWidget(self.finish_button)
        tool_row.addStretch(1)
        self.undo_button = self._button("graphic-undo", "planner.editor.undo", self._undo)
        self.redo_button = self._button("graphic-redo", "planner.editor.redo", self._redo)
        self.fit_button = self._button("graphic-fit", "planner.editor.fit", self.canvas.fit)
        self.rotate_button = self._button("graphic-rotate", "planner.editor.rotate", self._rotate)
        self.mirror_x_button = self._button(
            "graphic-mirror-x", "planner.editor.mirror_x", self._mirror_x
        )
        self.mirror_y_button = self._button(
            "graphic-mirror-y", "planner.editor.mirror_y", self._mirror_y
        )
        edit_row = QHBoxLayout()
        edit_row.setContentsMargins(0, 0, 0, 0)
        for button in (
            self.undo_button,
            self.redo_button,
            self.fit_button,
            self.rotate_button,
            self.mirror_x_button,
            self.mirror_y_button,
        ):
            edit_row.addWidget(button)
        edit_row.addStretch(1)
        self.elements = QListWidget()
        self.elements.setObjectName("graphic-elements")
        self.elements.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.elements.itemSelectionChanged.connect(self._list_selected)
        self.visible_button = self._button(
            "graphic-visible", "planner.editor.visible", self._toggle_visible
        )
        self.lock_button = self._button("graphic-lock", "planner.editor.lock", self._toggle_lock)
        self.duplicate_button = self._button(
            "graphic-duplicate", "planner.editor.duplicate", self._duplicate
        )
        self.delete_button = self._button("graphic-delete", "planner.editor.delete", self._delete)
        list_actions = QHBoxLayout()
        list_actions.setContentsMargins(0, 0, 0, 0)
        for button in (
            self.visible_button,
            self.lock_button,
            self.duplicate_button,
            self.delete_button,
        ):
            list_actions.addWidget(button)
        self.grid = QDoubleSpinBox()
        self.grid.setObjectName("graphic-grid")
        self.grid.setRange(1.0, 500.0)
        self.grid.setDecimals(1)
        self.grid.setValue(10.0)
        self.grid.valueChanged.connect(self._grid_changed)
        self.snap_grid = QCheckBox(translate("planner.editor.snap_grid"))
        self.snap_grid.setObjectName("graphic-snap-grid")
        self.snap_grid.setChecked(True)
        self.snap_grid.toggled.connect(self._snap_changed)
        self.snap_points = QCheckBox(translate("planner.editor.snap_points"))
        self.snap_points.setObjectName("graphic-snap-points")
        self.snap_points.setChecked(True)
        self.snap_points.toggled.connect(self._snap_changed)
        self.x_spin = self._metric("graphic-x")
        self.y_spin = self._metric("graphic-y")
        self.length_spin = self._metric("graphic-length")
        self.radius_spin = self._metric("graphic-radius")
        self.sweep_spin = self._metric("graphic-sweep")
        self.sweep_spin.setRange(-360.0, 360.0)
        self.direction_spin = self._metric("graphic-direction")
        self.direction_spin.setRange(0.0, 360.0)
        self.lanes_edit = QLineEdit()
        self.lanes_edit.setObjectName("graphic-lanes")
        for spin, name in (
            (self.x_spin, "x"),
            (self.y_spin, "y"),
            (self.length_spin, "length"),
            (self.radius_spin, "radius"),
            (self.sweep_spin, "sweep"),
            (self.direction_spin, "direction"),
        ):
            spin.editingFinished.connect(lambda metric=name: self._commit_metric(metric))
        self.lanes_edit.editingFinished.connect(lambda: self._commit_metric("lanes"))
        self.mark_color = QLineEdit()
        self.mark_color.setObjectName("graphic-mark-color")
        self.mark_color.editingFinished.connect(self._commit_mark)
        self.mark_width = QDoubleSpinBox()
        self.mark_width.setObjectName("graphic-mark-width")
        self.mark_width.setRange(0.1, 40.0)
        self.mark_width.setDecimals(2)
        self.mark_width.setSingleStep(0.5)
        self.mark_width.editingFinished.connect(self._commit_mark)
        self.mark_dashed = QCheckBox(translate("planner.editor.mark_dashed"))
        self.mark_dashed.setObjectName("graphic-mark-dashed")
        self.mark_dashed.toggled.connect(self._commit_mark)
        properties = QFormLayout()
        properties.addRow(translate("planner.editor.x"), self.x_spin)
        properties.addRow(translate("planner.editor.y"), self.y_spin)
        properties.addRow(translate("planner.editor.length"), self.length_spin)
        properties.addRow(translate("planner.editor.radius"), self.radius_spin)
        properties.addRow(translate("planner.editor.sweep"), self.sweep_spin)
        properties.addRow(translate("planner.editor.direction_deg"), self.direction_spin)
        properties.addRow(translate("planner.editor.lanes"), self.lanes_edit)
        properties.addRow(translate("planner.editor.mark_color"), self.mark_color)
        properties.addRow(translate("planner.editor.mark_width"), self.mark_width)
        properties.addRow(self.mark_dashed)
        properties.addRow(translate("planner.editor.grid"), self.grid)
        properties.addRow(self.snap_grid)
        properties.addRow(self.snap_points)
        self.side = QVBoxLayout()
        self.side.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(translate("planner.editor.elements"))
        set_role(heading, "section")
        self.side.addWidget(heading)
        self.side.addWidget(self.elements, 1)
        self.side.addLayout(list_actions)
        prop_heading = QLabel(translate("planner.editor.properties"))
        set_role(prop_heading, "section")
        self.side.addWidget(prop_heading)
        self.side.addLayout(properties)
        self.side.addWidget(self.issue)
        side_host = QWidget()
        side_host.setObjectName("graphic-side")
        side_host.setLayout(self.side)
        side_host.setMinimumWidth(240)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.canvas, 1)
        body.addWidget(side_host)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(tool_row)
        layout.addLayout(edit_row)
        layout.addLayout(body, 1)
        self.sync()

    def load(self, spec: PartSpec | None) -> None:
        self.session.load(spec)
        self.canvas._spec = spec
        self.canvas.fit()
        self.sync()

    def set_editable(self, editable: bool) -> None:
        self.editable = editable
        for button in self._tool_buttons.values():
            button.setEnabled(editable)
        for button in (
            self.finish_button,
            self.rotate_button,
            self.mirror_x_button,
            self.mirror_y_button,
            self.duplicate_button,
            self.delete_button,
        ):
            button.setEnabled(editable)

    def sync(self) -> None:
        self._fill_list()
        self._show_metrics()
        self.canvas.update()

    def show_issues(self, issues: tuple[tuple[str, str], ...]) -> None:
        if not issues:
            self.issue.setText("")
            return
        self.issue.setText(self._translator.translate(issues[0][0]))
        self.canvas.update()

    def _button(self, object_name: str, key: str, slot: Callable[[], None]) -> QPushButton:
        button = QPushButton(self._translator.translate(key))
        button.setObjectName(object_name)
        button.setToolTip(self._translator.translate(key))
        button.clicked.connect(slot)
        return button

    def _metric(self, object_name: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setObjectName(object_name)
        spin.setRange(-100_000.0, 100_000.0)
        spin.setDecimals(3)
        return spin

    def _tool_clicked(self, button: QAbstractButton) -> None:
        for tool, widget in self._tool_buttons.items():
            if widget is button:
                self.session.set_tool(tool)
                self.changed.emit()
                self.canvas.update()
                return

    def _finish(self) -> None:
        self.session.finish_path()
        self._after_edit()

    def _undo(self) -> None:
        self.session.undo()
        self._after_edit()

    def _redo(self) -> None:
        self.session.redo()
        self._after_edit()

    def _rotate(self) -> None:
        self.session.rotate_selection(90.0)
        self._after_edit()

    def _mirror_x(self) -> None:
        self.session.mirror_selection("x")
        self._after_edit()

    def _mirror_y(self) -> None:
        self.session.mirror_selection("y")
        self._after_edit()

    def _duplicate(self) -> None:
        self.session.duplicate_selection()
        self._after_edit()

    def _delete(self) -> None:
        self.session.delete_selection()
        self._after_edit()

    def _toggle_visible(self) -> None:
        for element_id in self.session.selected:
            item = self.session.element(element_id)
            if item is not None:
                self.session.set_visible(element_id, not item.visible)
        self._after_edit()

    def _toggle_lock(self) -> None:
        for element_id in self.session.selected:
            item = self.session.element(element_id)
            if item is not None:
                self.session.set_locked(element_id, not item.locked)
        self._after_edit()

    def _grid_changed(self, value: float) -> None:
        self.session.grid_mm = float(value)
        self.canvas.update()

    def _snap_changed(self, _checked: bool) -> None:
        self.session.snap_grid = self.snap_grid.isChecked()
        self.session.snap_points = self.snap_points.isChecked()

    def _commit_metric(self, name: str) -> None:
        if self._filling or not self.editable:
            return
        metrics = self.session.metrics()
        if name == "lanes":
            if self.lanes_edit.text() == str(metrics.get("lanes") or ""):
                return
            try:
                self.session.set_metric("lanes", self.lanes_edit.text())
            except ValidationError:
                self._show_metrics()
                return
        else:
            spin = {
                "x": self.x_spin,
                "y": self.y_spin,
                "length": self.length_spin,
                "radius": self.radius_spin,
                "sweep": self.sweep_spin,
                "direction": self.direction_spin,
            }[name]
            current = metrics.get(name)
            if not isinstance(current, (int, float)) or not spin.isEnabled():
                return
            if abs(float(current) - spin.value()) <= 0.001:
                return
            self.session.set_metric(name, spin.value())
        self._after_edit()

    def _after_edit(self) -> None:
        self.sync()
        self.changed.emit()

    def note_change(self) -> None:
        self._show_metrics()
        self._fill_list()
        self.canvas.update()
        self.changed.emit()

    def _fill_list(self) -> None:
        self._filling = True
        self.elements.clear()
        for item in self.session.elements:
            label = item.name or item.id
            if not item.visible:
                label = f"{label} · —"
            if item.locked:
                label = f"{label} · x"
            row = QListWidgetItem(label)
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            self.elements.addItem(row)
            row.setSelected(item.id in self.session.selected)
        self._filling = False

    def _list_selected(self) -> None:
        if self._filling:
            return
        chosen: list[str] = []
        for row in self.elements.selectedItems():
            value = row.data(Qt.ItemDataRole.UserRole)
            if isinstance(value, str):
                chosen.append(value)
        self.session.select_ids(frozenset(chosen))
        self._show_metrics()
        self.canvas.update()

    def _show_metrics(self) -> None:
        self._filling = True
        metrics = self.session.metrics()
        self._set_spin(self.x_spin, metrics.get("x"))
        self._set_spin(self.y_spin, metrics.get("y"))
        self._set_spin(self.length_spin, metrics.get("length"))
        self._set_spin(self.radius_spin, metrics.get("radius"))
        self._set_spin(self.sweep_spin, metrics.get("sweep"))
        self._set_spin(self.direction_spin, metrics.get("direction"))
        lanes = metrics.get("lanes")
        self.lanes_edit.setText("" if not isinstance(lanes, str) else lanes)
        self.lanes_edit.setEnabled(
            (self.editable and isinstance(lanes, str) and lanes != "") or self._joint_selected()
        )
        mark = self._center_selected()
        self.mark_color.setEnabled(self.editable and mark is not None)
        self.mark_width.setEnabled(self.editable and mark is not None)
        self.mark_dashed.setEnabled(self.editable and mark is not None)
        if mark is None:
            self.mark_color.setText("")
        else:
            self.mark_color.setText(mark.stroke or "")
            if mark.width_mm is not None:
                self.mark_width.setValue(mark.width_mm)
            self.mark_dashed.setChecked(mark.dashed)
        self._filling = False

    def _center_selected(self) -> EditorElement | None:
        element = self.session._single()
        if element is None or element.kind != KIND_CENTER:
            return None
        return element

    def _commit_mark(self, _checked: bool = False) -> None:
        if self._filling or not self.editable:
            return
        mark = self._center_selected()
        if mark is None:
            return
        color = self.mark_color.text().strip()
        if color and QColor(color).isValid() is False:
            self._show_metrics()
            return
        width = self.mark_width.value()
        dashed = self.mark_dashed.isChecked()
        if (
            color == (mark.stroke or "")
            and abs((mark.width_mm or 0.0) - width) <= 0.001
            and dashed == mark.dashed
        ):
            return
        self.session.set_mark_style(
            stroke=color or None,
            width_mm=width,
            dashed=dashed,
        )
        self._after_edit()

    def _joint_selected(self) -> bool:
        element = self.session._single()
        return element is not None and element.kind == KIND_JOINT

    def _set_spin(self, spin: QDoubleSpinBox, value: object) -> None:
        enabled = self.editable and isinstance(value, (int, float))
        spin.setEnabled(enabled)
        if isinstance(value, (int, float)):
            spin.setValue(float(value))


class GraphicCanvas(QWidget):
    """Millimetre canvas. Zoom stays under the pointer."""

    def __init__(self, editor: GraphicEditor) -> None:
        super().__init__(editor)
        self.setObjectName("graphic-canvas")
        self.setMinimumSize(480, 420)
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
        self._spec: PartSpec | None = None

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
        span_x = max(abs(x1 - x0), 1.0)
        span_y = max(abs(y1 - y0), 1.0)
        margin = 36.0
        scale = min((self.width() - 2 * margin) / span_x, (self.height() - 2 * margin) / span_y)
        center_x = (min(x0, x1) + max(x0, x1)) / 2.0
        center_y = (min(y0, y1) + max(y0, y1)) / 2.0
        self.set_view(
            scale, self.width() / 2.0 - center_x * scale, self.height() / 2.0 - center_y * scale
        )

    def fit(self) -> None:
        points = _view_points(self._editor.session)
        spec = self._spec
        if spec is not None:
            points.extend(spec.outline)
            points.extend((connector.x_mm, connector.y_mm) for connector in spec.connectors)
        if not points:
            self.fit_rect(-40.0, -80.0, 400.0, 220.0)
            return
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        pad = 30.0
        self.fit_rect(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

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
        spec = self._editor.spec_provider()
        self._spec = spec
        if spec is not None:
            paint_part(
                painter,
                spec,
                color_coding=False,
                selected=False,
                start_straight=False,
            )
        if spec is None or self._editor.session.edited:
            self._stroke_elements(painter)
        self._preview(painter)
        self._marks(painter)
        self._handles(painter)
        self._snap(painter)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_from = event.position()
            self._pan_origin = (self._pan_x, self._pan_y)
            self._panning = True
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or not self._editor.editable:
            return
        self.setFocus()
        x_mm, y_mm = self._world(event.position())
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._editor.session.click(x_mm, y_mm, shift=shift, tolerance_mm=self._tolerance())
        self._dragging = self._editor.session._drag_from is not None
        if self._dragging and not _offscreen():
            self.grabMouse()
        self._editor.note_change()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._panning and event.buttons() & Qt.MouseButton.MiddleButton:
            delta = event.position() - self._pan_from
            self._pan_x = self._pan_origin[0] + delta.x()
            self._pan_y = self._pan_origin[1] + delta.y()
            self._hold_view = True
            self.update()
            return
        x_mm, y_mm = self._world(event.position())
        self._editor.session.move(x_mm, y_mm, tolerance_mm=self._tolerance())
        if self._dragging:
            self._editor.note_change()
            return
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = False
        if self.mouseGrabber() is self:
            self.releaseMouse()
        self._editor.session.release()
        self._editor.note_change()

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
        if not self._editor.editable:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Escape:
            self._dragging = False
            if self.mouseGrabber() is self:
                self.releaseMouse()
            self._editor.session.cancel_action()
            self._editor.note_change()
            return
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            self._editor._delete()
            return
        control = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if control and event.key() == Qt.Key.Key_Z:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._editor._redo()
            else:
                self._editor._undo()
            return
        if control and event.key() == Qt.Key.Key_Y:
            self._editor._redo()
            return
        if control and event.key() == Qt.Key.Key_D:
            self._editor._duplicate()
            return
        super().keyPressEvent(event)

    def _tolerance(self) -> float:
        return _HIT_PX / max(self._scale, 0.05)

    def _world(self, pos: QPointF) -> tuple[float, float]:
        return ((pos.x() - self._pan_x) / self._scale, (pos.y() - self._pan_y) / self._scale)

    def _grid(self, painter: QPainter) -> None:
        step = self._editor.session.grid_mm
        if step <= 0:
            return
        x0, y0 = self._world(QPointF(0, 0))
        x1, y1 = self._world(QPointF(self.width(), self.height()))
        left, right = min(x0, x1), max(x0, x1)
        top, bottom = min(y0, y1), max(y0, y1)
        pen = QPen(QColor(COLORS.border))
        pen.setCosmetic(True)
        painter.setPen(pen)
        x_mm = math.floor(left / step) * step
        while x_mm <= right:
            painter.drawLine(QPointF(x_mm, top), QPointF(x_mm, bottom))
            x_mm += step
        y_mm = math.floor(top / step) * step
        while y_mm <= bottom:
            painter.drawLine(QPointF(left, y_mm), QPointF(right, y_mm))
            y_mm += step

    def _stroke_elements(self, painter: QPainter) -> None:
        for item in self._editor.session.elements:
            if not item.visible or item.kind == KIND_JOINT:
                continue
            color = COLORS.warning if item.kind == KIND_START else COLORS.text
            if item.kind == KIND_LANE:
                color = COLORS.info
            self._stroke(painter, item.spans, color, 1.6)

    def _preview(self, painter: QPainter) -> None:
        span = self._editor.session.preview_span()
        if span is None:
            return
        pen = QPen(QColor(COLORS.accent))
        pen.setCosmetic(True)
        pen.setWidthF(1.4)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _draw_span(painter, span)

    def _marks(self, painter: QPainter) -> None:
        session = self._editor.session
        for item in session.elements:
            if item.id not in session.marks or not item.spans:
                continue
            self._stroke(painter, item.spans, COLORS.error, 2.4)
        for item in session.elements:
            if item.kind != KIND_JOINT or not item.visible:
                continue
            color = COLORS.error if item.id in session.marks else COLORS.accent
            if item.id in session.selected:
                color = COLORS.accent_hover
            pen = QPen(QColor(color))
            pen.setCosmetic(True)
            pen.setWidthF(2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(item.x, item.y), 7.0, 7.0)
            radians = math.radians(item.direction_deg)
            painter.drawLine(
                QPointF(item.x, item.y),
                QPointF(item.x + math.cos(radians) * 18.0, item.y + math.sin(radians) * 18.0),
            )

    def _handles(self, painter: QPainter) -> None:
        session = self._editor.session
        pen = QPen(QColor(COLORS.accent))
        pen.setCosmetic(True)
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.setBrush(QColor(COLORS.elevated))
        size = 4.0 / max(self._scale, 0.05)
        for item in session.elements:
            if item.id not in session.selected or not item.visible:
                continue
            for span in item.spans:
                for name, anchor in _handle_points(span):
                    active = session.active_handle == name and session.active_span is not None
                    painter.setBrush(QColor(COLORS.accent if active else COLORS.elevated))
                    painter.drawRect(QRectF(anchor[0] - size, anchor[1] - size, size * 2, size * 2))

    def _snap(self, painter: QPainter) -> None:
        target = self._editor.session.snap_target
        if target is None:
            return
        pen = QPen(QColor(COLORS.warning))
        pen.setCosmetic(True)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(target[0], target[1]), 5.0, 5.0)

    def _stroke(
        self, painter: QPainter, spans: tuple[SlotSpan, ...], color: str, width: float
    ) -> None:
        pen = QPen(QColor(color))
        pen.setCosmetic(True)
        pen.setWidthF(width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for span in spans:
            _draw_span(painter, span)


def _view_points(session: GraphicSession) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in session.elements:
        if item.kind == KIND_JOINT:
            points.append((item.x, item.y))
        for span in item.spans:
            points.append(as_segment(span).start())
            points.append(as_segment(span).end())
    return points


def _handle_points(span: SlotSpan) -> tuple[tuple[str, tuple[float, float]], ...]:
    segment = as_segment(span)
    if isinstance(segment, CubicBezier):
        names = ("p0", "p1", "p2", "p3")
        return tuple(zip(names, segment.control_points(), strict=True))
    found = [("start", segment.start()), ("end", segment.end())]
    if span.kind == SPAN_ARC:
        found.append(("mid", segment.point_at(0.5)))
    return tuple(found)


def _draw_span(painter: QPainter, span: SlotSpan) -> None:
    segment = as_segment(span)
    if span.kind == SPAN_ARC or isinstance(segment, CubicBezier):
        steps = 24
        previous = segment.point_at(0.0)
        for index in range(1, steps + 1):
            point = segment.point_at(index / steps)
            painter.drawLine(QPointF(*previous), QPointF(*point))
            previous = point
        if isinstance(segment, CubicBezier):
            pen = QPen(QColor(COLORS.text_muted))
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(pen)
            controls = segment.control_points()
            painter.drawLine(QPointF(*controls[0]), QPointF(*controls[1]))
            painter.drawLine(QPointF(*controls[2]), QPointF(*controls[3]))
        return
    painter.drawLine(QPointF(*segment.start()), QPointF(*segment.end()))
