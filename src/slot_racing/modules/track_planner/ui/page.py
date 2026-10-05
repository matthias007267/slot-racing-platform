"""Track planner page: pick a track, edit its plan, save or discard."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.domain import TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.document import (
    CLOCKWISE,
    COUNTERCLOCKWISE,
    CURVE_90,
    STRAIGHT_H,
    STRAIGHT_V,
    Marker,
    Piece,
    TrackPlan,
    add_piece,
    empty_plan,
    move_marker,
    move_piece,
    next_origin,
    place_instance,
    place_start_finish,
    remove_instance,
    remove_marker,
    remove_piece,
    reposition_instance,
    reset_plan,
    rotate_instance,
    rotate_piece,
    set_direction,
    set_plan_grid,
)
from slot_racing.modules.track_planner.parts import PartInstance, PartSpec
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.canvas import PlanCanvas
from slot_racing.modules.track_planner.ui.library_dialog import PartDialog
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import configure_page, set_role
from slot_racing.uikit.widgets import StatusLabel


class PlannerPage(QWidget):
    def __init__(
        self, translator: Translator, tracks: TrackCatalog, planner: TrackPlannerService
    ) -> None:
        super().__init__()
        self._translator = translator
        self._tracks = tracks
        self._planner = planner
        self._plan = empty_plan(TrackId(0))
        self._lane_count = 0
        self._dirty = False
        self._filling = False
        self._parts: dict[int, PartSpec] = {}
        translate = translator.translate
        self.track_combo = QComboBox()
        self.track_combo.setObjectName("planner-track")
        self.track_combo.currentIndexChanged.connect(self._track_changed)
        self.lanes = QLabel(translator.format("planner.lanes", count=0))
        self.lanes.setObjectName("planner-lanes")
        self.direction_button = QPushButton(translate("planner.direction.clockwise"))
        self.direction_button.setObjectName("planner-direction")
        set_role(self.direction_button, "ghost")
        self.direction_button.clicked.connect(self._toggle_direction)
        self.save_button = QPushButton(translate("planner.save"))
        self.save_button.setObjectName("planner-save")
        self.save_button.clicked.connect(self.save)
        self.discard_button = QPushButton(translate("planner.discard"))
        self.discard_button.setObjectName("planner-discard")
        set_role(self.discard_button, "ghost")
        self.discard_button.clicked.connect(self.discard)
        self.reset_button = QPushButton(translate("planner.reset"))
        self.reset_button.setObjectName("planner-reset")
        set_role(self.reset_button, "ghost")
        self.reset_button.clicked.connect(self.reset)
        self.add_horizontal = self._tool(
            "planner-add-straight-h", "planner.tool.straight_h", self._add_horizontal
        )
        self.add_vertical = self._tool(
            "planner-add-straight-v", "planner.tool.straight_v", self._add_vertical
        )
        self.add_curve = self._tool("planner-add-curve", "planner.tool.curve", self._add_curve)
        self.add_start = self._tool("planner-add-start", "planner.tool.start", self._add_start)
        self.delete_button = self._tool(
            "planner-delete", "planner.tool.delete", self.delete_selected
        )
        self.library = QListWidget()
        self.library.setObjectName("planner-library")
        self.place_part = self._tool("planner-place", "planner.library.place", self._place_part)
        self.add_part = self._tool("planner-add-part", "planner.library.add", self._add_part)
        self.grid = QCheckBox(translate("planner.field.grid"))
        self.grid.setObjectName("planner-grid")
        self.grid.setChecked(True)
        self.grid_size = QDoubleSpinBox()
        self.grid_size.setObjectName("planner-grid-size")
        self.grid_size.setRange(1, 500)
        self.grid_size.setValue(10)
        self.snap_distance = QDoubleSpinBox()
        self.snap_distance.setObjectName("planner-snap")
        self.snap_distance.setRange(0, 500)
        self.snap_distance.setValue(25)
        self.rotation_free = QDoubleSpinBox()
        self.rotation_free.setObjectName("planner-rotation-free")
        self.rotation_free.setRange(0, 359.9)
        self.rotation_free.setDecimals(1)
        self.x_mm = QDoubleSpinBox()
        self.x_mm.setObjectName("planner-x-mm")
        self.x_mm.setRange(-100_000, 100_000)
        self.x_mm.setDecimals(1)
        self.y_mm = QDoubleSpinBox()
        self.y_mm.setObjectName("planner-y-mm")
        self.y_mm.setRange(-100_000, 100_000)
        self.y_mm.setDecimals(1)
        self.canvas = PlanCanvas()
        self.canvas.set_listener(self._moved)
        self.canvas.set_instance_listener(self._moved_instance)
        self.canvas.scene().selectionChanged.connect(self._show_selection)
        self.selection_label = QLabel(translate("planner.none"))
        self.selection_label.setObjectName("planner-selection")
        self.selection_label.setWordWrap(True)
        self.x_spin = self._spin("planner-x")
        self.y_spin = self._spin("planner-y")
        self.rotation = QComboBox()
        self.rotation.setObjectName("planner-rotation")
        for angle in (0, 90, 180, 270):
            self.rotation.addItem(f"{angle}°", angle)
        self.rotation.currentIndexChanged.connect(self._rotation_changed)
        self.x_spin.valueChanged.connect(self._position_changed)
        self.y_spin.valueChanged.connect(self._position_changed)
        self.rotation_free.valueChanged.connect(self._free_rotation_changed)
        self.x_mm.valueChanged.connect(self._millimetre_changed)
        self.y_mm.valueChanged.connect(self._millimetre_changed)
        self.grid.toggled.connect(self._grid_changed)
        self.grid_size.valueChanged.connect(self._grid_changed)
        self.snap_distance.valueChanged.connect(self._grid_changed)
        self.status = StatusLabel("planner-message")
        properties = QFormLayout()
        title = QLabel(translate("planner.properties"))
        set_role(title, "section")
        properties.addRow(title)
        properties.addRow(self.selection_label)
        properties.addRow(translate("planner.field.x"), self.x_spin)
        properties.addRow(translate("planner.field.y"), self.y_spin)
        properties.addRow(translate("planner.field.rotation"), self.rotation)
        properties.addRow(translate("planner.field.rotation_free"), self.rotation_free)
        properties.addRow(translate("planner.field.x"), self.x_mm)
        properties.addRow(translate("planner.field.y"), self.y_mm)
        properties.addRow(self.grid)
        properties.addRow(translate("planner.field.grid_size"), self.grid_size)
        properties.addRow(translate("planner.field.snap"), self.snap_distance)
        tools = QVBoxLayout()
        for button in (
            self.add_horizontal,
            self.add_vertical,
            self.add_curve,
            self.add_start,
            self.delete_button,
        ):
            tools.addWidget(button)
        library_title = QLabel(translate("planner.library"))
        set_role(library_title, "section")
        tools.addWidget(library_title)
        tools.addWidget(self.library, 1)
        tools.addWidget(self.place_part)
        tools.addWidget(self.add_part)
        tools.addStretch(1)
        header = QHBoxLayout()
        header.addWidget(QLabel(translate("planner.track")))
        header.addWidget(self.track_combo, 1)
        header.addWidget(self.lanes)
        header.addWidget(self.direction_button)
        header.addWidget(self.save_button)
        header.addWidget(self.discard_button)
        header.addWidget(self.reset_button)
        body = QHBoxLayout()
        body.addLayout(tools)
        body.addWidget(self.canvas, 1)
        side = QWidget()
        side.setObjectName("planner-properties")
        side.setLayout(properties)
        body.addWidget(side)
        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addLayout(header)
        layout.addLayout(body, 1)
        layout.addWidget(self.status)
        self._refresh_tracks()

    def plan(self) -> TrackPlan:
        return self._plan

    def save(self) -> None:
        if self._lane_count < 1:
            return
        try:
            self._plan = self._planner.save(self._plan)
        except Exception as error:
            self._report(error)
            return
        self._dirty = False
        self.status.show_info(self._translator.translate("planner.saved"))
        self._draw()

    def discard(self) -> None:
        track_id = self._current_track()
        if track_id is None:
            return
        self._open(track_id)
        self.status.show_info(self._translator.translate("planner.discarded"))

    def reset(self) -> None:
        if self._lane_count < 1:
            return
        self._plan = reset_plan(self._plan)
        self._dirty = True
        self._draw()
        self.status.show_info(self._translator.translate("planner.reset_done"))

    def delete_selected(self) -> None:
        selected = self.canvas.selected_id()
        if selected is None:
            return
        if any(piece.id == selected for piece in self._plan.pieces):
            self._plan = remove_piece(self._plan, selected)
        elif any(instance.id == selected for instance in self._plan.instances):
            self._plan = remove_instance(self._plan, selected)
        else:
            self._plan = remove_marker(self._plan, selected)
        self._dirty = True
        self._draw()

    def showEvent(self, event: object) -> None:  # noqa: N802
        super().showEvent(event)  # type: ignore[arg-type]
        if not self._dirty:
            self._refresh_tracks()

    def _tool(self, object_name: str, key: str, slot: Callable[[], None]) -> QPushButton:
        button = QPushButton(self._translator.translate(key))
        button.setObjectName(object_name)
        set_role(button, "ghost")
        button.clicked.connect(slot)
        return button

    def _spin(self, object_name: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setObjectName(object_name)
        spin.setRange(0, 63)
        return spin

    def _refresh_tracks(self) -> None:
        current = self._current_track()
        self._filling = True
        self.track_combo.clear()
        for track in self._tracks.list_tracks():
            self.track_combo.addItem(track.name, track.id)
        if self.track_combo.count() == 0:
            self._filling = False
            self._lane_count = 0
            self.lanes.setText(self._translator.format("planner.lanes", count=0))
            self._set_enabled(False)
            self.status.show_info(self._translator.translate("planner.empty"))
            return
        index = self.track_combo.findData(current)
        self.track_combo.setCurrentIndex(0 if index < 0 else index)
        self._filling = False
        self._open(self._current_track())

    def _track_changed(self) -> None:
        if self._filling:
            return
        if self._dirty:
            self.status.show_error(self._translator.translate("planner.unsaved"))
            self._filling = True
            index = self.track_combo.findData(self._plan.track_id)
            if index >= 0:
                self.track_combo.setCurrentIndex(index)
            self._filling = False
            return
        self._open(self._current_track())

    def _open(self, track_id: TrackId | None) -> None:
        if track_id is None:
            return
        track = self._tracks.get_track(track_id)
        if track is None:
            return
        try:
            self._plan = self._planner.load(track_id)
        except Exception as error:
            self._report(error)
            self._plan = empty_plan(track_id)
        self._lane_count = track.lane_count
        self._dirty = False
        self._load_parts()
        self._show_grid()
        self.lanes.setText(self._translator.format("planner.lanes", count=track.lane_count))
        self._set_enabled(True)
        self._draw()

    def _current_track(self) -> TrackId | None:
        value = self.track_combo.currentData()
        if value is None:
            return None
        return TrackId(int(value))

    def _add_horizontal(self) -> None:
        self._add(STRAIGHT_H)

    def _add_vertical(self) -> None:
        self._add(STRAIGHT_V)

    def _add_curve(self) -> None:
        self._add(CURVE_90)

    def _add(self, piece_type: str) -> None:
        if self._lane_count < 1:
            return
        origin = next_origin(self._plan, piece_type)
        self._plan = add_piece(self._plan, piece_type, origin[0], origin[1])
        self._dirty = True
        self._draw(self._plan.pieces[-1].id)

    def _add_start(self) -> None:
        if self._lane_count < 1:
            return
        current = self._plan.start_finish()
        if current is None:
            self._plan = place_start_finish(self._plan, 0, 0)
            self._dirty = True
        selected = self._plan.start_finish()
        self._draw(None if selected is None else selected.id)

    def _toggle_direction(self) -> None:
        if self._lane_count < 1:
            return
        direction = COUNTERCLOCKWISE if self._plan.direction == CLOCKWISE else CLOCKWISE
        self._plan = set_direction(self._plan, direction)
        self._dirty = True
        self._draw(self.canvas.selected_id())

    def _moved(self, item_id: str, x: int, y: int) -> None:
        if any(piece.id == item_id for piece in self._plan.pieces):
            self._plan = move_piece(self._plan, item_id, x, y)
        else:
            self._plan = move_marker(self._plan, item_id, x, y)
        self._dirty = True
        self._show_selection()

    def _position_changed(self) -> None:
        if self._filling:
            return
        selected = self._selected()
        if selected is None:
            return
        previous = self._plan
        x = self.x_spin.value()
        y = self.y_spin.value()
        try:
            if isinstance(selected, Piece):
                self._plan = move_piece(self._plan, selected.id, x, y)
            else:
                self._plan = move_marker(self._plan, selected.id, x, y)
        except ValidationError as error:
            self._plan = previous
            self._show_selection()
            self._report(error)
            return
        self._dirty = True
        self._draw(selected.id)

    def _rotation_changed(self) -> None:
        if self._filling:
            return
        selected = self._selected()
        if not isinstance(selected, Piece) or selected.piece_type != CURVE_90:
            return
        angle = self.rotation.currentData()
        if not isinstance(angle, int):
            return
        self._plan = rotate_piece(self._plan, selected.id, angle)
        self._dirty = True
        self._draw(selected.id)

    def _selected(self) -> Piece | Marker | PartInstance | None:
        selected = self.canvas.selected_id()
        if selected is None:
            return None
        for piece in self._plan.pieces:
            if piece.id == selected:
                return piece
        for instance in self._plan.instances:
            if instance.id == selected:
                return instance
        for marker in self._plan.markers:
            if marker.id == selected:
                return marker
        return None

    def _show_selection(self) -> None:
        selected = self._selected()
        self._filling = True
        if selected is None:
            self.selection_label.setText(self._translator.translate("planner.none"))
            self.x_spin.setEnabled(False)
            self.y_spin.setEnabled(False)
            self.rotation.setEnabled(False)
            self.rotation_free.setEnabled(False)
            self.x_mm.setEnabled(False)
            self.y_mm.setEnabled(False)
        elif isinstance(selected, PartInstance):
            spec = self._parts.get(selected.part_id)
            label = spec.name if spec is not None else self._translator.translate("planner.none")
            self.selection_label.setText(label)
            self.x_spin.setEnabled(False)
            self.y_spin.setEnabled(False)
            self.rotation.setEnabled(False)
            self.rotation_free.setEnabled(True)
            self.x_mm.setEnabled(True)
            self.y_mm.setEnabled(True)
            self.rotation_free.setValue(selected.rotation_z_deg)
            self.x_mm.setValue(selected.x_mm)
            self.y_mm.setValue(selected.y_mm)
        else:
            kind = selected.piece_type if isinstance(selected, Piece) else selected.kind
            self.selection_label.setText(self._translator.translate(f"planner.kind.{kind}"))
            self.x_spin.setEnabled(True)
            self.y_spin.setEnabled(True)
            self.x_spin.setValue(selected.x)
            self.y_spin.setValue(selected.y)
            curve = isinstance(selected, Piece) and selected.piece_type == CURVE_90
            self.rotation.setEnabled(curve)
            self.rotation_free.setEnabled(False)
            self.x_mm.setEnabled(False)
            self.y_mm.setEnabled(False)
            if isinstance(selected, Piece) and selected.piece_type == CURVE_90:
                index = self.rotation.findData(selected.rotation)
                if index >= 0:
                    self.rotation.setCurrentIndex(index)
        self._filling = False
        key = (
            "planner.direction.clockwise"
            if self._plan.direction == CLOCKWISE
            else "planner.direction.counterclockwise"
        )
        self.direction_button.setText(self._translator.translate(key))

    def _draw(self, selected: str | None = None) -> None:
        self.canvas.show_plan(self._plan, max(self._lane_count, 1), selected, self._parts)
        self._show_selection()

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.direction_button,
            self.save_button,
            self.discard_button,
            self.reset_button,
            self.add_horizontal,
            self.add_vertical,
            self.add_curve,
            self.add_start,
            self.delete_button,
            self.place_part,
            self.add_part,
            self.canvas,
        ):
            widget.setEnabled(enabled)

    def _load_parts(self) -> None:
        records = self._planner.list_parts()
        self._parts = {record.id: record.spec for record in records}
        current = self.library.currentRow()
        self.library.clear()
        for record in records:
            self.library.addItem(f"{record.spec.name} ({record.spec.article_number})")
            item = self.library.item(self.library.count() - 1)
            if item is not None:
                item.setData(Qt.ItemDataRole.UserRole, record.id)
        if self.library.count():
            self.library.setCurrentRow(0 if current < 0 else min(current, self.library.count() - 1))

    def _show_grid(self) -> None:
        self._filling = True
        self.grid.setChecked(self._plan.grid_enabled)
        self.grid_size.setValue(self._plan.grid_mm)
        self.snap_distance.setValue(self._plan.snap_mm)
        self._filling = False

    def _place_part(self) -> None:
        if self._lane_count < 1:
            return
        item = self.library.currentItem()
        if item is None:
            return
        part_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(part_id, bool) or not isinstance(part_id, int):
            return
        spec = self._parts.get(part_id)
        if spec is None:
            return
        origin_x = 0.0
        origin_y = 0.0
        if self._plan.instances:
            last = self._plan.instances[-1]
            gap = (spec.length_mm or 200.0) + self._plan.snap_mm + 10
            origin_x = last.x_mm + gap
            origin_y = last.y_mm
        try:
            self._plan = place_instance(
                self._plan, part_id, spec, origin_x, origin_y, 0.0, self._parts
            )
        except Exception as error:
            self._report(error)
            return
        self._dirty = True
        self._draw(self._plan.instances[-1].id)

    def _add_part(self) -> None:
        dialog = PartDialog(self._translator, self._planner)
        if dialog.exec():
            self._load_parts()
            self._draw(self.canvas.selected_id())

    def _moved_instance(self, item_id: str, x_mm: float, y_mm: float) -> None:
        try:
            self._plan = reposition_instance(self._plan, item_id, x_mm, y_mm, self._parts)
        except Exception as error:
            self._report(error)
            return
        self._dirty = True
        self._draw(item_id)

    def _millimetre_changed(self) -> None:
        if self._filling:
            return
        selected = self._selected()
        if not isinstance(selected, PartInstance):
            return
        try:
            self._plan = reposition_instance(
                self._plan, selected.id, self.x_mm.value(), self.y_mm.value(), self._parts
            )
        except Exception as error:
            self._report(error)
            self._show_selection()
            return
        self._dirty = True
        self._draw(selected.id)

    def _free_rotation_changed(self) -> None:
        if self._filling:
            return
        selected = self._selected()
        if not isinstance(selected, PartInstance):
            return
        self._plan = rotate_instance(self._plan, selected.id, self.rotation_free.value())
        self._dirty = True
        self._draw(selected.id)

    def _grid_changed(self) -> None:
        if self._filling or self._lane_count < 1:
            return
        self._plan = set_plan_grid(
            self._plan,
            enabled=self.grid.isChecked(),
            grid_mm=float(self.grid_size.value()),
            snap_mm=float(self.snap_distance.value()),
        )
        self._dirty = True
        self._draw(self.canvas.selected_id())

    def _report(self, error: Exception) -> None:
        self.status.show_error(describe_error(self._translator, error))
