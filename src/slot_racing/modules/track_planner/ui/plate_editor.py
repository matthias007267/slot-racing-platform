"""Point-and-click floor plate and driving lanes. Later elements stay disabled."""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from itertools import pairwise

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
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
from slot_racing.modules.track_planner.editing import span_end, span_start
from slot_racing.modules.track_planner.geometry import ArcSegment
from slot_racing.modules.track_planner.parts import SPAN_ARC, PartSpec, as_segment
from slot_racing.modules.track_planner.plate_session import (
    PlateSession,
    draft_lanes,
    plate_corners,
    plate_part,
    stored_sketch,
)
from slot_racing.modules.track_planner.ui.track_paint import paint_part
from slot_racing.uikit.theme import COLORS, set_role, set_tone

_SELECT = "select"
_FLOOR = "floor"
_LANE = "lane"
_HIT_PX = 10.0


def _offscreen() -> bool:
    """True when Qt has no real pointer. ``grabMouse`` then warns and can stall Windows."""
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return True
    app = QGuiApplication.instance()
    return isinstance(app, QGuiApplication) and app.platformName() == "offscreen"


_LATER = (
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
        self.lane_button = self._mode_button("plate-lane", "planner.plate.lane")
        self.select_button.setChecked(True)
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        modes.addButton(self.select_button)
        modes.addButton(self.floor_button)
        modes.addButton(self.lane_button)
        self.select_button.toggled.connect(self._mode_changed)
        self.floor_button.toggled.connect(self._mode_changed)
        self.lane_button.toggled.connect(self._mode_changed)
        self.close_button = self._action("plate-close", "planner.plate.close", self._close)
        self.replace_button = self._action("plate-replace", "planner.plate.replace", self._replace)
        self.add_lane_button = self._action("lane-add", "planner.lane.add", self._add_lane)
        self.finish_lane_button = self._action(
            "lane-finish", "planner.lane.finish", self._finish_lane
        )
        self.remove_lane_button = self._action(
            "lane-remove", "planner.lane.remove", self._remove_lane
        )
        self.insert_button = self._action("lane-insert", "planner.lane.insert", self._insert_point)
        self.remove_point_button = self._action(
            "lane-remove-point", "planner.lane.remove_point", self._remove_point
        )
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
        self.lane_label = QLabel("—")
        self.lane_label.setObjectName("lane-number")
        self.length_label = QLabel("—")
        self.length_label.setObjectName("lane-length")
        self.segment_label = QLabel("—")
        self.segment_label.setObjectName("lane-segment-length")
        self.angle_label = QLabel("—")
        self.angle_label.setObjectName("lane-angle")
        self.ends_label = QLabel("—")
        self.ends_label.setObjectName("lane-ends")
        self.ends_label.setWordWrap(True)
        self.other_label = QLabel("—")
        self.other_label.setObjectName("lane-other-distance")
        self.other_label.setToolTip(translate("planner.lane.other_hint"))
        self.edge_label = QLabel("—")
        self.edge_label.setObjectName("lane-edge-distance")
        self.edge_label.setToolTip(translate("planner.lane.edge_hint"))
        self.drawing_label = QLabel("")
        self.drawing_label.setObjectName("lane-drawing")
        self.drawing_label.setWordWrap(True)
        self.radius_spin = self._coord("lane-radius")
        self.radius_spin.setRange(0.001, 100_000)
        self.radius_spin.valueChanged.connect(self._preview_radius)
        self.radius_spin.editingFinished.connect(self._commit_radius)
        self.line_button = self._mode_button("lane-line", "planner.lane.line")
        self.arc_button = self._mode_button("lane-arc", "planner.lane.arc")
        kinds = QButtonGroup(self)
        kinds.setExclusive(True)
        kinds.addButton(self.line_button)
        kinds.addButton(self.arc_button)
        self.line_button.toggled.connect(lambda checked: self._kind_toggled("line", checked))
        self.arc_button.toggled.connect(lambda checked: self._kind_toggled("arc", checked))
        self.right_button = self._mode_button("lane-right", "planner.lane.right")
        self.left_button = self._mode_button("lane-left", "planner.lane.left")
        turns = QButtonGroup(self)
        turns.setExclusive(True)
        turns.addButton(self.right_button)
        turns.addButton(self.left_button)
        self.right_button.toggled.connect(lambda checked: self._turn_toggled(True, checked))
        self.left_button.toggled.connect(lambda checked: self._turn_toggled(False, checked))
        self.short_button = self._mode_button("lane-short", "planner.lane.short")
        self.long_button = self._mode_button("lane-long", "planner.lane.long")
        sizes = QButtonGroup(self)
        sizes.setExclusive(True)
        sizes.addButton(self.short_button)
        sizes.addButton(self.long_button)
        self.short_button.toggled.connect(lambda checked: self._size_toggled(False, checked))
        self.long_button.toggled.connect(lambda checked: self._size_toggled(True, checked))
        self.kind_row = QWidget()
        kind_layout = QHBoxLayout(self.kind_row)
        kind_layout.setContentsMargins(0, 0, 0, 0)
        kind_layout.addWidget(self.line_button)
        kind_layout.addWidget(self.arc_button)
        self.turn_row = QWidget()
        turn_layout = QHBoxLayout(self.turn_row)
        turn_layout.setContentsMargins(0, 0, 0, 0)
        turn_layout.addWidget(self.right_button)
        turn_layout.addWidget(self.left_button)
        self.size_row = QWidget()
        size_layout = QHBoxLayout(self.size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.addWidget(self.short_button)
        size_layout.addWidget(self.long_button)
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
        side.addWidget(self.lane_button)
        for name, key in _LATER:
            button = QPushButton(translate(key))
            button.setObjectName(name)
            button.setEnabled(False)
            button.setToolTip(translate("planner.plate.later"))
            side.addWidget(button)
        side.addStretch(1)
        side.addWidget(self.close_button)
        side.addWidget(self.replace_button)
        side.addWidget(self.add_lane_button)
        side.addWidget(self.finish_lane_button)
        side.addWidget(self.remove_lane_button)
        side.addWidget(self.insert_button)
        side.addWidget(self.remove_point_button)
        side.addWidget(self.undo_button)
        side.addWidget(self.redo_button)
        side.addWidget(self.fit_button)
        form = QFormLayout()
        form.addRow(translate("planner.plate.count"), self.count_label)
        form.addRow(translate("planner.plate.state"), self.state_label)
        form.addRow(translate("planner.plate.perimeter"), self.perimeter_label)
        form.addRow(translate("planner.plate.x"), self.x_spin)
        form.addRow(translate("planner.plate.y"), self.y_spin)
        form.addRow(translate("planner.lane.which"), self.lane_label)
        form.addRow(translate("planner.lane.length"), self.length_label)
        form.addRow(translate("planner.lane.kind"), self.kind_row)
        form.addRow(translate("planner.lane.radius"), self.radius_spin)
        form.addRow(translate("planner.lane.turn"), self.turn_row)
        form.addRow(translate("planner.lane.size"), self.size_row)
        form.addRow(translate("planner.lane.angle"), self.angle_label)
        form.addRow(translate("planner.lane.segment"), self.segment_label)
        form.addRow(translate("planner.lane.ends"), self.ends_label)
        form.addRow(translate("planner.lane.other"), self.other_label)
        form.addRow(translate("planner.lane.edge"), self.edge_label)
        properties = QWidget()
        properties.setObjectName("plate-properties")
        column = QVBoxLayout(properties)
        column.addLayout(form)
        column.addWidget(self.drawing_label)
        column.addWidget(self.issue)
        column.addWidget(self.cursor_label)
        column.addStretch(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        host = QWidget()
        host.setLayout(side)
        host.setFixedWidth(230)
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
        lanes = () if spec is None or corners is None else draft_lanes(spec) or ()
        if spec is None:
            self.session = PlateSession()
            self._editable = True
            self._reference = None
        elif corners is not None:
            self.session = PlateSession(corners, closed=True, lanes=lanes)
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
        self._commit_radius()

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
        return stored_sketch(
            self.session.points,
            self.session.sketch_lanes(),
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
        if self._filling:
            return
        if self.lane_button.isChecked():
            self._mode = _LANE
        elif self.floor_button.isChecked():
            self._mode = _FLOOR
        else:
            self._mode = _SELECT
        self._show_drawing()
        self.canvas.update()

    def _close(self) -> None:
        self.session.close()
        self.note_change()

    def _replace(self) -> None:
        self.session.replace()
        self.floor_button.setChecked(True)
        self.note_change()

    def _add_lane(self) -> None:
        self.session.add_lane()
        self.lane_button.setChecked(True)
        self.note_change()

    def _finish_lane(self) -> None:
        self.session.finish_lane()
        self.note_change()

    def _remove_lane(self) -> None:
        self.session.remove_lane()
        self.note_change()

    def _insert_point(self) -> None:
        self.session.insert_point()
        self.note_change()

    def _remove_point(self) -> None:
        self.session.remove_lane_point()
        self.note_change()

    def _kind_toggled(self, kind: str, checked: bool) -> None:
        if self._filling or not checked:
            return
        if kind == "arc":
            self.session.convert_to_arc()
        else:
            self.session.convert_to_line()
        self.note_change()

    def _turn_toggled(self, right: bool, checked: bool) -> None:
        if self._filling or not checked:
            return
        span = self.session.selected_span()
        if span is None or span.kind != SPAN_ARC:
            return
        long = abs(span.sweep_deg) > 180.0 + 1e-4
        self.session.set_arc(span.radius_mm, right=right, long=long)
        self.note_change()

    def _size_toggled(self, long: bool, checked: bool) -> None:
        if self._filling or not checked:
            return
        span = self.session.selected_span()
        if span is None or span.kind != SPAN_ARC:
            return
        self.session.set_arc(span.radius_mm, right=span.sweep_deg >= 0.0, long=long)
        self.note_change()

    def _preview_radius(self) -> None:
        if self._filling:
            return
        self.session.preview_span_radius(self.radius_spin.value())
        self.canvas.update()
        self._show_status()

    def _commit_radius(self) -> None:
        if self._filling or not self._editable:
            return
        self.session.commit_span_radius(self.radius_spin.value())
        self.note_change()

    def _undo(self) -> None:
        self.session.undo()
        self.note_change()

    def _redo(self) -> None:
        self.session.redo()
        self.note_change()

    def _point_selected(self) -> bool:
        return self.session.selected is not None or self.session.lane_point is not None

    def _preview_spin(self) -> None:
        if self._filling or not self._point_selected():
            return
        self.session.preview_selected(self.x_spin.value(), self.y_spin.value())
        self.canvas.update()
        self._show_status()

    def _commit_spin(self) -> None:
        if self._filling or not self._editable or not self._point_selected():
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
        self.lane_button.setEnabled(drawing)
        self.close_button.setEnabled(drawing and session.can_close())
        self.replace_button.setEnabled(drawing and session.can_replace())
        self.add_lane_button.setEnabled(drawing and session.can_add_lane())
        self.finish_lane_button.setEnabled(drawing and session.can_finish_lane())
        self.remove_lane_button.setEnabled(drawing and session.can_remove_lane())
        self.insert_button.setEnabled(drawing and session.can_insert_point())
        self.remove_point_button.setEnabled(drawing and session.can_remove_lane_point())
        self.undo_button.setEnabled(drawing and session.can_undo())
        self.redo_button.setEnabled(drawing and session.can_redo())
        self.count_label.setText(str(len(session.display_points())))
        state_key = "planner.plate.closed_state" if session.closed else "planner.plate.open_state"
        self.state_label.setText(translate(state_key))
        perimeter = session.perimeter_mm()
        self.perimeter_label.setText("—" if perimeter is None else f"{perimeter:.3f}")
        self._fill_point(drawing)
        self._fill_lane(translate)
        self._filling = False
        self._show_drawing()
        self._show_status()
        self.canvas.update()
        token = session.geometry_token()
        if token != self._last_token:
            self._last_token = token
            self.changed.emit()

    def _fill_point(self, drawing: bool) -> None:
        session = self.session
        point: tuple[float, float] | None = None
        if session.lane_point is not None:
            lane, index = session.lane_point
            vertices = session.lane_vertices(lane)
            if 0 <= index < len(vertices):
                point = vertices[index]
        elif session.selected is not None:
            points = session.display_points()
            if 0 <= session.selected < len(points):
                point = points[session.selected]
        self.x_spin.setEnabled(drawing and point is not None)
        self.y_spin.setEnabled(drawing and point is not None)
        if point is not None:
            self.x_spin.setValue(point[0])
            self.y_spin.setValue(point[1])

    def _fill_lane(self, translate: object) -> None:
        assert callable(translate)
        session = self.session
        focus = None
        if session.lane_point is not None:
            focus = session.lane_point[0]
        elif session.lane_span is not None:
            focus = session.lane_span[0]
        elif session.active_lane is not None:
            focus = session.active_lane
        self.lane_label.setText("—" if focus is None else str(focus + 1))
        length = None if focus is None else session.lane_length_mm(focus)
        self.length_label.setText("—" if length is None else f"{length:.3f}")
        other = session.point_to_other_lanes_mm()
        edge = session.point_to_plate_edge_mm()
        self.other_label.setText("—" if other is None else f"{other:.3f}")
        self.edge_label.setText("—" if edge is None else f"{edge:.3f}")
        span = session.selected_span()
        arc = span is not None and span.kind == SPAN_ARC
        self.line_button.setEnabled(self._editable and span is not None)
        self.arc_button.setEnabled(self._editable and span is not None)
        self.radius_spin.setEnabled(self._editable and arc)
        self.right_button.setEnabled(self._editable and arc)
        self.left_button.setEnabled(self._editable and arc)
        self.short_button.setEnabled(self._editable and arc)
        self.long_button.setEnabled(self._editable and arc)
        if span is None:
            self.segment_label.setText("—")
            self.angle_label.setText("—")
            self.ends_label.setText("—")
            return
        self.line_button.setChecked(span.kind != SPAN_ARC)
        self.arc_button.setChecked(span.kind == SPAN_ARC)
        segment = session.segment_length_mm()
        self.segment_label.setText("—" if segment is None else f"{segment:.3f}")
        start, end = span_start(span), span_end(span)
        self.ends_label.setText(f"{start[0]:.3f}, {start[1]:.3f}  →  {end[0]:.3f}, {end[1]:.3f}")
        if not arc:
            self.angle_label.setText("—")
            return
        self.radius_spin.setValue(span.radius_mm)
        self.right_button.setChecked(span.sweep_deg >= 0.0)
        self.left_button.setChecked(span.sweep_deg < 0.0)
        long = abs(span.sweep_deg) > 180.0 + 1e-4
        self.long_button.setChecked(long)
        self.short_button.setChecked(not long)
        self.angle_label.setText(f"{abs(span.sweep_deg):.3f}")

    def _show_drawing(self) -> None:
        session = self.session
        translate = self._translator.translate
        open_lane = session.unfinished_lane()
        if self._mode == _LANE and open_lane is not None:
            text = translate("planner.lane.drawing").format(number=open_lane + 1)
        elif self._mode == _LANE and session.active_lane is not None:
            text = translate("planner.lane.editing").format(number=session.active_lane + 1)
        elif self._mode == _FLOOR and not session.closed:
            text = translate("planner.lane.plate_drawing")
        elif self._mode == _FLOOR:
            text = translate("planner.lane.plate_editing")
        else:
            text = ""
        self.drawing_label.setText(text)

    def _show_status(self) -> None:
        if not self._editable and self._reference is not None:
            self.issue.setText(self._translator.translate("planner.plate.locked"))
            set_tone(self.issue, "warning")
            return
        key = self.session.status_key()
        if key is None:
            key = self.session.warning_key()
            self.issue.setText("" if key is None else self._translator.translate(key))
            set_tone(self.issue, "warning" if key else "")
            return
        self.issue.setText(self._translator.translate(key))
        set_tone(self.issue, "danger")


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
        for index in range(len(session.lanes)):
            points.extend(session.lane_vertices(index))
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
        self._end_grab()
        self._editor.session.commit_move()

    def _begin_drag(self) -> None:
        """Track the pointer until release. Offscreen cannot grab, and trying locks Windows CI."""
        self._dragging = True
        if _offscreen():
            return
        self.grabMouse()

    def _end_grab(self) -> None:
        if self.mouseGrabber() is self:
            self.releaseMouse()

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
        if self._editor.mode == _FLOOR and not session.closed:
            if session.near_start(x_mm, y_mm, tolerance):
                session.close()
            else:
                session.add_point(x_mm, y_mm)
            self._editor.note_change()
            return
        if self._editor.mode == _LANE:
            session.add_lane_point(x_mm, y_mm)
            self._editor.note_change()
            return
        if self._editor.mode == _FLOOR and session.closed:
            session.reject_second()
        target = session.pick(x_mm, y_mm, tolerance)
        if target is None:
            session.clear_pick()
        elif target[0] == "plate":
            session.select(target[1])
            if self._editor.mode == _SELECT:
                session.begin_move(target[1])
                self._begin_drag()
        elif target[0] == "point":
            if self._editor.mode == _SELECT:
                session.begin_lane_move(target[1], target[2])
                self._begin_drag()
            else:
                session.select_lane_point(target[1], target[2])
        else:
            session.select_lane_span(target[1], target[2])
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
        drawing_plate = self._editor.editable and self._editor.mode == _FLOOR
        drawing_lane = self._editor.editable and self._editor.mode == _LANE
        open_lane = session.unfinished_lane()
        lane_vertices = () if open_lane is None else session.lane_vertices(open_lane)
        plate_hover = drawing_plate and not session.closed and session.points
        if plate_hover or (drawing_lane and lane_vertices):
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
                self._end_grab()
                self._editor.session.cancel_move()
            elif self._editor.mode == _LANE:
                self._editor.session.cancel_lane()
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
        self._stroke_lanes(painter, session, sketch=spec is None)
        hover = session.hover
        if hover is not None and self._editor.mode == _FLOOR and points and not session.closed:
            pen = QPen(QColor(COLORS.info))
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(*points[-1]), QPointF(*hover))
        open_lane = session.unfinished_lane()
        if hover is not None and open_lane is not None and self._editor.mode == _LANE:
            vertices = session.lane_vertices(open_lane)
            if vertices:
                pen = QPen(QColor(COLORS.info))
                pen.setCosmetic(True)
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(QPointF(*vertices[-1]), QPointF(*hover))

    def _paint_spec(self) -> PartSpec | None:
        if not self._editor.editable and self._editor.reference is not None:
            return self._editor.reference
        session = self._editor.session
        if not session.closed:
            return None
        try:
            return plate_part(session.display_points(), session.sketch_lanes())
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
        for lane in range(len(session.lanes)):
            active = lane == session.active_lane
            for index, point in enumerate(session.lane_vertices(lane)):
                chosen = session.lane_point == (lane, index)
                color = COLORS.accent if chosen else COLORS.info if active else COLORS.elevated
                painter.setBrush(QColor(color))
                pen = QPen(QColor(COLORS.text))
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawEllipse(QPointF(*point), radius * (1.3 if chosen else 0.8), radius)

    def _stroke_lanes(self, painter: QPainter, session: PlateSession, *, sketch: bool) -> None:
        """Lane ink for an open sketch. A compiled body keeps only the selected span."""
        for lane in range(len(session.lanes)):
            active = lane == session.active_lane
            chosen = session.lane_span
            for index, span in enumerate(session.lane_spans(lane)):
                selected = chosen == (lane, index)
                if not sketch and not selected:
                    continue
                if sketch:
                    color = COLORS.accent if selected or active else COLORS.info
                    width = 2.4 if selected or active else 1.2
                else:
                    color = COLORS.accent
                    width = 2.4
                pen = QPen(QColor(color))
                pen.setCosmetic(True)
                pen.setWidthF(width)
                painter.setPen(pen)
                self._stroke_span(painter, span)

    def _stroke_span(self, painter: QPainter, span: object) -> None:
        segment = as_segment(span)  # type: ignore[arg-type]
        if isinstance(segment, ArcSegment):
            steps = max(8, int(abs(segment.sweep_deg) / 6.0))
            samples = [segment.point_at(index / steps) for index in range(steps + 1)]
            for start, end in pairwise(samples):
                painter.drawLine(QPointF(*start), QPointF(*end))
            return
        painter.drawLine(QPointF(*segment.start()), QPointF(*segment.end()))
