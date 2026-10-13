"""The drawing tab of the part editor: toolbar, canvas, properties and problem list.

It edits one :class:`EditSession`. The parametric tab of the same dialog reads that session, so
both modes share one part and switching loses nothing. Saving is the dialog's job.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.domain.lanes import MAX_LANE_COUNT
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner import editing as ed
from slot_racing.modules.track_planner.attachment import attachment_fits
from slot_racing.modules.track_planner.edit_session import (
    DEFAULT_ANGLE_STEP_DEG,
    DEFAULT_GRID_MM,
    KIND_CONNECTOR,
    KIND_PATH,
    KIND_POINT,
    EditSession,
    Snapping,
    Target,
    lane_rows,
    route_is_simple,
)
from slot_racing.modules.track_planner.edit_tools import (
    TOOL_CONNECTOR,
    TOOL_SELECT,
    TOOLS,
    EditController,
)
from slot_racing.modules.track_planner.parts import (
    BORDER_SLOTS,
    SPAN_ARC,
    SPAN_BEZIER,
    SPAN_LINE,
    TRACK,
    PartRecord,
    PartSpec,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.part_canvas import PartCanvas

_LAYER_KEYS = {
    ed.LAYER_GROOVE: "planner.gfx.layer.groove",
    ed.LAYER_CENTER: "planner.gfx.layer.center",
    ed.LAYER_CONTOUR: "planner.gfx.layer.contour",
    ed.LAYER_EDGE: "planner.gfx.layer.edge",
    ed.LAYER_ATTACH: "planner.gfx.layer.attach",
}
_TOOL_KEYS = {tool: f"planner.gfx.tool.{tool}" for tool in TOOLS}
_REASON_KEYS = {
    ed.SUPPORT_CORRUPT: "planner.gfx.reason.corrupt",
    ed.SUPPORT_CATEGORY: "planner.gfx.reason.category",
    ed.SUPPORT_BRANCH: "planner.gfx.reason.branch",
    ed.SUPPORT_STANDARD_BORDER: "planner.gfx.reason.standard_border",
    ed.SUPPORT_END_PIECE: "planner.gfx.reason.end_piece",
    "params": "planner.gfx.reason.params",
}
_FIELD_MM = 100_000.0


def ask(parent: QWidget | None, title: str, text: str) -> bool:
    """A yes/no question. Tests replace this function instead of driving a modal loop."""
    answer = QMessageBox.question(parent, title, text)
    return answer == QMessageBox.StandardButton.Yes


class GraphicEditor(QWidget):
    """Draws on one part. ``changed`` fires after every change of the part."""

    changed = Signal()

    def __init__(
        self,
        translator: Translator,
        planner: TrackPlannerService,
        spec: PartSpec,
        *,
        reason: str = "",
        exclude_id: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("part-graphic")
        self.translator = translator
        self._planner = planner
        self._library: tuple[PartRecord, ...] = ()
        self._exclude_id = exclude_id
        self._reason = reason
        self._syncing = False
        self.session = EditSession(spec)
        self.controller = EditController(self.session)
        self._hook(self.session, self.controller)
        self.canvas = PartCanvas(self.controller)
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_buttons: dict[str, QToolButton] = {}
        self._build()
        self.load(spec, reason=reason, exclude_id=exclude_id)

    # ------------------------------------------------------------------ public

    @property
    def read_only(self) -> bool:
        return bool(self._reason)

    def load(
        self,
        spec: PartSpec,
        *,
        reason: str = "",
        exclude_id: int | None = None,
    ) -> None:
        """Start over from ``spec``. The history belongs to the part that was loaded."""
        self._reason = reason
        self._exclude_id = exclude_id
        self.session = EditSession(spec)
        self.controller = EditController(self.session)
        self._hook(self.session, self.controller)
        self.canvas.set_controller(self.controller)
        self.canvas.read_only = bool(reason)
        self._library = self._read_library()
        self._apply_snapping()
        self._set_layer_choices()
        self.controller.set_tool(TOOL_SELECT)
        self._tool_buttons[TOOL_SELECT].setChecked(True)
        self._refresh_all()

    def result(self) -> PartSpec:
        return self.session.result()

    def say(self, key: str) -> None:
        """Show an error key in the status line. Used by the dialog as well."""
        self._status_message(key)

    # ------------------------------------------------------------------ build

    def _hook(self, session: EditSession, controller: EditController) -> None:
        session.listeners.append(self._session_changed)
        controller.on_message = self._status_message
        controller.on_selection = self._selection_changed

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._build_toolbar())
        layout.addLayout(self._build_options())
        row = QHBoxLayout()
        row.addWidget(self.canvas, 3)
        row.addWidget(self._build_panel(), 2)
        layout.addLayout(row, 1)
        self.status = QLabel("")
        self.status.setObjectName("gfx-status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for sequence, handler in (
            (QKeySequence(QKeySequence.StandardKey.Undo), self._undo),
            (QKeySequence(QKeySequence.StandardKey.Redo), self._redo),
        ):
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)

    def _build_toolbar(self) -> QHBoxLayout:
        t = self.translator.translate
        bar = QHBoxLayout()
        for tool in TOOLS:
            button = QToolButton()
            button.setObjectName(f"gfx-tool-{tool}")
            button.setText(t(_TOOL_KEYS[tool]))
            button.setToolTip(t(f"{_TOOL_KEYS[tool]}.tip"))
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self._tool_group.addButton(button)
            self._tool_buttons[tool] = button
            button.clicked.connect(lambda _checked=False, name=tool: self._choose_tool(name))
            bar.addWidget(button)
        bar.addSpacing(12)
        self.undo_button = self._plain_button("planner.gfx.undo", "gfx-undo", self._undo)
        self.redo_button = self._plain_button("planner.gfx.redo", "gfx-redo", self._redo)
        self.fit_button = self._plain_button("planner.gfx.fit", "gfx-fit", self.canvas.fit_view)
        for action in (self.undo_button, self.redo_button, self.fit_button):
            bar.addWidget(action)
        bar.addStretch(1)
        return bar

    def _build_options(self) -> QHBoxLayout:
        t = self.translator.translate
        row = QHBoxLayout()
        self.layer_box = QComboBox()
        self.layer_box.setObjectName("gfx-layer")
        self.layer_box.currentIndexChanged.connect(self._layer_changed)
        self.edge_slot = QComboBox()
        self.edge_slot.setObjectName("gfx-edge-slot")
        for slot in ("left", "right", "inner", "outer"):
            self.edge_slot.addItem(t(f"planner.editor.side.{slot}"), slot)
        self.edge_slot.currentIndexChanged.connect(self._edge_slot_changed)
        self.grid_snap = QCheckBox(t("planner.gfx.snap.grid"))
        self.grid_snap.setObjectName("gfx-grid-snap")
        self.grid_size = QDoubleSpinBox()
        self.grid_size.setObjectName("gfx-grid-size")
        self.grid_size.setRange(0.5, 500.0)
        self.grid_size.setDecimals(1)
        self.grid_size.setValue(DEFAULT_GRID_MM)
        self.grid_size.setSuffix(" mm")
        self.point_snap = QCheckBox(t("planner.gfx.snap.points"))
        self.point_snap.setObjectName("gfx-point-snap")
        self.point_snap.setChecked(True)
        self.tangent_snap = QCheckBox(t("planner.gfx.snap.tangent"))
        self.tangent_snap.setObjectName("gfx-tangent-snap")
        self.angle_snap = QCheckBox(t("planner.gfx.snap.angle"))
        self.angle_snap.setObjectName("gfx-angle-snap")
        self.angle_step = QDoubleSpinBox()
        self.angle_step.setObjectName("gfx-angle-step")
        self.angle_step.setRange(1.0, 90.0)
        self.angle_step.setValue(DEFAULT_ANGLE_STEP_DEG)
        self.angle_step.setSuffix("°")
        for box in (self.grid_snap, self.point_snap, self.tangent_snap, self.angle_snap):
            box.toggled.connect(self._apply_snapping)
        for spin in (self.grid_size, self.angle_step):
            spin.valueChanged.connect(self._apply_snapping)
        row.addWidget(QLabel(t("planner.gfx.draw_on")))
        row.addWidget(self.layer_box)
        row.addWidget(self.edge_slot)
        row.addSpacing(12)
        for widget in (
            self.grid_snap,
            self.grid_size,
            self.point_snap,
            self.tangent_snap,
            self.angle_snap,
            self.angle_step,
        ):
            row.addWidget(widget)
        row.addStretch(1)
        return row

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(4, 0, 4, 0)
        self.notice = QLabel("")
        self.notice.setObjectName("gfx-notice")
        self.notice.setWordWrap(True)
        column.addWidget(self.notice)
        column.addWidget(self._build_elements())
        column.addWidget(self._build_selection())
        column.addWidget(self._build_lanes())
        column.addWidget(self._build_joints())
        column.addWidget(self._build_contours())
        column.addWidget(self._build_borders())
        column.addWidget(self._build_problems())
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("gfx-panel")
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(340)
        return scroll

    def _group(self, key: str, name: str) -> tuple[QGroupBox, QVBoxLayout]:
        box = QGroupBox(self.translator.translate(key))
        box.setObjectName(name)
        inner = QVBoxLayout(box)
        return box, inner

    def _build_elements(self) -> QWidget:
        box, inner = self._group("planner.gfx.elements", "gfx-group-elements")
        self.elements = QListWidget()
        self.elements.setObjectName("gfx-elements")
        self.elements.setMaximumHeight(130)
        self.elements.currentRowChanged.connect(self._element_chosen)
        inner.addWidget(self.elements)
        return box

    def _build_selection(self) -> QWidget:
        t = self.translator.translate
        box, inner = self._group("planner.gfx.selection", "gfx-group-selection")
        self.selection_label = QLabel("")
        self.selection_label.setObjectName("gfx-selection")
        self.selection_label.setWordWrap(True)
        inner.addWidget(self.selection_label)
        form = QFormLayout()
        self._form = form
        self.handle_box = QComboBox()
        self.handle_box.setObjectName("gfx-handle")
        self.handle_box.currentIndexChanged.connect(self._handle_chosen)
        self.point_x = _spin("gfx-point-x", -_FIELD_MM, _FIELD_MM, 3)
        self.point_y = _spin("gfx-point-y", -_FIELD_MM, _FIELD_MM, 3)
        self.line_length = _spin("gfx-line-length", 0.001, _FIELD_MM, 3)
        self.line_angle = _spin("gfx-line-angle", -360.0, 360.0, 3)
        self.arc_radius = _spin("gfx-arc-radius", 0.1, _FIELD_MM, 3)
        self.arc_start = _spin("gfx-arc-start", -720.0, 720.0, 3)
        self.arc_sweep = _spin("gfx-arc-sweep", -359.99, 359.99, 3)
        self.tangent_box = QComboBox()
        self.tangent_box.setObjectName("gfx-tangent")
        for mode in ed.TANGENT_MODES:
            self.tangent_box.addItem(t(f"planner.gfx.tangent.{mode}"), mode)
        self.tangent_box.activated.connect(self._tangent_chosen)
        self.conn_x = _spin("gfx-conn-x", -_FIELD_MM, _FIELD_MM, 3)
        self.conn_y = _spin("gfx-conn-y", -_FIELD_MM, _FIELD_MM, 3)
        self.conn_dir = _spin("gfx-conn-dir", 0.0, 360.0, 3)
        self.conn_lanes = QLineEdit()
        self.conn_lanes.setObjectName("gfx-conn-lanes")
        self.conn_lanes.editingFinished.connect(self._connector_lanes_edited)
        self.lane_number = QSpinBox()
        self.lane_number.setObjectName("gfx-lane-number")
        self.lane_number.setRange(1, MAX_LANE_COUNT)
        self.lane_number.setKeyboardTracking(False)
        self.lane_number.valueChanged.connect(self._lane_number_edited)
        rows = (
            ("planner.gfx.handle", self.handle_box),
            ("planner.field.x", self.point_x),
            ("planner.field.y", self.point_y),
            ("planner.gfx.line_length", self.line_length),
            ("planner.gfx.line_angle", self.line_angle),
            ("planner.field.radius", self.arc_radius),
            ("planner.gfx.arc_start", self.arc_start),
            ("planner.gfx.arc_sweep", self.arc_sweep),
            ("planner.gfx.tangent", self.tangent_box),
            ("planner.gfx.conn_x", self.conn_x),
            ("planner.gfx.conn_y", self.conn_y),
            ("planner.gfx.conn_dir", self.conn_dir),
            ("planner.gfx.conn_lanes", self.conn_lanes),
            ("planner.gfx.lane_number", self.lane_number),
        )
        for key, widget in rows:
            form.addRow(t(key), widget)
        for spin in (self.point_x, self.point_y):
            spin.valueChanged.connect(self._point_edited)
        self.line_length.valueChanged.connect(self._line_edited)
        self.line_angle.valueChanged.connect(self._line_edited)
        for spin in (self.arc_radius, self.arc_start, self.arc_sweep):
            spin.valueChanged.connect(self._arc_edited)
        for spin in (self.conn_x, self.conn_y, self.conn_dir):
            spin.valueChanged.connect(self._connector_edited)
        inner.addLayout(form)
        buttons = QHBoxLayout()
        self.to_bezier = self._plain_button(
            "planner.gfx.to_bezier", "gfx-to-bezier", self._to_bezier
        )
        self.to_line = self._plain_button("planner.gfx.to_line", "gfx-to-line", self._to_line)
        self.flip_arc = self._plain_button("planner.gfx.flip_arc", "gfx-flip-arc", self._flip_arc)
        self.reset_handle = self._plain_button(
            "planner.gfx.reset_handle", "gfx-reset-handle", self._reset_handle
        )
        for button in (self.to_bezier, self.to_line, self.flip_arc, self.reset_handle):
            buttons.addWidget(button)
        inner.addLayout(buttons)
        return box

    def _build_lanes(self) -> QWidget:
        box, inner = self._group("planner.gfx.lanes", "gfx-group-lanes")
        row = QHBoxLayout()
        self.lane_add = self._plain_button("planner.gfx.lane_add", "gfx-lane-add", self._lane_add)
        self.lane_remove = self._plain_button(
            "planner.gfx.lane_remove", "gfx-lane-remove", self._lane_remove
        )
        row.addWidget(self.lane_add)
        row.addWidget(self.lane_remove)
        inner.addLayout(row)
        self.lane_info = QLabel("")
        self.lane_info.setObjectName("gfx-lane-lengths")
        self.lane_info.setWordWrap(True)
        self.route_info = QLabel("")
        self.route_info.setObjectName("gfx-route")
        self.route_info.setWordWrap(True)
        inner.addWidget(self.lane_info)
        inner.addWidget(self.route_info)
        return box

    def _build_joints(self) -> QWidget:
        box, inner = self._group("planner.gfx.paths", "gfx-group-paths")
        row = QHBoxLayout()
        self.path_reverse = self._plain_button(
            "planner.gfx.path_reverse", "gfx-path-reverse", self._path_reverse
        )
        self.path_join = self._plain_button(
            "planner.gfx.path_join", "gfx-path-join", self._path_join
        )
        self.gap_close = self._plain_button(
            "planner.gfx.gap_close", "gfx-gap-close", self._gap_close
        )
        for button in (self.path_reverse, self.path_join, self.gap_close):
            row.addWidget(button)
        inner.addLayout(row)
        row = QHBoxLayout()
        self.conn_add = self._plain_button("planner.gfx.conn_add", "gfx-conn-add", self._conn_add)
        self.conn_remove = self._plain_button(
            "planner.gfx.conn_remove", "gfx-conn-remove", self._conn_remove
        )
        row.addWidget(self.conn_add)
        row.addWidget(self.conn_remove)
        inner.addLayout(row)
        return box

    def _build_contours(self) -> QWidget:
        box, inner = self._group("planner.gfx.contours", "gfx-group-contours")
        row = QHBoxLayout()
        self.contour_make = self._plain_button(
            "planner.gfx.contour_from_outline", "gfx-contour-from-outline", self._contour_make
        )
        self.contour_close = self._plain_button(
            "planner.gfx.contour_close", "gfx-contour-close", self._contour_close
        )
        row.addWidget(self.contour_make)
        row.addWidget(self.contour_close)
        inner.addLayout(row)
        self.contour_note = QLabel("")
        self.contour_note.setObjectName("gfx-contour-note")
        self.contour_note.setWordWrap(True)
        inner.addWidget(self.contour_note)
        return box

    def _build_borders(self) -> QWidget:
        t = self.translator.translate
        box, inner = self._group("planner.gfx.borders", "gfx-group-borders")
        self.edges_add = self._plain_button(
            "planner.gfx.edges_add", "gfx-edges-add", self._edges_add
        )
        self.border_convert = self._plain_button(
            "planner.gfx.border_convert", "gfx-border-convert", self._border_convert
        )
        inner.addWidget(self.edges_add)
        inner.addWidget(self.border_convert)
        form = QFormLayout()
        self.strip_width = _spin("gfx-strip-width", 1.0, 400.0, 1)
        self.strip_width.setValue(30.0)
        self.strip_side = QComboBox()
        self.strip_side.setObjectName("gfx-strip-side")
        self.strip_side.addItem(t("planner.gfx.strip_left"), True)
        self.strip_side.addItem(t("planner.gfx.strip_right"), False)
        form.addRow(t("planner.gfx.strip_width"), self.strip_width)
        form.addRow(t("planner.gfx.strip_side"), self.strip_side)
        inner.addLayout(form)
        self.strip_make = self._plain_button(
            "planner.gfx.strip_make", "gfx-strip-make", self._strip_make
        )
        inner.addWidget(self.strip_make)
        self.host_box = QComboBox()
        self.host_box.setObjectName("gfx-host-part")
        self.host_slot = QComboBox()
        self.host_slot.setObjectName("gfx-host-slot")
        self.copy_edge = self._plain_button(
            "planner.gfx.copy_edge", "gfx-copy-edge", self._copy_edge
        )
        self.host_box.currentIndexChanged.connect(self._host_chosen)
        inner.addWidget(QLabel(t("planner.gfx.host")))
        inner.addWidget(self.host_box)
        inner.addWidget(self.host_slot)
        inner.addWidget(self.copy_edge)
        self.compat = QListWidget()
        self.compat.setObjectName("gfx-compat")
        self.compat.setMaximumHeight(90)
        inner.addWidget(QLabel(t("planner.gfx.compat")))
        inner.addWidget(self.compat)
        return box

    def _build_problems(self) -> QWidget:
        box, inner = self._group("planner.gfx.problems", "gfx-group-problems")
        self.problems = QListWidget()
        self.problems.setObjectName("gfx-issues")
        self.problems.setMaximumHeight(120)
        self.problem_state = QLabel("")
        self.problem_state.setObjectName("gfx-problem-state")
        inner.addWidget(self.problem_state)
        inner.addWidget(self.problems)
        return box

    def _plain_button(self, key: str, name: str, handler: Callable[[], None]) -> QPushButton:
        button = QPushButton(self.translator.translate(key))
        button.setObjectName(name)
        button.clicked.connect(lambda _checked=False: handler())
        return button

    # ------------------------------------------------------------------ options

    def _read_library(self) -> tuple[PartRecord, ...]:
        try:
            records = self._planner.list_parts()
        except Exception:
            return ()
        return tuple(
            record for record in records if record.is_current and record.id != self._exclude_id
        )

    def _apply_snapping(self, *_args: object) -> None:
        self.controller.snapping = Snapping(
            grid=self.grid_snap.isChecked(),
            grid_mm=float(self.grid_size.value()),
            points=self.point_snap.isChecked(),
            tangent=self.tangent_snap.isChecked(),
            angle=self.angle_snap.isChecked(),
            angle_step_deg=float(self.angle_step.value()),
        )
        self.canvas.update()

    def _set_layer_choices(self) -> None:
        spec = self.session.spec
        layers = [ed.LAYER_GROOVE, ed.LAYER_CENTER, ed.LAYER_CONTOUR]
        if spec.attachment is None:
            layers.append(ed.LAYER_EDGE)
        else:
            layers = [ed.LAYER_CONTOUR, ed.LAYER_ATTACH]
        self.layer_box.blockSignals(True)
        self.layer_box.clear()
        for layer in layers:
            self.layer_box.addItem(self.translator.translate(_LAYER_KEYS[layer]), layer)
        self.layer_box.blockSignals(False)
        self.controller.layer = str(self.layer_box.itemData(0))

    def _layer_changed(self) -> None:
        layer = self.layer_box.currentData()
        if isinstance(layer, str):
            self.controller.set_layer(layer)
        self._refresh_enabled()

    def _edge_slot_changed(self) -> None:
        slot = self.edge_slot.currentData()
        if isinstance(slot, str):
            self.controller.edge_slot = slot

    def _choose_tool(self, tool: str) -> None:
        if self.read_only:
            return
        self.controller.set_tool(tool)
        index = self.layer_box.findData(self.controller.layer)
        if index >= 0:
            self.layer_box.blockSignals(True)
            self.layer_box.setCurrentIndex(index)
            self.layer_box.blockSignals(False)
        self.canvas.update()
        self._refresh_enabled()

    # ------------------------------------------------------------------ refresh

    def _session_changed(self) -> None:
        if self._syncing:
            return
        if self.session.is_live:
            self._refresh_properties()
            self.canvas.update()
            return
        self._refresh_all()
        self.changed.emit()

    def _selection_changed(self) -> None:
        if self._syncing:
            return
        self._refresh_properties()
        self._sync_elements()
        self.canvas.update()

    def _refresh_all(self) -> None:
        self._syncing = True
        try:
            self._fill_elements()
            self._refresh_lanes()
            self._refresh_problems()
            self._refresh_notice()
            self._refresh_borders()
        finally:
            self._syncing = False
        self._refresh_properties()
        self._refresh_enabled()
        self.canvas.update()

    def _refresh_enabled(self) -> None:
        spec = self.session.spec
        writable = not self.read_only
        for tool, button in self._tool_buttons.items():
            button.setEnabled(writable)
            if tool == TOOL_CONNECTOR:
                button.setEnabled(writable and spec.attachment is None)
        self.undo_button.setEnabled(writable and self.session.can_undo)
        self.redo_button.setEnabled(writable and self.session.can_redo)
        for widget in (
            self.layer_box,
            self.edge_slot,
            self.lane_add,
            self.lane_remove,
            self.path_reverse,
            self.path_join,
            self.gap_close,
            self.conn_add,
            self.conn_remove,
            self.contour_make,
            self.contour_close,
        ):
            widget.setEnabled(writable)
        part = spec.attachment is None
        border = spec.attachment is not None
        standard = border and self._reason == ed.SUPPORT_STANDARD_BORDER
        self.lane_add.setEnabled(writable and part)
        self.lane_remove.setEnabled(writable and part)
        self.conn_add.setEnabled(writable and part)
        self.conn_remove.setEnabled(writable and part)
        self.edges_add.setEnabled(writable and part)
        self.edges_add.setVisible(part)
        self.border_convert.setVisible(border)
        self.border_convert.setEnabled(standard)
        self.strip_make.setVisible(border)
        self.strip_make.setEnabled(writable and border)
        self.copy_edge.setVisible(border)
        self.copy_edge.setEnabled(writable and border and self.host_slot.count() > 0)
        self.host_box.setVisible(border)
        self.host_slot.setVisible(border)
        self.edge_slot.setVisible(self.controller.layer in {ed.LAYER_EDGE, ed.LAYER_ATTACH})
        self.fit_button.setEnabled(True)

    def _refresh_notice(self) -> None:
        t = self.translator.translate
        lines: list[str] = []
        if self._reason:
            lines.append(t(_REASON_KEYS.get(self._reason, "planner.gfx.reason.category")))
        spec = self.session.spec
        if len(spec.contours) > 1:
            lines.append(t("planner.gfx.hole_note"))
        if spec.attachment is not None and any(
            span.kind == SPAN_BEZIER for path in spec.attachment.edge for span in (path,)
        ):
            lines.append(t("planner.gfx.strip_bezier_note"))
        self.notice.setText("\n".join(lines))
        self.notice.setVisible(bool(lines))

    def _fill_elements(self) -> None:
        t = self.translator.translate
        spec = self.session.spec
        current = self.elements.currentRow()
        self.elements.blockSignals(True)
        self.elements.clear()
        for ref in ed.path_refs(spec):
            try:
                spans = ed.spans_of(spec, ref)
                length = sum(ed.span_length_mm(span) for span in spans)
            except ValidationError:
                continue
            name = t(_LAYER_KEYS[ref.layer])
            lane = ed.lane_of(spec, ref)
            if lane:
                name = f"{name} {lane}"
            slot = ed.slot_of(spec, ref)
            if slot:
                name = f"{name} ({t(f'planner.editor.side.{slot}')})"
            elif ref.layer == ed.LAYER_CONTOUR:
                name = f"{name} {ref.index + 1}"
            item = QListWidgetItem(
                self.translator.format(
                    "planner.gfx.element", name=name, count=len(spans), length=f"{length:.1f}"
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, ("path", ref))
            self.elements.addItem(item)
        for index, joint in enumerate(spec.connectors):
            kind = "planner.gfx.rail" if joint.kind == TRACK else "planner.gfx.joint_other"
            text = self.translator.format(
                "planner.gfx.connector",
                kind=t(kind),
                name=joint.name,
                direction=f"{joint.direction_deg:.0f}",
                lanes=",".join(str(lane) for lane in joint.lanes),
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ("conn", index))
            self.elements.addItem(item)
        if 0 <= current < self.elements.count():
            self.elements.setCurrentRow(current)
        self.elements.blockSignals(False)
        self._sync_elements()

    def _sync_elements(self) -> None:
        target = self.controller.selection
        self.elements.blockSignals(True)
        row = -1
        for index in range(self.elements.count()):
            kind, value = self.elements.item(index).data(Qt.ItemDataRole.UserRole)
            if target is None:
                continue
            if kind == "path" and target.path == value and target.kind != KIND_CONNECTOR:
                row = index
            if kind == "conn" and target.kind == KIND_CONNECTOR and target.connector == value:
                row = index
        self.elements.setCurrentRow(row)
        self.elements.blockSignals(False)

    def _element_chosen(self, row: int) -> None:
        if row < 0 or self._syncing:
            return
        data = self.elements.item(row).data(Qt.ItemDataRole.UserRole)
        kind, value = data
        if kind == "path":
            self.controller.select_path(value)
        else:
            self.controller.select(Target(KIND_CONNECTOR, connector=int(value)))

    def _refresh_lanes(self) -> None:
        t = self.translator.translate
        result = self._safe_result()
        rows = lane_rows(result)
        lines = [
            self.translator.format(
                "planner.gfx.lane_length",
                lane=row.lane,
                length="—" if row.length_mm is None else f"{row.length_mm:.1f} mm",
            )
            for row in rows
        ]
        self.lane_info.setText("\n".join(lines))
        if result.attachment is not None or not rows:
            self.route_info.setText("")
            return
        simple = route_is_simple(result)
        self.route_info.setText(
            t("planner.gfx.route_simple" if simple else "planner.gfx.route_open")
        )

    def _safe_result(self) -> PartSpec:
        try:
            return self.session.result()
        except ValidationError:
            return self.session.spec

    def _refresh_problems(self) -> None:
        t = self.translator.translate
        issues = self.session.issues()
        self.problems.clear()
        for issue in issues:
            text = t(issue.key)
            if issue.where:
                text = f"{text} ({issue.where})"
            self.problems.addItem(text)
        if self.read_only:
            self.problem_state.setText(t("planner.gfx.read_only"))
        elif issues:
            self.problem_state.setText(
                self.translator.format("planner.gfx.problems_count", count=len(issues))
            )
        else:
            self.problem_state.setText(t("planner.gfx.problems_none"))

    def _refresh_borders(self) -> None:
        t = self.translator.translate
        spec = self._safe_result()
        previous = self.host_box.currentData()
        self.host_box.blockSignals(True)
        self.host_box.clear()
        if spec.attachment is not None:
            for record in self._library:
                if record.spec.border_edges:
                    self.host_box.addItem(record.spec.name, record.id)
            index = self.host_box.findData(previous)
            if index >= 0:
                self.host_box.setCurrentIndex(index)
        self.host_box.blockSignals(False)
        self._host_chosen()
        self.compat.clear()
        for record in self._library:
            for slot in sorted(BORDER_SLOTS):
                if spec.attachment is not None and attachment_fits(spec, record.spec, slot):
                    self.compat.addItem(
                        self.translator.format(
                            "planner.gfx.fits_host",
                            name=record.spec.name,
                            slot=t(f"planner.editor.side.{slot}"),
                        )
                    )
                elif (
                    spec.attachment is None
                    and record.spec.attachment is not None
                    and attachment_fits(record.spec, spec, slot)
                ):
                    self.compat.addItem(
                        self.translator.format(
                            "planner.gfx.fits_border",
                            name=record.spec.name,
                            slot=t(f"planner.editor.side.{slot}"),
                        )
                    )

    def _host_chosen(self) -> None:
        self.host_slot.clear()
        record_id = self.host_box.currentData()
        record = next((item for item in self._library if item.id == record_id), None)
        if record is None:
            return
        for edge in record.spec.border_edges:
            self.host_slot.addItem(
                self.translator.translate(f"planner.editor.side.{edge.slot}"), edge.slot
            )
        self.copy_edge.setEnabled(not self.read_only and self.host_slot.count() > 0)

    # ------------------------------------------------------------------ properties

    def _refresh_properties(self) -> None:
        t = self.translator.translate
        controller = self.controller
        target = controller.selection
        span = controller.selected_span()
        writable = not self.read_only
        self._syncing = True
        try:
            self.selection_label.setText(self._describe(target))
            show_point = target is not None and target.kind == KIND_POINT and span is not None
            self._row(self.handle_box, span is not None)
            self.handle_box.blockSignals(True)
            self.handle_box.clear()
            if span is not None:
                for name in ed.handle_points(span):
                    self.handle_box.addItem(t(f"planner.gfx.handle.{name}"), name)
                if target is not None and target.kind == KIND_POINT:
                    self.handle_box.setCurrentIndex(self.handle_box.findData(target.handle))
                else:
                    self.handle_box.setCurrentIndex(-1)
            self.handle_box.blockSignals(False)
            self._row(self.point_x, show_point)
            self._row(self.point_y, show_point)
            if show_point and target is not None and span is not None:
                x, y = ed.handle_points(span)[target.handle]
                self.point_x.setValue(x)
                self.point_y.setValue(y)
            line = span is not None and span.kind == SPAN_LINE
            self._row(self.line_length, line)
            self._row(self.line_angle, line)
            if line and span is not None:
                self.line_length.setValue(math.hypot(span.x1 - span.x0, span.y1 - span.y0))
                self.line_angle.setValue(
                    math.degrees(math.atan2(span.y1 - span.y0, span.x1 - span.x0))
                )
            arc = span is not None and span.kind == SPAN_ARC
            for widget in (self.arc_radius, self.arc_start, self.arc_sweep):
                self._row(widget, arc)
            if arc and span is not None:
                self.arc_radius.setValue(span.radius_mm)
                self.arc_start.setValue(span.start_deg)
                self.arc_sweep.setValue(span.sweep_deg)
            joint = self._joint_index(target)
            self._row(self.tangent_box, joint is not None)
            if joint is not None and target is not None and target.path is not None:
                report = ed.joint_report(self.session.spec, target.path)
                smooth = report[joint][1] <= 1.0
                self.tangent_box.setCurrentIndex(
                    self.tangent_box.findData(ed.TANGENT_SMOOTH if smooth else ed.TANGENT_KINK)
                )
            connector = target is not None and target.kind == KIND_CONNECTOR
            for field in (self.conn_x, self.conn_y, self.conn_dir, self.conn_lanes):
                self._row(field, connector)
            if connector and target is not None:
                item = self.session.spec.connectors[target.connector]
                self.conn_x.setValue(item.x_mm)
                self.conn_y.setValue(item.y_mm)
                self.conn_dir.setValue(item.direction_deg)
                self.conn_lanes.setText(",".join(str(lane) for lane in item.lanes))
            groove = (
                target is not None
                and target.path is not None
                and target.path.layer == ed.LAYER_GROOVE
                and target.kind != KIND_CONNECTOR
            )
            self._row(self.lane_number, groove)
            if groove and target is not None and target.path is not None:
                lane = self.session.spec.slot_paths[target.path.index].lane
                self.lane_number.setRange(1, max(ed.lane_numbers(self.session.spec) or (1,)))
                self.lane_number.setValue(lane or 1)
            self.to_bezier.setVisible(line)
            self.to_line.setVisible(span is not None and span.kind == SPAN_BEZIER)
            self.flip_arc.setVisible(arc)
            self.reset_handle.setVisible(
                show_point and target is not None and target.handle in {"p1", "p2"}
            )
            for control in (
                self.handle_box,
                self.point_x,
                self.point_y,
                self.line_length,
                self.line_angle,
                self.arc_radius,
                self.arc_start,
                self.arc_sweep,
                self.tangent_box,
                self.conn_x,
                self.conn_y,
                self.conn_dir,
                self.conn_lanes,
                self.lane_number,
                self.to_bezier,
                self.to_line,
                self.flip_arc,
                self.reset_handle,
            ):
                control.setEnabled(writable)
        finally:
            self._syncing = False

    def _row(self, widget: QWidget, visible: bool) -> None:
        self._form.setRowVisible(widget, visible)

    def _describe(self, target: Target | None) -> str:
        t = self.translator.translate
        if target is None:
            return t("planner.gfx.nothing")
        if target.kind == KIND_CONNECTOR:
            item = self.session.spec.connectors[target.connector]
            return self.translator.format(
                "planner.gfx.sel.connector", name=item.name, direction=f"{item.direction_deg:.1f}"
            )
        if target.path is None:
            return t("planner.gfx.nothing")
        spec = self.session.spec
        name = t(_LAYER_KEYS[target.path.layer])
        lane = ed.lane_of(spec, target.path)
        if lane:
            name = f"{name} {lane}"
        if target.kind == KIND_PATH:
            return name
        span = self.controller.selected_span()
        shape = "" if span is None else t(f"planner.gfx.shape.{span.kind}")
        text = f"{name}: {t('planner.gfx.segment')} {target.segment + 1} ({shape})"
        if target.kind == KIND_POINT:
            text = f"{text}, {t(f'planner.gfx.handle.{target.handle}')}"
        return text

    @staticmethod
    def _joint_index(target: Target | None) -> int | None:
        if target is None or target.kind != KIND_POINT or target.path is None:
            return None
        if target.handle in {"end", "p3"}:
            return target.segment
        if target.handle in {"start", "p0"} and target.segment > 0:
            return target.segment - 1
        return None

    # ------------------------------------------------------------------ actions

    def _undo(self) -> None:
        if not self.read_only:
            self.session.undo()

    def _redo(self) -> None:
        if not self.read_only:
            self.session.redo()

    def _status_message(self, key: str) -> None:
        self.status.setText("" if not key else self.translator.translate(key))

    def _run(self, change: Callable[[PartSpec], PartSpec]) -> bool:
        if self.read_only or self._syncing:
            return False
        return self.controller.run(change)

    def _handle_chosen(self) -> None:
        if self._syncing:
            return
        target = self.controller.selection
        handle = self.handle_box.currentData()
        if target is not None and target.path is not None and isinstance(handle, str):
            self.controller.select(Target(KIND_POINT, target.path, target.segment, handle))

    def _point_edited(self) -> None:
        target = self.controller.selection
        point = None if target is None else target.point()
        if point is None or self._syncing:
            return
        x, y = self.point_x.value(), self.point_y.value()
        self._run(lambda spec: ed.move_point(spec, point, x, y))

    def _line_edited(self) -> None:
        target = self.controller.selection
        if target is None or target.path is None or self._syncing:
            return
        path, segment = target.path, target.segment
        length, angle = self.line_length.value(), self.line_angle.value()
        self._run(lambda spec: ed.set_line(spec, path, segment, length=length, angle=angle))

    def _arc_edited(self) -> None:
        target = self.controller.selection
        if target is None or target.path is None or self._syncing:
            return
        path, segment = target.path, target.segment
        radius, start, sweep = (
            self.arc_radius.value(),
            self.arc_start.value(),
            self.arc_sweep.value(),
        )
        self._run(
            lambda spec: ed.set_arc(spec, path, segment, radius=radius, start=start, sweep=sweep)
        )

    def _tangent_chosen(self) -> None:
        target = self.controller.selection
        mode = self.tangent_box.currentData()
        joint = self._joint_index(target)
        if target is None or target.path is None or joint is None or not isinstance(mode, str):
            return
        path = target.path
        self._run(lambda spec: ed.set_tangent(spec, path, joint, mode))

    def _connector_edited(self) -> None:
        target = self.controller.selection
        if target is None or target.kind != KIND_CONNECTOR or self._syncing:
            return
        index = target.connector
        x, y, heading = self.conn_x.value(), self.conn_y.value(), self.conn_dir.value()
        self._run(lambda spec: ed.set_connector(spec, index, x_mm=x, y_mm=y, direction_deg=heading))

    def _connector_lanes_edited(self) -> None:
        target = self.controller.selection
        if target is None or target.kind != KIND_CONNECTOR or self._syncing:
            return
        index = target.connector
        try:
            lanes = tuple(
                int(piece)
                for piece in self.conn_lanes.text().replace(";", ",").split(",")
                if piece.strip()
            )
        except ValueError:
            self._status_message("error.planner.lane")
            return
        if (
            not lanes
            or len(set(lanes)) != len(lanes)
            or not all(1 <= n <= MAX_LANE_COUNT for n in lanes)
        ):
            self._status_message("error.planner.lane")
            return
        self._run(lambda spec: ed.set_connector(spec, index, lanes=lanes))

    def _lane_number_edited(self) -> None:
        target = self.controller.selection
        if target is None or target.path is None or self._syncing:
            return
        path = target.path
        lane = self.lane_number.value()
        self._run(lambda spec: ed.set_lane(spec, path, lane))

    def _to_bezier(self) -> None:
        self._convert(SPAN_BEZIER)

    def _to_line(self) -> None:
        self._convert(SPAN_LINE)

    def _convert(self, kind: str) -> None:
        target = self.controller.selection
        if target is None or target.path is None:
            return
        path, segment = target.path, target.segment
        self._run(lambda spec: ed.convert_span(spec, path, segment, kind))

    def _flip_arc(self) -> None:
        target = self.controller.selection
        if target is None or target.path is None:
            return
        path, segment = target.path, target.segment
        self._run(lambda spec: ed.flip_arc(spec, path, segment))

    def _reset_handle(self) -> None:
        target = self.controller.selection
        if target is None or target.path is None:
            return
        path, segment, handle = target.path, target.segment, target.handle
        self._run(lambda spec: ed.reset_handle(spec, path, segment, handle))

    def _lane_add(self) -> None:
        if self.read_only:
            return
        if self.controller.run_with_ref(ed.add_lane):
            return

    def _lane_remove(self) -> None:
        target = self.controller.selection
        spec = self.session.spec
        lane: int | None = None
        if target is not None and target.path is not None:
            lane = ed.lane_of(spec, target.path)
        if lane is None:
            lanes = ed.lane_numbers(spec)
            lane = lanes[-1] if lanes else None
        if lane is None:
            return
        chosen = lane
        self._run(lambda current: ed.remove_lane(current, chosen))

    def _path_reverse(self) -> None:
        target = self.controller.selection
        if target is None or target.path is None:
            return
        path = target.path
        self._run(lambda spec: ed.reverse_path(spec, path))

    def _path_join(self) -> None:
        target = self.controller.selection
        if target is None or target.path is None:
            return
        first = target.path
        spec = self.session.spec
        best: tuple[float, ed.PathRef] | None = None
        end = ed.span_end(ed.spans_of(spec, first)[-1])
        for ref in ed.path_refs(spec):
            if ref.layer != first.layer or ref == first:
                continue
            spans = ed.spans_of(spec, ref)
            start = ed.span_start(spans[0])
            distance = math.hypot(start[0] - end[0], start[1] - end[1])
            if best is None or distance < best[0]:
                best = (distance, ref)
        if best is None:
            self._status_message("error.planner.geometry")
            return
        second = best[1]
        self._run(lambda current: ed.join_paths(current, first, second))
        self.controller.select_path(
            first if second.index > first.index else ed.PathRef(first.layer, first.index - 1)
        )

    def _gap_close(self) -> None:
        target = self.controller.selection
        if target is None or target.path is None:
            return
        path = target.path

        def close_all(spec: PartSpec) -> PartSpec:
            current = spec
            joint = len(ed.spans_of(current, path)) - 2
            while joint >= 0:
                current = ed.close_gap(current, path, joint)
                joint -= 1
            return current

        self._run(close_all)

    def _conn_add(self) -> None:
        spec = self.session.spec
        rails = ed.rail_connectors(spec)
        if rails:
            last = rails[-1]
            x, y, heading = last.x_mm, last.y_mm + 20.0, last.direction_deg
        else:
            x, y, heading = 0.0, 0.0, 0.0
        self._run(lambda current: ed.add_connector(current, x, y, heading))
        self.controller.select(
            Target(KIND_CONNECTOR, connector=len(self.session.spec.connectors) - 1)
        )

    def _conn_remove(self) -> None:
        target = self.controller.selection
        if target is None or target.kind != KIND_CONNECTOR:
            self._status_message("error.planner.editor.connectors")
            return
        index = target.connector
        self._run(lambda spec: ed.remove_connector(spec, index))

    def _contour_make(self) -> None:
        self._run(ed.contour_from_outline)

    def _contour_close(self) -> None:
        target = self.controller.selection
        if target is None or target.path is None:
            return
        path = target.path
        self._run(lambda spec: ed.close_path(spec, path))

    def _edges_add(self) -> None:
        self._run(ed.with_side_edges)

    def _border_convert(self) -> None:
        t = self.translator.translate
        if not ask(self, t("planner.gfx.border_convert"), t("planner.gfx.border_convert_confirm")):
            return
        if self.controller.run(ed.to_custom_border):
            self._reason = ""
            self.canvas.read_only = False
            self._set_layer_choices()
            self._refresh_all()

    def _strip_make(self) -> None:
        width = self.strip_width.value()
        left = bool(self.strip_side.currentData())
        self._run(lambda spec: ed.strip_from_edge(spec, width, left=left))

    def _copy_edge(self) -> None:
        record_id = self.host_box.currentData()
        slot = self.host_slot.currentData()
        record = next((item for item in self._library if item.id == record_id), None)
        if record is None or not isinstance(slot, str):
            return
        host = record.spec
        self._run(lambda spec: ed.copy_edge(spec, host, slot))


def _spin(name: str, low: float, high: float, decimals: int) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setObjectName(name)
    spin.setRange(low, high)
    spin.setDecimals(decimals)
    spin.setKeyboardTracking(False)
    return spin
