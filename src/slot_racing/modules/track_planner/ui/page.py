"""Track planner page: pick a track, edit its plan, save or discard."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.config import AppConfig, save_config
from slot_racing.core.config.models import (
    TRACK_PLANNER_BUILD_COLLECTION,
    TRACK_PLANNER_BUILD_UNLIMITED,
)
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
    duplicate_instances,
    empty_plan,
    extend_from_connector,
    move_marker,
    move_piece,
    next_origin,
    place_instance,
    place_start_finish,
    remove_instance,
    remove_marker,
    remove_piece,
    reposition_instance,
    reposition_selection,
    reset_plan,
    rotate_instance,
    rotate_instances_around,
    rotate_piece,
    set_direction,
    set_plan_grid,
    toggle_group,
)
from slot_racing.modules.track_planner.inventory import InventoryReport, analyze_inventory
from slot_racing.modules.track_planner.lane_length import display_lane_lengths, format_length_m
from slot_racing.modules.track_planner.parts import (
    DEFAULT_GRID_MM,
    DEFAULT_SNAP_MM,
    SCALES,
    PartInstance,
    PartRecord,
    PartSpec,
    can_dock,
)
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.canvas import PlanCanvas
from slot_racing.modules.track_planner.ui.collection_dialog import CollectionDialog
from slot_racing.modules.track_planner.ui.flow_layout import FlowLayout, retain_content_width
from slot_racing.modules.track_planner.ui.library_dialog import PartDialog
from slot_racing.modules.track_planner.ui.library_manager import LibraryManager
from slot_racing.modules.track_planner.ui.library_view import PartLibrary
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import configure_page, set_role, set_tone
from slot_racing.uikit.widgets import StatusLabel


class _BodyHost(QWidget):
    """Reports the column minimum as its hint so a wide canvas hint cannot force a scrollbar."""

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        layout = self.layout()
        width = 0 if layout is None else layout.minimumSize().width()
        return QSize(width, 48)


class _RowScroll(QScrollArea):
    """Grows the row to the viewport, and scrolls sideways when the columns do not fit."""

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        host = self.widget()
        if host is None:
            return
        needed = host.minimumSizeHint().width()
        viewport = self.viewport().size()
        width = max(needed, viewport.width())
        height = viewport.height()
        if width > viewport.width():
            bar = self.horizontalScrollBar().sizeHint().height()
            height = max(1, viewport.height() - bar)
        if host.width() != width or host.height() != height:
            host.resize(width, height)


class _PlanStage(QWidget):
    """Hosts the canvas without publishing QGraphicsView's 1440x900 size hint."""

    def __init__(self, canvas: QWidget, warning: QWidget) -> None:
        super().__init__()
        self.setObjectName("planner-stage")
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas, 0, 0)
        layout.addWidget(
            warning,
            0,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
        )

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, 120)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 48)


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
        self._stock: dict[int, int] = {}
        self._build_mode: Literal["unlimited", "collection"] = TRACK_PLANNER_BUILD_UNLIMITED
        if config is not None and config.track_planner_build_mode == TRACK_PLANNER_BUILD_COLLECTION:
            self._build_mode = TRACK_PLANNER_BUILD_COLLECTION
        # One notice per over-capacity episode. It arms again only after the plan fits.
        self._stock_problem_noted = False
        self._plan = empty_plan(TrackId(0))
        self._lane_count = 0
        self._dirty = False
        self._filling = False
        self._parts: dict[int, PartSpec] = {}
        self._records: tuple[PartRecord, ...] = ()
        self._history: list[TrackPlan] = []
        self._future: list[TrackPlan] = []
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
        self.redo_button = QPushButton(translate("planner.redo"))
        self.redo_button.setObjectName("planner-redo")
        set_role(self.redo_button, "ghost")
        self.redo_button.clicked.connect(self.redo)
        self.redo_button.setEnabled(False)
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
        self.mode_unlimited = QPushButton(translate("planner.mode.unlimited"))
        self.mode_unlimited.setObjectName("planner-mode-unlimited")
        self.mode_unlimited.setCheckable(True)
        set_role(self.mode_unlimited, "ghost")
        self.mode_collection = QPushButton(translate("planner.mode.collection"))
        self.mode_collection.setObjectName("planner-mode-collection")
        self.mode_collection.setCheckable(True)
        set_role(self.mode_collection, "ghost")
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self.mode_unlimited)
        self._mode_group.addButton(self.mode_collection)
        self.mode_unlimited.setChecked(self._build_mode == TRACK_PLANNER_BUILD_UNLIMITED)
        self.mode_collection.setChecked(self._build_mode == TRACK_PLANNER_BUILD_COLLECTION)
        self.mode_unlimited.toggled.connect(self._mode_toggled)
        self.mode_collection.toggled.connect(self._mode_toggled)
        self.show_all_parts = QPushButton(translate("planner.stock.show_all"))
        self.show_all_parts.setObjectName("planner-show-all-parts")
        self.show_all_parts.setCheckable(True)
        set_role(self.show_all_parts, "ghost")
        self.show_all_parts.setVisible(self._build_mode == TRACK_PLANNER_BUILD_COLLECTION)
        self.show_all_parts.toggled.connect(self._show_all_toggled)
        self.manage_stock = self._tool(
            "planner-manage-stock", "planner.stock.manage", self._manage_stock
        )
        self.delete_track_button = QPushButton(translate("planner.delete_track"))
        self.delete_track_button.setObjectName("planner-delete-track")
        set_role(self.delete_track_button, "danger")
        self.delete_track_button.clicked.connect(self.delete_current_track)
        self.announce_stock_problem: Callable[[], None] = self._announce_stock_problem
        self.confirm_delete: Callable[[str], bool] = self._confirm_delete
        self.stock_dialog_runner: Callable[[CollectionDialog], int] = lambda dialog: dialog.exec()
        warning_text = QLabel(translate("planner.stock.warning"))
        warning_text.setObjectName("planner-stock-warning-text")
        set_tone(warning_text, "error")
        self.stock_warning = QFrame()
        self.stock_warning.setObjectName("planner-stock-warning")
        set_role(self.stock_warning, "card")
        self.stock_warning.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        warning_layout = QHBoxLayout(self.stock_warning)
        warning_layout.setContentsMargins(12, 8, 12, 8)
        warning_layout.addWidget(warning_text)
        self.stock_warning.hide()
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
        self.grid_size.setValue(DEFAULT_GRID_MM)
        self.snap_distance = QDoubleSpinBox()
        self.snap_distance.setObjectName("planner-snap")
        self.snap_distance.setRange(0, 500)
        self.snap_distance.setValue(DEFAULT_SNAP_MM)
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
        # The view's own minimum is a standalone default. In the page the side
        # columns keep their text width and the canvas takes whatever is left.
        self.canvas.setMinimumSize(0, 0)
        # QGraphicsView's size hint is 1440x900. Ignored keeps that hint from
        # forcing the page to scroll past the window.
        self.canvas.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.canvas.set_color_coding(self._color_coding)
        self.canvas.set_listener(self._moved)
        self.canvas.set_group_listener(self._moved_group)
        self.canvas.set_rotation_listener(self._rotated)
        self.canvas.set_drop_listener(self._dropped)
        self.canvas.set_extend_listener(self._extended)
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
        self.length_title = QLabel(translate("planner.length.title"))
        self.length_title.setObjectName("planner-length-title")
        set_role(self.length_title, "section")
        self.length_body = QLabel("")
        self.length_body.setObjectName("planner-length")
        self.length_body.setWordWrap(True)
        self.length_title.hide()
        self.length_body.hide()
        properties.addRow(self.length_title)
        properties.addRow(self.length_body)
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
        mode_label = QLabel(translate("planner.mode"))
        mode_label.setObjectName("planner-mode-label")
        tools.addWidget(mode_label)
        modes = QVBoxLayout()
        modes.setSpacing(6)
        modes.addWidget(self.mode_unlimited)
        modes.addWidget(self.mode_collection)
        tools.addLayout(modes)
        tools.addWidget(QLabel(translate("planner.filter.scale")))
        tools.addWidget(self.scale_filter)
        tools.addWidget(self.compatible)
        tools.addWidget(self.reset_filter)
        self.library.setMinimumWidth(0)
        self.library.setMinimumHeight(72)
        self.library.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        tools.addWidget(self.library, 1)
        tools.addWidget(self.show_all_parts)
        tools.addWidget(self.manage_stock)
        tools.addWidget(self.add_part)
        tools.addWidget(self.manage_library)
        tools.addWidget(self.delete_track_button)
        tools.addStretch(1)
        library_panel = QWidget()
        library_panel.setObjectName("planner-library-panel")
        library_panel.setLayout(tools)
        labeled = (
            self.mode_unlimited,
            self.mode_collection,
            self.compatible,
            self.reset_filter,
            self.show_all_parts,
            self.manage_stock,
            self.add_part,
            self.manage_library,
            self.delete_track_button,
        )
        for control in labeled:
            control.setMinimumWidth(control.sizeHint().width())
        # The list's own 340 px floor would crush the canvas. Width follows the
        # longest label; cards ellipsize inside that column.
        library_panel.setMinimumWidth(max(260, *(control.minimumWidth() for control in labeled)))
        library_scroll = QScrollArea()
        library_scroll.setObjectName("planner-library-scroll")
        library_scroll.setWidgetResizable(True)
        library_scroll.setFrameShape(QFrame.Shape.NoFrame)
        library_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        library_scroll.setWidget(library_panel)
        gutter = library_scroll.verticalScrollBar().sizeHint().width()
        library_scroll.setMinimumWidth(library_panel.minimumWidth() + gutter)
        library_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        track_label = QLabel(translate("planner.track"))
        track_label.setObjectName("planner-track-label")
        self._toolbar_widgets: tuple[QWidget, ...] = (
            track_label,
            self.track_combo,
            self.lanes,
            self.direction_button,
            self.undo_button,
            self.redo_button,
            self.save_button,
            self.save_as_button,
            self.color_coding,
            self.jump_button,
            self.discard_button,
            self.reset_button,
        )
        self.toolbar = QWidget()
        self.toolbar.setObjectName("planner-toolbar")
        header = FlowLayout(self.toolbar)
        for widget in self._toolbar_widgets:
            header.addWidget(widget)
        self._fit_toolbar()
        warning_holder = QWidget()
        warning_holder.setObjectName("planner-stock-warning-holder")
        warning_holder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        warning_holder.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        holder_layout = QHBoxLayout(warning_holder)
        holder_layout.setContentsMargins(0, 0, 16, 16)
        holder_layout.addWidget(self.stock_warning)
        stage = _PlanStage(self.canvas, warning_holder)
        side = QWidget()
        side.setObjectName("planner-properties")
        side.setLayout(properties)
        properties_scroll = QScrollArea()
        properties_scroll.setObjectName("planner-properties-scroll")
        properties_scroll.setWidgetResizable(True)
        properties_scroll.setFrameShape(QFrame.Shape.NoFrame)
        properties_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        properties_scroll.setWidget(side)
        bar = properties_scroll.verticalScrollBar().sizeHint().width()
        properties_scroll.setMinimumWidth(side.sizeHint().width() + bar)
        properties_scroll.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Ignored)
        body = QHBoxLayout()
        body.addWidget(library_scroll)
        body.addWidget(stage, 1)
        body.addWidget(properties_scroll)
        body_host = _BodyHost()
        body_host.setObjectName("planner-body")
        body_host.setLayout(body)
        body_scroll = _RowScroll()
        body_scroll.setObjectName("planner-body-scroll")
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body_scroll.setWidget(body_host)
        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addWidget(self.toolbar)
        layout.addWidget(body_scroll, 1)
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
        self._future.append(self._plan)
        self._restore(self._history.pop())

    def redo(self) -> None:
        if not self._future:
            return
        self._history.append(self._plan)
        self._restore(self._future.pop())

    def _restore(self, plan: TrackPlan) -> None:
        selected = set(self.canvas.selected_ids())
        self._plan = plan
        self._dirty = True
        self.undo_button.setEnabled(bool(self._history))
        self.redo_button.setEnabled(bool(self._future))
        known = {piece.id for piece in plan.pieces}
        known.update(instance.id for instance in plan.instances)
        known.update(marker.id for marker in plan.markers)
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
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and key == Qt.Key.Key_Z and not shift:
            self.undo()
            return True
        if (ctrl and key == Qt.Key.Key_Y) or (ctrl and shift and key == Qt.Key.Key_Z):
            self.redo()
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
            self._plan = empty_plan(TrackId(0))
            self._lane_count = 0
            self.lanes.setText(self._translator.format("planner.lanes", count=0))
            self._fit_toolbar()
            self._set_enabled(False)
            self._draw()
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
        self._future.clear()
        self.undo_button.setEnabled(False)
        self.redo_button.setEnabled(False)
        self._load_parts()
        self._show_grid()
        self.lanes.setText(self._translator.format("planner.lanes", count=track.lane_count))
        self._fit_toolbar()
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
        self._fit_toolbar()
        self.jump_button.setEnabled(
            any(instance.start_straight for instance in self._plan.instances)
        )
        one = isinstance(selected, PartInstance) and len(self.canvas.selected_ids()) == 1
        self.compatible.setEnabled(one)
        self._apply_filters()
        self._show_lengths()

    def _show_lengths(self) -> None:
        """Refresh the side-panel lengths from the current plan. The canvas is not involved."""
        rows = display_lane_lengths(self._plan.instances, self._parts)
        if not rows:
            self.length_title.hide()
            self.length_body.hide()
            self.length_body.setText("")
            return
        lines = [
            self._translator.format(
                "planner.length.lane",
                lane=row.lane,
                length=format_length_m(row.length_mm),
            )
            for row in rows
        ]
        self.length_body.setText("\n".join(lines))
        self.length_title.show()
        self.length_body.show()

    def _draw(self, selected: str | set[str] | list[str] | None = None) -> None:
        chosen = set(selected) if isinstance(selected, list) else selected
        report = self._inventory()
        excess = report.excess_ids() if self._collection_mode() else frozenset()
        self.canvas.show_plan(
            self._plan, max(self._lane_count, 1), chosen, self._parts, excess=excess
        )
        self._sync_stock_warning(report)
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
            self.manage_stock,
            self.show_all_parts,
            self.delete_track_button,
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
        self._stock = self._planner.stock_quantities()
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
            plan = reposition_selection(self._plan, raw, anchor, self._parts)
        except Exception as error:
            self._report(error)
            self._draw(set(raw))
            return
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

    def _extended(self, instance_id: str, connector_index: int, direction: str) -> None:
        try:
            updated = extend_from_connector(
                self._plan, instance_id, connector_index, direction, self._parts
            )
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

    def _fit_toolbar(self) -> None:
        for widget in self._toolbar_widgets:
            retain_content_width(widget)
        self.toolbar.updateGeometry()

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
        report = self._inventory()
        if self._collection_mode() and not self.show_all_parts.isChecked():
            records = [record for record in records if report.balance(record.id).available >= 1]
        remaining = None
        if self._collection_mode():
            remaining = {record.id: self._remaining_text(report, record.id) for record in records}
        current = self.library.currentItem()
        current_id = None if current is None else current.data(Qt.ItemDataRole.UserRole)
        self.library.set_records(records, remaining)
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
        self._future.clear()
        self._plan = plan
        self._dirty = True
        self.undo_button.setEnabled(True)
        self.redo_button.setEnabled(False)
        self._draw(selected)

    def delete_current_track(self) -> None:
        """Delete the open track after confirmation. This is not a canvas undo step."""
        track_id = self._current_track()
        if track_id is None or self._lane_count < 1:
            return
        track = self._tracks.get_track(track_id)
        name = "" if track is None else track.name
        text = self._translator.format("planner.delete_track.confirm", name=name)
        if not self.confirm_delete(text):
            return
        try:
            self._planner.delete_track(track_id)
        except Exception as error:
            self._report(error)
            return
        self._dirty = False
        self._history.clear()
        self._future.clear()
        self.undo_button.setEnabled(False)
        self.redo_button.setEnabled(False)
        self._refresh_tracks()
        self.status.show_info(self._translator.translate("planner.delete_track.done"))

    def _confirm_delete(self, text: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setObjectName("planner-delete-track-dialog")
        box.setWindowTitle(self._translator.translate("planner.delete_track.title"))
        box.setText(text)
        accept = box.addButton(
            self._translator.translate("planner.delete_track.accept"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        cancel = box.addButton(
            self._translator.translate("planner.delete_track.cancel"),
            QMessageBox.ButtonRole.RejectRole,
        )
        accept.setObjectName("planner-delete-track-accept")
        cancel.setObjectName("planner-delete-track-cancel")
        set_role(accept, "danger")
        box.setDefaultButton(cancel)
        _keep_message_visible(box)
        box.exec()
        return box.clickedButton() is accept

    def _announce_stock_problem(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setObjectName("planner-stock-notice")
        box.setWindowTitle(self._translator.translate("planner.stock.notice.title"))
        box.setText(self._translator.translate("planner.stock.notice"))
        box.setInformativeText(self._translator.translate("planner.stock.notice.detail"))
        _keep_message_visible(box)
        box.exec()

    def _manage_stock(self) -> None:
        dialog = CollectionDialog(self._translator, self._planner, self)
        self.stock_dialog_runner(dialog)
        dialog.close()
        self._stock = self._planner.stock_quantities()
        self._draw()

    def _mode_toggled(self, checked: bool) -> None:
        if self._filling or not checked:
            return
        mode: Literal["unlimited", "collection"] = (
            TRACK_PLANNER_BUILD_COLLECTION
            if self.mode_collection.isChecked()
            else TRACK_PLANNER_BUILD_UNLIMITED
        )
        if mode == self._build_mode:
            return
        self._build_mode = mode
        self.show_all_parts.setVisible(mode == TRACK_PLANNER_BUILD_COLLECTION)
        if mode != TRACK_PLANNER_BUILD_COLLECTION and self.show_all_parts.isChecked():
            self._filling = True
            self.show_all_parts.setChecked(False)
            self._filling = False
        self._persist_mode()
        self._draw()

    def _show_all_toggled(self, _checked: bool) -> None:
        if self._filling:
            return
        self._apply_filters()

    def _persist_mode(self) -> None:
        if self._config is None:
            return
        self._config.track_planner_build_mode = self._build_mode
        if self._config_path is not None:
            save_config(self._config, self._config_path)

    def _collection_mode(self) -> bool:
        return self._build_mode == TRACK_PLANNER_BUILD_COLLECTION

    def _inventory(self) -> InventoryReport:
        return analyze_inventory(self._plan.instances, self._stock)

    def _remaining_text(self, report: InventoryReport, part_id: int) -> str:
        available = report.balance(part_id).available
        if available < 0:
            return self._translator.format("planner.stock.short", count=-available)
        return self._translator.format("planner.stock.remaining", count=available)

    def _sync_stock_warning(self, report: InventoryReport) -> None:
        active = self._collection_mode() and report.over_capacity()
        self.stock_warning.setVisible(active)
        if not report.over_capacity():
            self._stock_problem_noted = False
            return
        if active and not self._stock_problem_noted:
            self._stock_problem_noted = True
            self.announce_stock_problem()

    def _report(self, error: Exception) -> None:
        self.status.show_error(describe_error(self._translator, error))


def _keep_message_visible(box: QMessageBox) -> None:
    """Widen the message and its buttons. Stylesheet padding otherwise clips the last letters."""
    for name in ("qt_msgbox_label", "qt_msgbox_informativelabel"):
        label = box.findChild(QLabel, name)
        if label is None or not label.text():
            continue
        label.ensurePolished()
        widest = max(
            label.fontMetrics().horizontalAdvance(line) for line in label.text().splitlines()
        )
        label.setMinimumWidth(widest + 12)
    for button in box.buttons():
        if not isinstance(button, QPushButton) or not button.text():
            continue
        button.ensurePolished()
        advance = button.fontMetrics().horizontalAdvance(button.text())
        button.setMinimumWidth(max(button.sizeHint().width(), advance + 30) + 12)
