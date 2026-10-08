"""Settings editor for the live HUD. The preview is the same surface as a race.

The settings page only launches a window. That window keeps the navigation column of the
live shell on the left and the real ``LiveHudStage`` on the right.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import ClassVar

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import (
    FIELD_IDS,
    LIGHT_ASPECT,
    SCALE_MAX,
    SCALE_MIN,
    SCALE_STEP,
    SHARE_MAX,
    SHARE_MIN,
    FieldStyle,
    HudConfiguration,
    HudConfigurationStore,
    HudLayout,
    HudWindowPlacement,
    LightFrame,
    add_layout,
    delete_layout,
    effective_light_scale,
    factory_layout,
    light_frame,
    light_frame_from_box,
    light_pixels,
    light_scale_limits,
    mark_default,
    normalize_light,
    replace_layout,
    snap_alignment,
    snap_scale,
    snap_share,
    to_document,
)
from slot_racing.modules.races.runner import LiveRow, RaceSnapshot
from slot_racing.modules.races.ui.live_stage import LiveHudStage
from slot_racing.modules.races.ui.start_cue import StartCue, StartCueStep
from slot_racing.modules.races.ui.start_lights import StartLightWidget
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import NAVIGATION_WIDTH, SPACE, set_role
from slot_racing.uikit.widgets import StatusPill

logger = logging.getLogger(__name__)

_MIN_WINDOW_WIDTH = 640
_MIN_WINDOW_HEIGHT = 400
_MAX_WINDOW_EXTENT = 10000
_DIALOG_TEXT_SPARE = 12
_BUTTON_CHROME = 14 * 2 + 2

_HANDLE = 14
_PREVIEW_STATUSES: tuple[tuple[str, str], ...] = (
    ("running", "race.status.running"),
    ("paused", "race.status.paused"),
    ("finished", "race.status.finished"),
    ("aborted", "race.status.aborted"),
)
_DRIVERS = ("Zoe", "Anna", "Ben", "Mia")
_VEHICLES = ("Porsche 911", "Ferrari 488", "Audi R8", "BMW M4")


@dataclass(frozen=True, slots=True)
class LiveHudInsets:
    """Pixels the shell spends around the live stage."""

    navigation_width: int
    left: int
    top: int
    right: int
    bottom: int


def live_hud_insets(host: QWidget | None = None) -> LiveHudInsets:
    """Read the live HUD frame from the shell that is actually on screen.

    A standalone editor falls back to the same navigation width and content margins the
    shell uses, plus the header's size hint, so the numbers are not a second guess.
    """
    shell = _shell_window(host)
    if shell is not None:
        sidebar = shell.findChild(QWidget, "sidebar")
        header = shell.findChild(QWidget, "shell-header")
        content = shell.findChild(QWidget, "shell-content")
        layout = None if content is None else content.layout()
        if sidebar is not None and header is not None and layout is not None:
            margins = layout.contentsMargins()
            spacing = max(layout.spacing(), 0)
            return LiveHudInsets(
                navigation_width=_span(sidebar, horizontal=True),
                left=margins.left(),
                top=margins.top() + _span(header, horizontal=False) + spacing,
                right=margins.right(),
                bottom=margins.bottom(),
            )
    return _fallback_insets()


def _shell_window(host: QWidget | None) -> QWidget | None:
    current = host
    while current is not None:
        sidebar = current.findChild(QWidget, "sidebar")
        content = current.findChild(QWidget, "shell-content")
        if sidebar is not None and content is not None:
            return current
        current = current.parentWidget()
    return None


def _span(widget: QWidget, *, horizontal: bool) -> int:
    value = widget.width() if horizontal else widget.height()
    if value > 0:
        return value
    hint = widget.sizeHint()
    hinted = hint.width() if horizontal else hint.height()
    minimum = widget.minimumWidth() if horizontal else widget.minimumHeight()
    return max(hinted, minimum, 1)


_FALLBACK_TOP: int | None = None


def _fallback_insets() -> LiveHudInsets:
    return LiveHudInsets(
        navigation_width=NAVIGATION_WIDTH,
        left=SPACE.lg,
        top=_fallback_top(),
        right=SPACE.lg,
        bottom=SPACE.lg,
    )


def _fallback_top() -> int:
    global _FALLBACK_TOP
    if _FALLBACK_TOP is not None:
        return _FALLBACK_TOP
    header = QWidget()
    title = QLabel("Rennen")
    set_role(title, "page-title")
    status = StatusPill("shell-status")
    status.set_status("Simulation", "ok")
    row = QHBoxLayout(header)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE.md)
    row.addWidget(title, 1)
    row.addWidget(status, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    header.ensurePolished()
    top = SPACE.md + max(header.sizeHint().height(), 1) + SPACE.md
    header.deleteLater()
    _FALLBACK_TOP = top
    return top


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
            width = max(_HANDLE * 2, origin.width() + delta.x())
            rect.setWidth(width)
            rect.setHeight(max(_HANDLE * 2, round(width / LIGHT_ASPECT)))
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
        self._commit(replace(self.selected_layout(), lights=normalize_light(frame)))

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

    def save_and_close(self) -> None:
        """Store the layout and close the editor after the store confirms the write."""
        try:
            self.save()
        except Exception as error:
            logger.exception("Could not save the HUD layout")
            self._show_save_error(error)
            return
        if self.is_dirty():
            self._show_save_error(RuntimeError("stored document differs"))
            return
        host = self.window()
        if isinstance(host, HudEditorWindow):
            host.close()

    def _show_save_error(self, error: BaseException) -> None:
        box = QMessageBox(self.window())
        box.setObjectName("hud-save-error")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self._translator.translate("hud.window.title"))
        box.setText(
            f"{self._translator.translate('hud.save.failed')}\n\n"
            f"{describe_error(self._translator, error)}"
        )
        _keep_dialog_text_visible(box)
        box.exec()

    def revert(self) -> None:
        self._stop_cue()
        self._config = self._store.load()
        self._apply_preview()
        self._fill_form()

    def is_dirty(self) -> bool:
        """True when the layout on screen differs from the stored document.

        The simulated status and the lane count are preview-only and stay out of this check.
        """
        return to_document(self._config) != to_document(self._store.load())

    def match_live(self) -> bool:
        return self._match.isChecked()

    def set_match_live(self, enabled: bool) -> None:
        self._match.setChecked(enabled)
        self.sync_chrome()

    def sync_chrome(self) -> None:
        """Keep the sidebar in the navigation column and the stage inside the live frame."""
        insets = live_hud_insets(self)
        self._scroll.setFixedWidth(insets.navigation_width)
        if self.match_live():
            self._canvas_layout.setContentsMargins(
                insets.left, insets.top, insets.right, insets.bottom
            )
        else:
            self._canvas_layout.setContentsMargins(0, 0, 0, 0)
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self._place_lights()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.sync_chrome()

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
        column.setContentsMargins(SPACE.sm, SPACE.sm, SPACE.sm, SPACE.sm)
        column.setSpacing(SPACE.sm)

        _heading(column, tr("hud.group.surface"))
        self._match = QCheckBox(tr("hud.match_live"))
        self._match.setObjectName("hud-match-live")
        self._match.setToolTip(tr("hud.match_live.hint"))
        self._match.setChecked(True)
        self._match.toggled.connect(self._on_match)
        column.addWidget(self._match)

        _heading(column, tr("hud.group.layout"))
        self._layouts = QComboBox()
        self._layouts.setObjectName("hud-layout")
        self._layouts.currentIndexChanged.connect(self._on_layout)
        self._name = QLineEdit()
        self._name.setObjectName("hud-layout-name")
        self._name.editingFinished.connect(self._on_name)
        self._default_mark = QLabel()
        self._default_mark.setObjectName("hud-default-mark")
        self._default_mark.setWordWrap(True)
        column.addWidget(self._layouts)
        column.addWidget(self._name)
        column.addWidget(self._default_mark)
        self._new = _button("hud-layout-new", tr("hud.layout.new"), self._on_new)
        self._delete = _button("hud-layout-delete", tr("hud.layout.delete"), self.delete_selected)
        self._default = _button("hud-set-default", tr("hud.layout.default"), self.mark_default)
        column.addWidget(self._new)
        column.addWidget(self._delete)
        column.addWidget(self._default)

        _heading(column, tr("hud.group.text"))
        self._font = _scale_spin("hud-font-scale")
        self._font.valueChanged.connect(self._on_font)
        _row(column, tr("hud.font_scale"), self._font)
        self._alignment = QComboBox()
        self._alignment.setObjectName("hud-alignment")
        for key in ("center", "left", "right"):
            self._alignment.addItem(tr(f"hud.alignment.{key}"), key)
        self._alignment.currentIndexChanged.connect(self._on_alignment)
        _row(column, tr("hud.alignment"), self._alignment)

        _heading(column, tr("hud.group.fields"))
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
            column.addWidget(checkbox)
            column.addWidget(scale)
            self._visible[field_id] = checkbox
            self._scales[field_id] = scale

        _heading(column, tr("hud.group.shares"))
        self._shares: dict[str, QSpinBox] = {}
        for part in ("clock", "status", "lanes", "ranking"):
            spin = QSpinBox()
            spin.setObjectName(f"hud-share-{part}")
            spin.setRange(SHARE_MIN, SHARE_MAX)
            spin.valueChanged.connect(lambda value, name=part: self._on_share(name, value))
            self._shares[part] = spin
            _row(column, tr(f"hud.share.{part}"), spin)

        _heading(column, tr("hud.group.lights"))
        self._lights_visible = QCheckBox(tr("hud.lights.visible"))
        self._lights_visible.setObjectName("hud-lights-visible")
        self._lights_visible.toggled.connect(self._on_lights_visible)
        column.addWidget(self._lights_visible)
        self._light_spins: dict[str, QSpinBox] = {}
        for axis in ("x", "y"):
            spin = QSpinBox()
            spin.setObjectName(f"hud-lights-{axis}")
            spin.setSuffix(" %")
            spin.setRange(0, 100)
            spin.valueChanged.connect(self._on_light_spin)
            self._light_spins[axis] = spin
            _row(column, tr(f"hud.lights.{axis}"), spin)
        self._light_scale = QSlider(Qt.Orientation.Horizontal)
        self._light_scale.setObjectName("hud-lights-scale")
        self._light_scale.setRange(20, 200)
        self._light_scale.valueChanged.connect(self._on_light_scale)
        self._light_readout = QLabel("100 %")
        self._light_readout.setObjectName("hud-lights-scale-readout")
        scale_row = QWidget()
        scale_line = QHBoxLayout(scale_row)
        scale_line.setContentsMargins(0, 0, 0, 0)
        scale_line.addWidget(self._light_scale, 1)
        scale_line.addWidget(self._light_readout)
        _row(column, tr("hud.lights.scale"), scale_row)
        column.addWidget(_button("hud-lights-reset", tr("hud.lights.reset"), self.reset_lights))
        column.addWidget(
            _button("hud-lights-preview", tr("hud.lights.preview"), self.preview_start_sequence)
        )

        _heading(column, tr("hud.group.preview"))
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

        column.addWidget(_button("hud-standard", tr("hud.standard"), self.apply_standard_layout))
        column.addWidget(_button("hud-save-close", tr("hud.save_close"), self.save_and_close))
        column.addWidget(_button("hud-revert", tr("hud.revert"), self.revert))
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("hud-editor-sidebar")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(form)
        scroll.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._scroll = scroll

        canvas = QWidget()
        canvas.setObjectName("hud-live-canvas")
        canvas_layout = QVBoxLayout(canvas)
        canvas_layout.setSpacing(0)
        canvas_layout.addWidget(self.preview, 1)
        self._canvas_layout = canvas_layout

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(scroll)
        layout.addWidget(canvas, 1)
        self.sync_chrome()

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
            self._sync_light_controls(layout.lights)
            self._status.setCurrentIndex(max(self._status.findData(self._preview_status), 0))
            self._lanes.setValue(self._preview_lanes)
        finally:
            self._filling = False

    def _sync_light_controls(self, frame: LightFrame) -> None:
        stage_w = self.stage.width()
        stage_h = self.stage.height()
        low, high = light_scale_limits(stage_w, stage_h)
        shown = effective_light_scale(frame.scale, stage_w, stage_h)
        pixels = light_pixels(frame, stage_w, stage_h)
        width_pct = 0 if stage_w <= 1 else round(pixels.width / stage_w * 100)
        height_pct = 0 if stage_h <= 1 else round(pixels.height / stage_h * 100)
        controls = (
            self._light_scale,
            self._light_spins["x"],
            self._light_spins["y"],
            self._lights_visible,
        )
        for control in controls:
            control.blockSignals(True)
        try:
            self._light_scale.setRange(low, high)
            self._light_scale.setValue(shown)
            self._light_readout.setText(f"{shown} %")
            self._light_spins["x"].setRange(0, max(0, 100 - width_pct))
            self._light_spins["y"].setRange(0, max(0, 100 - height_pct))
            if stage_w > 1:
                self._light_spins["x"].setValue(
                    min(round(pixels.x / stage_w * 100), self._light_spins["x"].maximum())
                )
            if stage_h > 1:
                self._light_spins["y"].setValue(
                    min(round(pixels.y / stage_h * 100), self._light_spins["y"].maximum())
                )
            self._lights_visible.setChecked(frame.visible)
        finally:
            for control in controls:
                control.blockSignals(False)

    def _place_lights(self, *, move_handle: bool = True) -> None:
        stage = self.stage
        if stage.width() <= 1 or stage.height() <= 1:
            return
        frame = self.selected_layout().lights
        rect = light_pixels(frame, stage.width(), stage.height())
        origin = stage.mapTo(self.preview, QPoint(0, 0))
        placed = QRect(origin.x() + rect.x, origin.y() + rect.y, rect.width, rect.height)
        if self.lights.geometry() != placed:
            self.lights.setGeometry(placed)
        if move_handle and self.handle.geometry() != placed:
            self.handle.setGeometry(placed)
        sequence = self._cue is not None
        if frame.visible or sequence:
            if not sequence and self.lights.isHidden():
                self.lights.show_lights(0)
            self.lights.raise_()
        elif not self.lights.isHidden():
            self.lights.clear()
        self.handle.raise_()
        self._sync_light_controls(frame)

    def _frame_from_handle(self, x: int, y: int, width: int, height: int) -> LightFrame:
        stage = self.stage
        origin = stage.mapTo(self.preview, QPoint(0, 0))
        current = self.selected_layout().lights
        return light_frame_from_box(
            x - origin.x(),
            y - origin.y(),
            width,
            height,
            max(stage.width(), 1),
            max(stage.height(), 1),
            visible=current.visible,
        )

    def _on_handle(self, x: int, y: int, width: int, height: int) -> None:
        frame = self._frame_from_handle(x, y, width, height)
        layout = replace(self.selected_layout(), lights=frame)
        self._config = replace_layout(replace(self._config, selected_id=layout.id), layout)
        self._place_lights()

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
        frame = light_frame(
            visible=self._lights_visible.isChecked(),
            x=self._light_spins["x"].value() / 100,
            y=self._light_spins["y"].value() / 100,
            scale=current.scale,
        )
        if frame != current:
            self.set_light_frame(frame)

    def _on_light_scale(self, value: int) -> None:
        if self._filling:
            return
        current = self.selected_layout().lights
        frame = light_frame(
            visible=self._lights_visible.isChecked(),
            x=current.x,
            y=current.y,
            scale=value,
        )
        if frame.scale != current.scale:
            self.set_light_frame(frame)

    def _on_match(self, _checked: bool) -> None:
        self.sync_chrome()

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
    button.setProperty("compact", True)
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    button.clicked.connect(slot)
    return button


def _heading(parent: QVBoxLayout, text: str) -> None:
    label = QLabel(text)
    set_role(label, "section")
    parent.addWidget(label)


def _row(parent: QVBoxLayout, label: str, widget: QWidget) -> None:
    line = QHBoxLayout()
    caption = QLabel(label)
    line.addWidget(caption)
    line.addWidget(widget, 1)
    parent.addLayout(line)


class HudEditorWindow(QMainWindow):
    """One maximizable editor. Closing it leaves the main window running."""

    _open: ClassVar[dict[int, weakref.ReferenceType[HudEditorWindow]]] = {}

    def __init__(
        self, parent: QWidget, translator: Translator, store: HudConfigurationStore
    ) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setWindowTitle(translator.translate("hud.window.title"))
        self._translator = translator
        self._store = store
        self._forced_close = False
        self._want_maximized = False
        self.editor = HudEditor(translator, store)
        self.setCentralWidget(self.editor)
        placement = store.load_window()
        self.editor.set_match_live(placement.match_live)
        self._restore_geometry(placement)
        if parent is not None:
            parent.installEventFilter(self)

    @classmethod
    def present(
        cls, parent: QWidget, translator: Translator, store: HudConfigurationStore
    ) -> HudEditorWindow:
        """Show the editor for this store. A second call raises the window that is already open."""
        key = id(store)
        current = cls._current(key)
        if current is None:
            current = cls(parent, translator, store)
            cls._open[key] = weakref.ref(current)
        elif not current.isVisible() and not current.editor.is_dirty():
            current.editor.revert()
        if current.isMinimized():
            current.showNormal()
        current.show()
        current.raise_()
        current.activateWindow()
        return current

    @classmethod
    def _current(cls, key: int) -> HudEditorWindow | None:
        ref = cls._open.get(key)
        current = None if ref is None else ref()
        if current is None:
            cls._open.pop(key, None)
        return current

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if self._forced_close:
            return super().eventFilter(watched, event)
        if watched is self.parentWidget() and event.type() == QEvent.Type.Hide:
            self._forced_close = True
            try:
                self.close()
            finally:
                self._forced_close = False
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._forced_close and self.editor.is_dirty():
            choice = self._ask_unsaved()
            if choice is None:
                event.ignore()
                return
            if choice == "save":
                self.editor.save()
            else:
                self.editor.revert()
        self.editor._stop_cue()
        self._remember_geometry()
        super().closeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._want_maximized:
            self._want_maximized = False
            self.showMaximized()

    def _ask_unsaved(self) -> str | None:
        tr = self._translator.translate
        box = QMessageBox(self)
        box.setObjectName("hud-close-dialog")
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("hud.window.title"))
        box.setText(tr("hud.close.unsaved"))
        save = box.addButton(tr("hud.close.save"), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(tr("hud.close.discard"), QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(tr("hud.close.cancel"), QMessageBox.ButtonRole.RejectRole)
        save.setObjectName("hud-close-save")
        discard.setObjectName("hud-close-discard")
        cancel.setObjectName("hud-close-cancel")
        box.setDefaultButton(cancel)
        _keep_dialog_text_visible(box)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        if clicked is discard:
            return "discard"
        return None

    def _restore_geometry(self, placement: HudWindowPlacement) -> None:
        self._want_maximized = placement.maximized
        width = placement.width
        height = placement.height
        if (
            isinstance(width, int)
            and isinstance(height, int)
            and _usable_window_size(width, height)
        ):
            self.resize(width, height)
        else:
            self._apply_default_size()
        x = placement.x
        y = placement.y
        if isinstance(x, int) and isinstance(y, int) and _usable_window_position(x, y):
            self.move(x, y)

    def _apply_default_size(self) -> None:
        parent = self.parentWidget()
        if (
            isinstance(parent, QWidget)
            and parent.isVisible()
            and parent.width() >= _MIN_WINDOW_WIDTH
            and parent.height() >= _MIN_WINDOW_HEIGHT
        ):
            self.resize(parent.size())
            self.move(parent.pos())
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1400, 900)
            return
        available = screen.availableGeometry()
        width = min(max(int(available.width() * 0.92), _MIN_WINDOW_WIDTH), available.width())
        height = min(max(int(available.height() * 0.92), _MIN_WINDOW_HEIGHT), available.height())
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def _remember_geometry(self) -> None:
        maximized = bool(self.windowState() & Qt.WindowState.WindowMaximized)
        geo = self.normalGeometry() if maximized else self.geometry()
        if not _usable_window_size(geo.width(), geo.height()):
            geo = self.geometry()
        width = geo.width() if _usable_window_size(geo.width(), geo.height()) else None
        height = geo.height() if width is not None else None
        x = geo.x() if _usable_window_position(geo.x(), geo.y()) else None
        y = geo.y() if x is not None else None
        try:
            self._store.save_window(
                HudWindowPlacement(
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    maximized=maximized,
                    match_live=self.editor.match_live(),
                )
            )
        except Exception:
            logger.exception("Could not store the HUD editor window")


class HudEditorLauncher(QWidget):
    """Settings entry. The editor itself lives in its own window."""

    def __init__(self, translator: Translator, store: HudConfigurationStore) -> None:
        super().__init__()
        self.setObjectName("race-hud-settings")
        self._translator = translator
        self._store = store
        button = QPushButton(translator.translate("hud.open_editor"))
        button.setObjectName("hud-open-editor")
        button.clicked.connect(self.open_editor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(button)

    def open_editor(self) -> HudEditorWindow:
        return HudEditorWindow.present(self.window(), self._translator, self._store)


def _usable_window_size(width: int | None, height: int | None) -> bool:
    return (
        isinstance(width, int)
        and isinstance(height, int)
        and _MIN_WINDOW_WIDTH <= width <= _MAX_WINDOW_EXTENT
        and _MIN_WINDOW_HEIGHT <= height <= _MAX_WINDOW_EXTENT
    )


def _usable_window_position(x: int | None, y: int | None) -> bool:
    return (
        isinstance(x, int)
        and isinstance(y, int)
        and abs(x) <= _MAX_WINDOW_EXTENT
        and abs(y) <= _MAX_WINDOW_EXTENT
    )


def _keep_dialog_text_visible(box: QMessageBox) -> None:
    """Widen the message and its buttons so the last letter is not clipped."""
    label = box.findChild(QLabel, "qt_msgbox_label")
    if label is not None and label.text():
        label.ensurePolished()
        widest = max(
            label.fontMetrics().horizontalAdvance(line) for line in label.text().splitlines()
        )
        label.setMinimumWidth(widest + _DIALOG_TEXT_SPARE)
    for button in box.buttons():
        if not isinstance(button, QPushButton) or not button.text():
            continue
        button.ensurePolished()
        advance = button.fontMetrics().horizontalAdvance(button.text())
        button.setMinimumWidth(
            max(button.sizeHint().width(), advance + _BUTTON_CHROME) + _DIALOG_TEXT_SPARE
        )
