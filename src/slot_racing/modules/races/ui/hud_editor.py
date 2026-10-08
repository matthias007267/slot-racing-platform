"""Settings editor for the live HUD. The preview is the same surface as a race."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import (
    FIELD_IDS,
    SCALE_MAX,
    SCALE_MIN,
    SCALE_STEP,
    SHARE_MAX,
    SHARE_MIN,
    FieldStyle,
    HudConfiguration,
    HudConfigurationStore,
    HudLayout,
    HudWidgetConfig,
    LightFrame,
    add_layout,
    clamp_widget,
    delete_layout,
    factory_layout,
    mark_default,
    replace_layout,
    snap_alignment,
    snap_scale,
    snap_share,
    to_pixels,
)
from slot_racing.modules.races.runner import LiveRow, RaceSnapshot
from slot_racing.modules.races.ui.live_stage import LiveHudStage
from slot_racing.modules.races.ui.start_cue import StartCue, StartCueStep
from slot_racing.modules.races.ui.start_lights import StartLightWidget
from slot_racing.uikit.theme import SPACE, configure_page

_HANDLE = 14
_PREVIEW_STATUSES: tuple[tuple[str, str], ...] = (
    ("running", "race.status.running"),
    ("paused", "race.status.paused"),
    ("finished", "race.status.finished"),
    ("aborted", "race.status.aborted"),
)
_DRIVERS = ("Zoe", "Anna", "Ben", "Mia")
_VEHICLES = ("Porsche 911", "Ferrari 488", "Audi R8", "BMW M4")


def _clamped_frame(frame: LightFrame) -> LightFrame:
    clamped = clamp_widget(
        HudWidgetConfig("start_lights", frame.visible, frame.x, frame.y, frame.width, frame.height)
    )
    return LightFrame(
        visible=frame.visible,
        x=clamped.x,
        y=clamped.y,
        width=clamped.width,
        height=clamped.height,
    )


def _fresh_id(document: HudConfiguration) -> str:
    taken = {item.id for item in document.layouts}
    number = 2
    while f"layout-{number}" in taken:
        number += 1
    return f"layout-{number}"


def _sample(status_key: str, lanes: int) -> tuple[RaceSnapshot, str]:
    aborted = status_key == "aborted"
    status = {
        "running": RaceStatus.RUNNING,
        "paused": RaceStatus.PAUSED,
        "finished": RaceStatus.FINISHED,
        "aborted": RaceStatus.FINISHED,
    }[status_key]
    message = {
        "running": "hud.message.started",
        "paused": "hud.message.paused",
        "finished": "hud.message.finished",
        "aborted": "hud.message.aborted",
    }[status_key]
    rows: list[LiveRow] = []
    for lane in range(1, lanes + 1):
        rows.append(
            LiveRow(
                position=1 if lane == 2 else lane,
                lane=lane,
                driver_label=_DRIVERS[lane - 1],
                vehicle_label=_VEHICLES[lane - 1],
                start_number=lane + 6,
                current_lap=3,
                laps_completed=2,
                last_lap_ns=4_200_000_000 + lane * 80_000_000,
                total_time_ns=18_400_000_000,
                best_lap_ns=4_050_000_000,
                finished=status is RaceStatus.FINISHED and not aborted,
                lap_times_ns=(4_200_000_000,),
            )
        )
    if lanes >= 2:
        rows[0] = replace(rows[0], position=2)
    snapshot = RaceSnapshot(
        race_id=RaceId(1),
        name="Vorschau",
        track_name="Heimbahn",
        timing_provider="simulation",
        status=status,
        aborted=aborted,
        laps=8,
        elapsed_ns=95_000_000_000,
        rows=tuple(rows),
        source_errors=(),
    )
    return snapshot, message


class LightHandle(QWidget):
    """Editor chrome for the start gantry. The gantry itself does not take mouse clicks."""

    moved = Signal(int, int, int, int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("hud-light-handle")
        self._drag: tuple[str, QPoint, QRect] | None = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def is_dragging(self) -> bool:
        return self._drag is not None

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setPen(QPen(QColor(255, 196, 64), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        grip = QRect(self.width() - _HANDLE, self.height() - _HANDLE, _HANDLE - 1, _HANDLE - 1)
        painter.setBrush(QColor(255, 196, 64))
        painter.drawRect(grip)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        mode = "resize" if self._on_grip(pos) else "move"
        self._drag = (mode, event.globalPosition().toPoint(), QRect(self.geometry()))
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        mode, start, origin = self._drag
        delta = event.globalPosition().toPoint() - start
        rect = QRect(origin)
        if mode == "move":
            rect.translate(delta)
        else:
            rect.setWidth(max(_HANDLE * 2, origin.width() + delta.x()))
            rect.setHeight(max(_HANDLE * 2, origin.height() + delta.y()))
        self.setGeometry(rect)
        self.moved.emit(rect.x(), rect.y(), rect.width(), rect.height())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag is not None:
            rect = self.geometry()
            self._drag = None
            self.moved.emit(rect.x(), rect.y(), rect.width(), rect.height())
        super().mouseReleaseEvent(event)

    def _on_grip(self, pos: QPoint) -> bool:
        return pos.x() >= self.width() - _HANDLE and pos.y() >= self.height() - _HANDLE


class HudPreviewHost(QWidget):
    """Live HUD plus the light overlay. The gantry is not part of the stage layout."""

    def __init__(
        self,
        translator: Translator,
        on_resize: Callable[[], None],
        on_drag: Callable[[int, int, int, int], None],
    ) -> None:
        super().__init__()
        self.setObjectName("hud-preview")
        self.setMinimumSize(640, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.stage = LiveHudStage(translator, object_name="hud-live-preview")
        self.lights = StartLightWidget(self)
        self.lights.use_as_overlay()
        self.handle = LightHandle(self)
        self.handle.moved.connect(on_drag)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stage)
        self._on_resize = on_resize

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._on_resize()


class HudEditor(QWidget):
    """Configure the live HUD: type, shares, visibility and the start-light overlay."""

    def __init__(self, translator: Translator, store: HudConfigurationStore) -> None:
        super().__init__()
        self.setObjectName("race-hud-editor")
        self._translator = translator
        self._store = store
        self._config = store.load()
        self._filling = False
        self._cue: StartCue | None = None
        self._sequence_started = False
        self._preview_status = "running"
        self._preview_lanes = 2
        self.preview = HudPreviewHost(translator, self._place_lights, self._on_handle)
        self.stage = self.preview.stage
        self.lights = self.preview.lights
        self.handle = self.preview.handle
        self._build()
        self.stage.apply_layout(self.selected_layout())
        self._fill_form()
        self._show_sample()
        self._place_lights()
        self.destroyed.connect(lambda *_args: self._stop_cue(restore=False))

    def configuration(self) -> HudConfiguration:
        return self._config

    def selected_layout(self) -> HudLayout:
        return self._config.selected()

    @property
    def light_preview_started(self) -> bool:
        """True once the preview sequence has reached the lights-out start signal."""
        return self._sequence_started

    def select_layout(self, layout_id: str) -> None:
        if self._config.layout(layout_id) is None:
            return
        self._stop_cue()
        self._config = replace(self._config, selected_id=layout_id)
        self._apply_preview()
        self._fill_form()

    def add_layout(self, name: str | None = None) -> str:
        current = self.selected_layout()
        layout_id = _fresh_id(self._config)
        number = layout_id.removeprefix("layout-")
        title = name.strip() if isinstance(name, str) and name.strip() else f"Layout {number}"
        created = replace(current, id=layout_id, name=title, builtin=False)
        self._config = add_layout(self._config, created)
        self._apply_preview()
        self._fill_form()
        return layout_id

    def delete_selected(self) -> None:
        current = self.selected_layout()
        if current.builtin:
            return
        self._stop_cue()
        self._config = self._store.save(delete_layout(self._config, current.id))
        self._apply_preview()
        self._fill_form()

    def set_font_scale(self, value: int) -> None:
        self._commit(replace(self.selected_layout(), font_scale=snap_scale(value)))

    def set_alignment(self, alignment: str) -> None:
        self._commit(replace(self.selected_layout(), alignment=snap_alignment(alignment)))

    def set_field_visible(self, field_id: str, visible: bool) -> None:
        style = self.selected_layout().field(field_id)
        self._commit(self._with_field(field_id, replace(style, visible=visible)))

    def set_field_scale(self, field_id: str, value: int) -> None:
        style = self.selected_layout().field(field_id)
        self._commit(self._with_field(field_id, replace(style, scale=snap_scale(value))))

    def set_share(self, part: str, value: int) -> None:
        layout = self.selected_layout()
        shares = {
            "clock": replace(layout, clock_share=snap_share(value, layout.clock_share)),
            "status": replace(layout, status_share=snap_share(value, layout.status_share)),
            "lanes": replace(layout, lanes_share=snap_share(value, layout.lanes_share)),
            "ranking": replace(layout, ranking_share=snap_share(value, layout.ranking_share)),
        }
        updated = shares.get(part)
        if updated is not None:
            self._commit(updated)

    def set_light_frame(self, frame: LightFrame) -> None:
        self._commit(replace(self.selected_layout(), lights=_clamped_frame(frame)))

    def reset_lights(self) -> None:
        self._commit(replace(self.selected_layout(), lights=LightFrame()))

    def apply_standard_layout(self) -> None:
        """Restore the factory presentation of the selected layout. Save stores it."""
        current = self.selected_layout()
        factory = factory_layout()
        self._commit(replace(factory, id=current.id, name=current.name, builtin=current.builtin))

    def mark_default(self) -> None:
        """Persist the selected layout as the one a new live view loads."""
        self._config = self._store.save(mark_default(self._config, self.selected_layout().id))
        self._fill_form()

    def save(self) -> HudConfiguration:
        self._config = self._store.save(self._config)
        self._fill_form()
        return self._config

    def save_persistent(self) -> None:
        """Write the layout that is on screen, including edits that were not saved yet."""
        self.save()

    def revert(self) -> None:
        self._stop_cue()
        self._config = self._store.load()
        self._apply_preview()
        self._fill_form()

    def preview_start_sequence(self) -> None:
        """Play the existing start cue in the preview. It does not start a race."""
        self._stop_cue()
        self._sequence_started = False
        cue = StartCue(self._note_sequence_started, interval_ms=60_000, parent=self)
        cue.changed.connect(self._show_preview_step)
        cue.finished.connect(self._finish_preview_sequence)
        self._cue = cue
        cue.begin()

    def advance_light_preview(self) -> None:
        cue = self._cue
        if cue is not None:
            cue.advance()

    def _build(self) -> None:
        tr = self._translator.translate
        form = QWidget()
        form.setObjectName("hud-editor-form")
        column = QVBoxLayout(form)
        column.setContentsMargins(0, 0, SPACE.sm, 0)
        column.setSpacing(SPACE.sm)

        self._layouts = QComboBox()
        self._layouts.setObjectName("hud-layout")
        self._layouts.currentIndexChanged.connect(self._on_layout)
        self._name = QLineEdit()
        self._name.setObjectName("hud-layout-name")
        self._name.editingFinished.connect(self._on_name)
        self._default_mark = QLabel()
        self._default_mark.setObjectName("hud-default-mark")
        column.addWidget(self._layouts)
        column.addWidget(self._name)
        column.addWidget(self._default_mark)

        actions = QHBoxLayout()
        self._new = _button("hud-layout-new", tr("hud.layout.new"), self._on_new)
        self._delete = _button("hud-layout-delete", tr("hud.layout.delete"), self.delete_selected)
        self._default = _button("hud-set-default", tr("hud.layout.default"), self.mark_default)
        actions.addWidget(self._new)
        actions.addWidget(self._delete)
        column.addLayout(actions)
        column.addWidget(self._default)

        self._font = _scale_spin("hud-font-scale")
        self._font.valueChanged.connect(self._on_font)
        _row(column, tr("hud.font_scale"), self._font)
        self._alignment = QComboBox()
        self._alignment.setObjectName("hud-alignment")
        for key in ("center", "left", "right"):
            self._alignment.addItem(tr(f"hud.alignment.{key}"), key)
        self._alignment.currentIndexChanged.connect(self._on_alignment)
        _row(column, tr("hud.alignment"), self._alignment)

        self._visible: dict[str, QCheckBox] = {}
        self._scales: dict[str, QSpinBox] = {}
        for field_id in FIELD_IDS:
            checkbox = QCheckBox(tr(f"hud.field.{field_id}"))
            checkbox.setObjectName(f"hud-visible-{field_id}")
            scale = _scale_spin(f"hud-scale-{field_id}")
            checkbox.toggled.connect(
                lambda checked, field=field_id: self._on_visible(field, checked)
            )
            scale.valueChanged.connect(
                lambda value, field=field_id: self._on_field_scale(field, value)
            )
            line = QHBoxLayout()
            line.addWidget(checkbox, 1)
            line.addWidget(scale)
            column.addLayout(line)
            self._visible[field_id] = checkbox
            self._scales[field_id] = scale

        self._shares: dict[str, QSpinBox] = {}
        for part in ("clock", "status", "lanes", "ranking"):
            spin = QSpinBox()
            spin.setObjectName(f"hud-share-{part}")
            spin.setRange(SHARE_MIN, SHARE_MAX)
            spin.valueChanged.connect(lambda value, name=part: self._on_share(name, value))
            self._shares[part] = spin
            _row(column, tr(f"hud.share.{part}"), spin)

        self._lights_visible = QCheckBox(tr("hud.lights.visible"))
        self._lights_visible.setObjectName("hud-lights-visible")
        self._lights_visible.toggled.connect(self._on_lights_visible)
        column.addWidget(self._lights_visible)
        self._light_spins: dict[str, QSpinBox] = {}
        for axis in ("x", "y", "width", "height"):
            spin = QSpinBox()
            spin.setObjectName(f"hud-lights-{axis}")
            spin.setSuffix(" %")
            spin.setRange(0, 100)
            spin.valueChanged.connect(self._on_light_spin)
            self._light_spins[axis] = spin
            _row(column, tr(f"hud.lights.{axis}"), spin)
        light_actions = QHBoxLayout()
        light_actions.addWidget(
            _button("hud-lights-reset", tr("hud.lights.reset"), self.reset_lights)
        )
        light_actions.addWidget(
            _button("hud-lights-preview", tr("hud.lights.preview"), self.preview_start_sequence)
        )
        column.addLayout(light_actions)

        self._status = QComboBox()
        self._status.setObjectName("hud-preview-status")
        for key, label_key in _PREVIEW_STATUSES:
            self._status.addItem(tr(label_key), key)
        self._status.currentIndexChanged.connect(self._on_preview_status)
        _row(column, tr("hud.preview.status"), self._status)
        self._lanes = QSpinBox()
        self._lanes.setObjectName("hud-preview-lanes")
        self._lanes.setRange(2, 4)
        self._lanes.valueChanged.connect(self._on_preview_lanes)
        _row(column, tr("hud.preview.lanes"), self._lanes)

        store_actions = QHBoxLayout()
        store_actions.addWidget(
            _button("hud-standard", tr("hud.standard"), self.apply_standard_layout)
        )
        store_actions.addWidget(_button("hud-save", tr("hud.save"), self.save_persistent))
        store_actions.addWidget(_button("hud-revert", tr("hud.revert"), self.revert))
        column.addLayout(store_actions)
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(form)
        scroll.setMinimumWidth(340)
        scroll.setMaximumWidth(460)

        layout = QHBoxLayout(self)
        configure_page(layout)
        layout.addWidget(scroll)
        layout.addWidget(self.preview, 1)

    def _commit(self, layout: HudLayout) -> None:
        self._config = replace_layout(replace(self._config, selected_id=layout.id), layout)
        self._apply_preview()
        self._fill_form()

    def _with_field(self, field_id: str, style: FieldStyle) -> HudLayout:
        fields = tuple(
            (key, style if key == field_id else value)
            for key, value in self.selected_layout().fields
        )
        return replace(self.selected_layout(), fields=fields)

    def _apply_preview(self) -> None:
        self.stage.apply_layout(self.selected_layout())
        self._show_sample()
        self._place_lights()

    def _show_sample(self) -> None:
        snapshot, message_key = _sample(self._preview_status, self._preview_lanes)
        self.stage.show_standings(snapshot, mode=RaceMode.LAPS, lane_count=self._preview_lanes)
        self.stage.messages.message_label.setText(self._translator.translate(message_key))

    def _fill_form(self) -> None:
        layout = self.selected_layout()
        self._filling = True
        try:
            self._layouts.blockSignals(True)
            self._layouts.clear()
            for item in self._config.layouts:
                label = item.name
                if item.id == self._config.default_id:
                    label = f"{label} [Standard]"
                self._layouts.addItem(label, item.id)
            index = self._layouts.findData(self._config.selected_id)
            self._layouts.setCurrentIndex(max(index, 0))
            self._layouts.blockSignals(False)
            self._name.setText(layout.name)
            default = self._config.default_layout()
            self._default_mark.setText(
                self._translator.format("hud.layout.default_mark", name=default.name)
            )
            self._delete.setEnabled(not layout.builtin)
            self._font.setValue(layout.font_scale)
            alignment = self._alignment.findData(layout.alignment)
            self._alignment.setCurrentIndex(max(alignment, 0))
            for field_id, checkbox in self._visible.items():
                style = layout.field(field_id)
                checkbox.setChecked(style.visible)
                self._scales[field_id].setValue(style.scale)
            self._shares["clock"].setValue(layout.clock_share)
            self._shares["status"].setValue(layout.status_share)
            self._shares["lanes"].setValue(layout.lanes_share)
            self._shares["ranking"].setValue(layout.ranking_share)
            self._sync_light_spins(layout.lights)
            self._status.setCurrentIndex(max(self._status.findData(self._preview_status), 0))
            self._lanes.setValue(self._preview_lanes)
        finally:
            self._filling = False

    def _sync_light_spins(self, frame: LightFrame) -> None:
        width = _percent(frame.width)
        height = _percent(frame.height)
        self._light_spins["width"].setRange(6, 100)
        self._light_spins["height"].setRange(6, 100)
        self._light_spins["width"].setValue(width)
        self._light_spins["height"].setValue(height)
        self._light_spins["x"].setRange(0, max(0, 100 - width))
        self._light_spins["y"].setRange(0, max(0, 100 - height))
        self._light_spins["x"].setValue(min(_percent(frame.x), self._light_spins["x"].maximum()))
        self._light_spins["y"].setValue(min(_percent(frame.y), self._light_spins["y"].maximum()))
        self._lights_visible.setChecked(frame.visible)

    def _place_lights(self, *, move_handle: bool = True) -> None:
        stage = self.stage
        if stage.width() <= 1 or stage.height() <= 1:
            return
        frame = self.selected_layout().lights
        rect = to_pixels(frame.as_config(), stage.width(), stage.height())
        origin = stage.mapTo(self.preview, QPoint(0, 0))
        placed = QRect(origin.x() + rect.x, origin.y() + rect.y, rect.width, rect.height)
        if self.lights.geometry() != placed:
            self.lights.setGeometry(placed)
        if move_handle and not self.handle.is_dragging() and self.handle.geometry() != placed:
            self.handle.setGeometry(placed)
        sequence = self._cue is not None
        if frame.visible or sequence:
            if not sequence and self.lights.isHidden():
                self.lights.show_lights(0)
            self.lights.raise_()
        elif not self.lights.isHidden():
            self.lights.clear()
        self.handle.raise_()

    def _frame_from_handle(self, x: int, y: int, width: int, height: int) -> LightFrame:
        stage = self.stage
        origin = stage.mapTo(self.preview, QPoint(0, 0))
        surface_w = max(stage.width(), 1)
        surface_h = max(stage.height(), 1)
        current = self.selected_layout().lights
        return _clamped_frame(
            LightFrame(
                visible=current.visible,
                x=(x - origin.x()) / surface_w,
                y=(y - origin.y()) / surface_h,
                width=width / surface_w,
                height=height / surface_h,
            )
        )

    def _on_handle(self, x: int, y: int, width: int, height: int) -> None:
        frame = self._frame_from_handle(x, y, width, height)
        layout = replace(self.selected_layout(), lights=frame)
        self._config = replace_layout(replace(self._config, selected_id=layout.id), layout)
        move_handle = not self.handle.is_dragging()
        self._place_lights(move_handle=move_handle)
        self._filling = True
        try:
            self._sync_light_spins(frame)
        finally:
            self._filling = False

    def _on_layout(self) -> None:
        if self._filling:
            return
        layout_id = self._layouts.currentData()
        if isinstance(layout_id, str):
            self.select_layout(layout_id)

    def _on_name(self) -> None:
        if self._filling:
            return
        text = self._name.text().strip()
        current = self.selected_layout()
        if not text or text == current.name:
            return
        self._commit(replace(current, name=text))

    def _on_new(self) -> None:
        self.add_layout(None)

    def _on_font(self, value: int) -> None:
        if self._filling:
            return
        snapped = snap_scale(value)
        if snapped != value:
            self._font.setValue(snapped)
            return
        if snapped != self.selected_layout().font_scale:
            self.set_font_scale(snapped)

    def _on_alignment(self) -> None:
        if self._filling:
            return
        alignment = self._alignment.currentData()
        if isinstance(alignment, str):
            self.set_alignment(alignment)

    def _on_visible(self, field_id: str, visible: bool) -> None:
        if self._filling or visible == self.selected_layout().field(field_id).visible:
            return
        self.set_field_visible(field_id, visible)

    def _on_field_scale(self, field_id: str, value: int) -> None:
        if self._filling:
            return
        snapped = snap_scale(value)
        if snapped != value:
            self._scales[field_id].setValue(snapped)
            return
        if snapped != self.selected_layout().field(field_id).scale:
            self.set_field_scale(field_id, snapped)

    def _on_share(self, part: str, value: int) -> None:
        if self._filling:
            return
        current = getattr(self.selected_layout(), f"{part}_share")
        if value != current:
            self.set_share(part, value)

    def _on_lights_visible(self, visible: bool) -> None:
        if self._filling or visible == self.selected_layout().lights.visible:
            return
        frame = replace(self.selected_layout().lights, visible=visible)
        self.set_light_frame(frame)

    def _on_light_spin(self) -> None:
        if self._filling:
            return
        current = self.selected_layout().lights
        frame = _clamped_frame(
            LightFrame(
                visible=self._lights_visible.isChecked(),
                x=self._light_spins["x"].value() / 100,
                y=self._light_spins["y"].value() / 100,
                width=self._light_spins["width"].value() / 100,
                height=self._light_spins["height"].value() / 100,
            )
        )
        if frame != current:
            self.set_light_frame(frame)

    def _on_preview_status(self) -> None:
        if self._filling:
            return
        status = self._status.currentData()
        if isinstance(status, str) and status != self._preview_status:
            self._preview_status = status
            self._show_sample()

    def _on_preview_lanes(self, value: int) -> None:
        if self._filling or value == self._preview_lanes:
            return
        self._preview_lanes = value
        self._show_sample()

    def _show_preview_step(self, step: object) -> None:
        if isinstance(step, StartCueStep):
            self.lights.show_step(step)
            self._place_lights()

    def _finish_preview_sequence(self) -> None:
        self._cue = None
        self._restore_idle_lights()

    def _note_sequence_started(self) -> None:
        self._sequence_started = True

    def _stop_cue(self, *, restore: bool = True) -> None:
        cue = self._cue
        self._cue = None
        if cue is not None:
            cue.blockSignals(True)
            cue.stop()
        if restore:
            self._restore_idle_lights()

    def _restore_idle_lights(self) -> None:
        frame = self.selected_layout().lights
        if frame.visible:
            self.lights.show_lights(0)
        elif not self.lights.isHidden():
            self.lights.clear()
        self._place_lights()


def _scale_spin(object_name: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setObjectName(object_name)
    spin.setRange(SCALE_MIN, SCALE_MAX)
    spin.setSingleStep(SCALE_STEP)
    spin.setSuffix(" %")
    return spin


def _button(object_name: str, text: str, slot: Callable[[], None]) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    button.clicked.connect(slot)
    return button


def _row(parent: QVBoxLayout, label: str, widget: QWidget) -> None:
    line = QHBoxLayout()
    caption = QLabel(label)
    line.addWidget(caption)
    line.addWidget(widget, 1)
    parent.addLayout(line)


def _percent(value: float) -> int:
    return round(value * 100)
