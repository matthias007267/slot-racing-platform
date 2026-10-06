"""Track planner page: pick a track, edit its plan, save or discard."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.config import AppConfig, save_config
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
    dock_copy,
    duplicate_instances,
    empty_plan,
    move_instance,
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
    rotate_instances_around,
    rotate_piece,
    set_direction,
    set_plan_grid,
    set_start_straight,
    toggle_group,
)
from slot_racing.modules.track_planner.parts import (
    SCALES,
    STRAIGHT,
    PartInstance,
    PartRecord,
    PartSpec,
    can_dock,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.canvas import PlanCanvas
from slot_racing.modules.track_planner.ui.library_dialog import PartDialog
from slot_racing.modules.track_planner.ui.library_manager import LibraryManager
from slot_racing.modules.track_planner.ui.library_view import PartLibrary
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import configure_page, set_role
from slot_racing.uikit.widgets import StatusLabel


class PlannerPage(QWidget):
    def __init__(
        self,
        translator: Translator,
        tracks: TrackCatalog,
        planner: TrackPlannerService,
        config: AppConfig | None = None,
        config_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._translator = translator
        self._tracks = tracks
        self._planner = planner
        self._config = config
        self._config_path = config_path
        self._color_coding = False if config is None else config.track_planner_color_coding
        self._plan = empty_plan(TrackId(0))
        self._lane_count = 0
        self._dirty = False
        self._filling = False
        self._parts: dict[int, PartSpec] = {}
        self._records: tuple[PartRecord, ...] = ()
        self._history: list[TrackPlan] = []
        self._clipboard: tuple[PartInstance, ...] = ()
        self._keys_attached = False
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
        self.undo_button = QPushButton(translate("planner.undo"))
        self.undo_button.setObjectName("planner-undo")
        set_role(self.undo_button, "ghost")
        self.undo_button.clicked.connect(self.undo)
        self.undo_button.setEnabled(False)
        self.save_button = QPushButton(translate("planner.save"))
        self.save_button.setObjectName("planner-save")
        self.save_button.clicked.connect(self.save)
        self.save_as_button = QPushButton(translate("planner.save_as"))
        self.save_as_button.setObjectName("planner-save-as")
        set_role(self.save_as_button, "ghost")
        self.save_as_button.clicked.connect(self.save_as_new)
        self.jump_button = QPushButton(translate("planner.jump_start"))
        self.jump_button.setObjectName("planner-jump-start")
        set_role(self.jump_button, "ghost")
        self.jump_button.clicked.connect(self.jump_to_start)
        self.jump_button.setEnabled(False)
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
        self.library = PartLibrary()
        self.library.set_color_coding(self._color_coding)
        self.color_coding = QPushButton(translate("planner.color_coding"))
        self.color_coding.setObjectName("planner-color-coding")
        self.color_coding.setCheckable(True)
        set_role(self.color_coding, "ghost")
        self.color_coding.setChecked(self._color_coding)
        self.color_coding.toggled.connect(self._color_coding_changed)
        self.place_part = self._tool("planner-place", "planner.library.place", self._place_part)
        self.add_part = self._tool("planner-add-part", "planner.library.add", self._add_part)
        self.manage_library = self._tool(
            "planner-manage-library", "planner.library.manage", self._manage_library
        )
        self.scale_filter = QComboBox()
        self.scale_filter.setObjectName("planner-filter-scale")
        self.scale_filter.addItem(translate("planner.filter.all"), None)
        for scale in SCALES:
            self.scale_filter.addItem(scale, scale)
        self.scale_filter.currentIndexChanged.connect(self._filters_changed)
        self.compatible = QCheckBox(translate("planner.filter.compatible"))
        self.compatible.setObjectName("planner-filter-compatible")
        self.compatible.setEnabled(False)
        self.compatible.toggled.connect(self._filters_changed)
        self.reset_filter = self._tool(
            "planner-filter-reset", "planner.filter.reset", self._reset_filters
        )
        self.grid = QCheckBox(translate("planner.field.grid"))
        self.grid.setObjectName("planner-grid")
        self.grid.setChecked(False)
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
        self.canvas.set_color_coding(self._color_coding)
        self.canvas.set_listener(self._moved)
        self.canvas.set_group_listener(self._moved_group)
        self.canvas.set_rotation_listener(self._rotated)
        self.canvas.set_drop_listener(self._dropped)
        self.canvas.set_dock_listener(self._docked)
        self.canvas.scene().selectionChanged.connect(self._show_selection)
        self.start_straight = QCheckBox(translate("planner.start_straight"))
        self.start_straight.setObjectName("planner-start-straight")
        self.start_straight.setEnabled(False)
        self.start_straight.toggled.connect(self._start_straight_changed)
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
        properties.addRow(self.start_straight)
        properties.addRow(translate("planner.field.x"), self.x_spin)
        properties.addRow(translate("planner.field.y"), self.y_spin)
        properties.addRow(translate("planner.field.rotation"), self.rotation)
        properties.addRow(translate("planner.field.rotation_free"), self.rotation_free)
        properties.addRow(translate("planner.field.x"), self.x_mm)
        properties.addRow(translate("planner.field.y"), self.y_mm)
        properties.addRow(self.grid)
        properties.addRow(translate("planner.field.grid_size"), self.grid_size)
        properties.addRow(translate("planner.field.snap"), self.snap_distance)
        for retired in (
            self.add_horizontal,
            self.add_vertical,
            self.add_curve,
            self.add_start,
            self.delete_button,
            self.place_part,
        ):
            retired.hide()
        tools = QVBoxLayout()
        library_title = QLabel(translate("planner.library"))
        set_role(library_title, "section")
        tools.addWidget(library_title)
        tools.addWidget(QLabel(translate("planner.filter.scale")))
        tools.addWidget(self.scale_filter)
        tools.addWidget(self.compatible)
        tools.addWidget(self.reset_filter)
        tools.addWidget(self.library, 1)
        tools.addWidget(self.add_part)
        tools.addWidget(self.manage_library)
        tools.addStretch(1)
        library_panel = QWidget()
        library_panel.setObjectName("planner-library-panel")
        library_panel.setMinimumWidth(360)
        library_panel.setLayout(tools)
        header = QHBoxLayout()
        header.addWidget(QLabel(translate("planner.track")))
        header.addWidget(self.track_combo, 1)
        header.addWidget(self.lanes)
        header.addWidget(self.direction_button)
        header.addWidget(self.undo_button)
        header.addWidget(self.save_button)
        header.addWidget(self.save_as_button)
        header.addWidget(self.color_coding)
        header.addWidget(self.jump_button)
        header.addWidget(self.discard_button)
        header.addWidget(self.reset_button)
        body = QHBoxLayout()
        body.addWidget(library_panel)
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

    def save_as_new(self) -> None:
        """Store a copy under a new track. The open plan is not written."""
        if self._lane_count < 1:
            return
        name = self._ask_track_name()
        if name is None:
            return
        try:
            copied = self._planner.save_as_new(self._plan, name)
        except Exception as error:
            self._report(error)
            return
        self._filling = True
        self.track_combo.addItem(name.strip(), copied.track_id)
        index = self.track_combo.findData(self._plan.track_id)
        if index >= 0:
            self.track_combo.setCurrentIndex(index)
        self._filling = False
        self.status.show_info(self._translator.translate("planner.saved_as"))

    def jump_to_start(self) -> None:
        start = next(
            (instance for instance in self._plan.instances if instance.start_straight), None
        )
        if start is None:
            self.status.show_info(self._translator.translate("planner.start_straight.missing"))
            return
        self.canvas.center_on_mm(start.x_mm, start.y_mm)

    def discard(self) -> None:
        track_id = self._current_track()
        if track_id is None:
            return
        self._open(track_id)
        self.status.show_info(self._translator.translate("planner.discarded"))

    def reset(self) -> None:
        if self._lane_count < 1:
            return
        self._commit(reset_plan(self._plan))
        self.status.show_info(self._translator.translate("planner.reset_done"))

    def delete_selected(self) -> None:
        selected = set(self.canvas.selected_ids())
        if not selected:
            return
        plan = self._plan
        for piece in self._plan.pieces:
            if piece.id in selected:
                plan = remove_piece(plan, piece.id)
        for instance in self._plan.instances:
            if instance.id in selected:
                plan = remove_instance(plan, instance.id)
        for marker in plan.markers:
            if marker.id in selected:
                plan = remove_marker(plan, marker.id)
        self._commit(plan)

    def undo(self) -> None:
        if not self._history:
            return
        selected = set(self.canvas.selected_ids())
        self._plan = self._history.pop()
        self._dirty = True
        self.undo_button.setEnabled(bool(self._history))
        known = {piece.id for piece in self._plan.pieces}
        known.update(instance.id for instance in self._plan.instances)
        known.update(marker.id for marker in self._plan.markers)
        self._draw(selected & known)

    def copy_selection(self) -> None:
        selected = set(self.canvas.selected_ids())
        self._clipboard = tuple(
            instance for instance in self._plan.instances if instance.id in selected
        )

    def paste_selection(self) -> None:
        if not self._clipboard or self._lane_count < 1:
            return
        shift = max(self._plan.grid_mm * 4, 80.0)
        plan, created = duplicate_instances(
            self._plan, [instance.id for instance in self._clipboard], shift, shift
        )
        if not created:
            return
        self._clipboard = tuple(
            instance for instance in plan.instances if instance.id in set(created)
        )
        self._commit(plan, set(created))

    def showEvent(self, event: object) -> None:  # noqa: N802
        super().showEvent(event)  # type: ignore[arg-type]
        self._attach_keys()
        if not self._dirty:
            self._refresh_tracks()

    def hideEvent(self, event: object) -> None:  # noqa: N802
        self._detach_keys()
        super().hideEvent(event)  # type: ignore[arg-type]

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.Type.KeyPress
            and self.isVisible()
            and isinstance(event, QKeyEvent)
            and isinstance(watched, QWidget)
            and (watched is self or self.isAncestorOf(watched))
            and self._handle_planner_key(event)
        ):
            return True
        return super().eventFilter(watched, event)

    def _attach_keys(self) -> None:
        app = QApplication.instance()
        if app is None or self._keys_attached:
            return
        app.installEventFilter(self)
        self._keys_attached = True

    def _detach_keys(self) -> None:
        app = QApplication.instance()
        if app is not None and self._keys_attached:
            app.removeEventFilter(self)
        self._keys_attached = False

    def _handle_planner_key(self, event: QKeyEvent) -> bool:
        """Delete, undo, copy and paste while the planner is the visible page."""
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        key = event.key()
        if key == Qt.Key.Key_Delete and not ctrl:
            self.delete_selected()
            return True
        if ctrl and key == Qt.Key.Key_Z:
            self.undo()
            return True
        if ctrl and key == Qt.Key.Key_C:
            self.copy_selection()
            return True
        if ctrl and key == Qt.Key.Key_V:
            self.paste_selection()
            return True
        if ctrl and key == Qt.Key.Key_G:
            self._toggle_group()
            return True
        return False

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
        self._history.clear()
        self.undo_button.setEnabled(False)
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
        updated = add_piece(self._plan, piece_type, origin[0], origin[1])
        created = updated.pieces[-1].id if updated.pieces else None
        self._commit(updated, created)

    def _add_start(self) -> None:
        if self._lane_count < 1:
            return
        current = self._plan.start_finish()
        if current is None:
            self._commit(place_start_finish(self._plan, 0, 0))
        selected = self._plan.start_finish()
        self._draw(None if selected is None else selected.id)

    def _toggle_direction(self) -> None:
        if self._lane_count < 1:
            return
        direction = COUNTERCLOCKWISE if self._plan.direction == CLOCKWISE else CLOCKWISE
        self._commit(set_direction(self._plan, direction), self.canvas.selected_ids())

    def _moved(self, item_id: str, x: int, y: int) -> None:
        try:
            if any(piece.id == item_id for piece in self._plan.pieces):
                updated = move_piece(self._plan, item_id, x, y)
            else:
                updated = move_marker(self._plan, item_id, x, y)
        except ValidationError as error:
            self._report(error)
            return
        self._commit(updated, item_id)

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
                updated = move_piece(self._plan, selected.id, x, y)
            else:
                updated = move_marker(self._plan, selected.id, x, y)
        except ValidationError as error:
            self._plan = previous
            self._show_selection()
            self._report(error)
            return
        self._commit(updated, selected.id)

    def _rotation_changed(self) -> None:
        if self._filling:
            return
        selected = self._selected()
        if not isinstance(selected, Piece) or selected.piece_type != CURVE_90:
            return
        angle = self.rotation.currentData()
        if not isinstance(angle, int):
            return
        self._commit(rotate_piece(self._plan, selected.id, angle), selected.id)

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
            self.start_straight.setEnabled(False)
            self.start_straight.setChecked(False)
        elif len(self.canvas.selected_ids()) > 1:
            self.selection_label.setText(
                self._translator.format(
                    "planner.selection.many", count=len(self.canvas.selected_ids())
                )
            )
            self.x_spin.setEnabled(False)
            self.y_spin.setEnabled(False)
            self.rotation.setEnabled(False)
            self.rotation_free.setEnabled(False)
            self.x_mm.setEnabled(False)
            self.y_mm.setEnabled(False)
            self.start_straight.setEnabled(False)
            self.start_straight.setChecked(False)
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
            spec = self._parts.get(selected.part_id)
            straight = spec is not None and spec.category == STRAIGHT
            self.start_straight.setEnabled(straight)
            self.start_straight.setChecked(straight and selected.start_straight)
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
            self.start_straight.setEnabled(False)
            self.start_straight.setChecked(False)
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
        self.jump_button.setEnabled(
            any(instance.start_straight for instance in self._plan.instances)
        )
        one = isinstance(selected, PartInstance) and len(self.canvas.selected_ids()) == 1
        self.compatible.setEnabled(one)
        self._apply_filters()

    def _draw(self, selected: str | set[str] | list[str] | None = None) -> None:
        chosen = set(selected) if isinstance(selected, list) else selected
        self.canvas.show_plan(self._plan, max(self._lane_count, 1), chosen, self._parts)
        self._show_selection()

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.direction_button,
            self.save_button,
            self.save_as_button,
            self.jump_button,
            self.discard_button,
            self.reset_button,
            self.add_horizontal,
            self.add_vertical,
            self.add_curve,
            self.add_start,
            self.delete_button,
            self.place_part,
            self.add_part,
            self.manage_library,
            self.scale_filter,
            self.reset_filter,
            self.canvas,
        ):
            widget.setEnabled(enabled)
        if enabled:
            self.jump_button.setEnabled(
                any(instance.start_straight for instance in self._plan.instances)
            )

    def _load_parts(self) -> None:
        records = self._planner.list_parts()
        self._records = records
        self._parts = {record.id: record.spec for record in records}
        self._apply_filters()

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
            updated = place_instance(
                self._plan, part_id, spec, origin_x, origin_y, 0.0, self._parts
            )
        except Exception as error:
            self._report(error)
            return
        created = updated.instances[-1].id if updated.instances else None
        self._commit(updated, created)

    def _add_part(self) -> None:
        dialog = PartDialog(self._translator, self._planner)
        if dialog.exec():
            self._load_parts()
            self._draw(self.canvas.selected_id())

    def _manage_library(self) -> None:
        used = {instance.part_id for instance in self._plan.instances}
        dialog = LibraryManager(
            self._translator, self._planner, used, color_coding=self.color_coding.isChecked()
        )
        dialog.exec()
        self._load_parts()
        self._draw(self.canvas.selected_ids())

    def _dropped(self, part_id: int, x_mm: float, y_mm: float) -> None:
        if self._lane_count < 1:
            return
        spec = self._parts.get(part_id)
        if spec is None:
            return
        try:
            updated = place_instance(self._plan, part_id, spec, x_mm, y_mm, 0.0, self._parts)
        except Exception as error:
            self._report(error)
            return
        created = updated.instances[-1].id if updated.instances else None
        self._commit(updated, created)

    def _moved_group(self, anchor: str, moves: list[tuple[str, float, float]]) -> None:
        raw = {item_id: (x_mm, y_mm) for item_id, x_mm, y_mm in moves}
        if anchor not in raw:
            return
        try:
            snapped = reposition_instance(
                self._plan, anchor, raw[anchor][0], raw[anchor][1], self._parts
            )
        except Exception as error:
            self._report(error)
            self._draw(set(raw))
            return
        anchor_pose = next(instance for instance in snapped.instances if instance.id == anchor)
        dx = anchor_pose.x_mm - raw[anchor][0]
        dy = anchor_pose.y_mm - raw[anchor][1]
        plan = snapped
        for item_id, (x_mm, y_mm) in raw.items():
            if item_id == anchor:
                continue
            plan = move_instance(plan, item_id, x_mm + dx, y_mm + dy)
        self._commit(plan, set(raw))

    def _rotated(self, ids: list[str], center_x: float, center_y: float, delta: float) -> None:
        self._commit(
            rotate_instances_around(self._plan, ids, center_x, center_y, delta),
            set(ids),
        )

    def _millimetre_changed(self) -> None:
        if self._filling:
            return
        selected = self._selected()
        if not isinstance(selected, PartInstance):
            return
        try:
            updated = reposition_instance(
                self._plan, selected.id, self.x_mm.value(), self.y_mm.value(), self._parts
            )
        except Exception as error:
            self._report(error)
            self._show_selection()
            return
        self._commit(updated, selected.id)

    def _free_rotation_changed(self) -> None:
        if self._filling:
            return
        selected = self._selected()
        if not isinstance(selected, PartInstance):
            return
        self._commit(
            rotate_instance(self._plan, selected.id, self.rotation_free.value()), selected.id
        )

    def _docked(self, instance_id: str, connector_index: int) -> None:
        try:
            updated = dock_copy(self._plan, instance_id, connector_index, self._parts)
        except Exception as error:
            self._report(error)
            return
        created = updated.instances[-1].id if updated.instances else None
        self._commit(updated, created)

    def _color_coding_changed(self, enabled: bool) -> None:
        self._color_coding = enabled
        if self._config is not None:
            self._config.track_planner_color_coding = enabled
            if self._config_path is not None:
                save_config(self._config, self._config_path)
        self.canvas.set_color_coding(enabled)
        self.library.set_color_coding(enabled)

    def _start_straight_changed(self) -> None:
        if self._filling:
            return
        selected = self._selected()
        if not isinstance(selected, PartInstance):
            return
        try:
            updated = set_start_straight(
                self._plan, selected.id, self.start_straight.isChecked(), self._parts
            )
        except Exception as error:
            self._report(error)
            self._show_selection()
            return
        self._commit(updated, selected.id)

    def _toggle_group(self) -> None:
        ids = [
            item_id
            for item_id in self.canvas.selected_ids()
            if any(instance.id == item_id for instance in self._plan.instances)
        ]
        self._commit(toggle_group(self._plan, ids), ids)

    def _filters_changed(self) -> None:
        if self._filling:
            return
        self._apply_filters()

    def _reset_filters(self) -> None:
        self._filling = True
        self.scale_filter.setCurrentIndex(0)
        self.compatible.setChecked(False)
        self._filling = False
        self._apply_filters()

    def _apply_filters(self) -> None:
        records = list(self._records)
        scale = self.scale_filter.currentData()
        if isinstance(scale, str):
            records = [record for record in records if record.spec.scale == scale]
        selected = self._selected()
        if (
            self.compatible.isEnabled()
            and self.compatible.isChecked()
            and isinstance(selected, PartInstance)
            and len(self.canvas.selected_ids()) == 1
        ):
            spec = self._parts.get(selected.part_id)
            if spec is None:
                records = []
            else:
                placed = [
                    (instance, self._parts[instance.part_id])
                    for instance in self._plan.instances
                    if instance.part_id in self._parts
                ]
                records = [
                    record for record in records if can_dock(record.spec, selected, spec, placed)
                ]
        current = self.library.currentItem()
        current_id = None if current is None else current.data(Qt.ItemDataRole.UserRole)
        self.library.set_records(records, self._translator)
        if not self.library.count():
            return
        row = 0
        if isinstance(current_id, int):
            for index in range(self.library.count()):
                item = self.library.item(index)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == current_id:
                    row = index
                    break
        self.library.setCurrentRow(row)

    def _ask_track_name(self) -> str | None:
        text, accepted = QInputDialog.getText(
            self,
            self._translator.translate("planner.save_as.title"),
            self._translator.translate("planner.save_as.prompt"),
        )
        if not accepted:
            return None
        return text

    def _grid_changed(self) -> None:
        if self._filling or self._lane_count < 1:
            return
        self._commit(
            set_plan_grid(
                self._plan,
                enabled=self.grid.isChecked(),
                grid_mm=float(self.grid_size.value()),
                snap_mm=float(self.snap_distance.value()),
            ),
            self.canvas.selected_ids(),
        )

    def _commit(self, plan: TrackPlan, selected: str | set[str] | list[str] | None = None) -> None:
        if plan == self._plan:
            if selected is not None:
                self._draw(selected)
            return
        self._history.append(self._plan)
        if len(self._history) > 50:
            self._history.pop(0)
        self._plan = plan
        self._dirty = True
        self.undo_button.setEnabled(True)
        self._draw(selected)

    def _report(self, error: Exception) -> None:
        self.status.show_error(describe_error(self._translator, error))
