"""Settings editor for the live HUD. The preview is the same surface as a race.

The settings page only launches a window. Settings stay in a scrollable column on the
left. The real ``LiveHudStage`` fills the remaining width on the right.
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
    QFormLayout,
    QGroupBox,
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
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import (
    DISPLAY_AUTO,
    DISPLAY_HIDE,
    DISPLAY_MODES,
    FIELD_IDS,
    LIGHT_ASPECT,
    PANEL_RECORDS,
    SCALE_MAX,
    SCALE_MIN,
    SCALE_STEP,
    SHARE_MAX,
    SHARE_MIN,
    TEXT_ROLES,
    VIEW_IDS,
    VIEW_LIVE,
    FieldStyle,
    HudConfiguration,
    HudConfigurationStore,
    HudLayout,
    HudWindowPlacement,
    LightFrame,
    ViewStyle,
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
    snap_display,
    snap_scale,
    snap_share,
    to_document,
    with_view,
)
from slot_racing.modules.races.runner import LiveRow, RaceSnapshot
from slot_racing.modules.races.ui.live_stage import LiveHudStage
from slot_racing.modules.races.ui.race_briefing import apply_text_size
from slot_racing.modules.races.ui.start_lights import StartLightWidget
from slot_racing.uikit.enter import bind_enter
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import (
    FONT_CAPTION,
    FONT_LANE,
    FONT_VALUE,
    NAVIGATION_WIDTH,
    SPACE,
    set_role,
)
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
        # The stage scrolls on its own. A large minimum here used to steal the
        # width the settings column needs and pushed those controls on top of
        # each other.
        self.setMinimumSize(320, 200)
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


class SurfaceSample(QWidget):
    """Preview of a board that is not the live lane HUD."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("hud-surface-sample")
        self._labels: dict[str, QLabel] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.md, SPACE.md, SPACE.md, SPACE.md)
        layout.setSpacing(SPACE.sm)
        bases = {
            "header": FONT_CAPTION,
            "driver": FONT_LANE,
            "vehicle": FONT_VALUE,
            "place": FONT_LANE,
            "time": FONT_VALUE,
            "row": FONT_VALUE,
        }
        self._bases = bases
        for role in TEXT_ROLES:
            label = QLabel()
            label.setObjectName(f"hud-sample-{role}")
            label.setWordWrap(True)
            layout.addWidget(label)
            self._labels[role] = label
        layout.addStretch(1)

    def show_style(self, style: ViewStyle, translator: Translator) -> None:
        width = max(self.width(), 640)
        for role, label in self._labels.items():
            label.setText(translator.translate(f"hud.sample.{role}"))
            scale = round(style.font_scale * style.text_scale(role) / 100)
            apply_text_size(label, self._bases[role], width, bold=role != "header", scale=scale)


class HudEditor(QWidget):
    """Configure the live HUD: type, shares, visibility and the start-light overlay."""

    def __init__(self, translator: Translator, store: HudConfigurationStore) -> None:
        super().__init__()
        self.setObjectName("race-hud-editor")
        self._translator = translator
        self._store = store
        self._config = store.load()
        self._filling = False
        self._surface = VIEW_LIVE
        self._preview_status = "running"
        self._preview_lanes = 2
        self._preview_mode = RaceMode.LAPS
        self._locking = False
        self._splitter_placed = False
        self.preview = HudPreviewHost(translator, self._place_lights, self._on_handle)
        self.stage = self.preview.stage
        self.lights = self.preview.lights
        self.handle = self.preview.handle
        self._build()
        bind_enter(self, self.save)
        self.stage.apply_layout(self.selected_layout())
        self._fill_form()
        self._show_sample()
        self._place_lights()

    def configuration(self) -> HudConfiguration:
        return self._config

    def selected_layout(self) -> HudLayout:
        return self._config.selected()

    def select_layout(self, layout_id: str) -> None:
        if self._config.layout(layout_id) is None:
            return
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
        self._config = self._store.save(delete_layout(self._config, current.id))
        self._apply_preview()
        self._fill_form()

    def set_font_scale(self, value: int) -> None:
        snapped = snap_scale(value)
        if self._surface == VIEW_LIVE:
            self._commit(replace(self.selected_layout(), font_scale=snapped))
            return
        style = replace(self.selected_layout().view(self._surface), font_scale=snapped)
        self._commit(with_view(self.selected_layout(), self._surface, style))

    def select_view(self, view_id: str) -> None:
        if view_id not in VIEW_IDS:
            return
        self._surface = view_id
        self._apply_preview()
        self._fill_form()

    def set_text_scale(self, role: str, value: int) -> None:
        if role not in TEXT_ROLES or self._surface == VIEW_LIVE:
            return
        current = self.selected_layout().view(self._surface)
        texts = tuple(
            (key, snap_scale(value) if key == role else scale) for key, scale in current.texts
        )
        if role not in {key for key, _scale in texts}:
            texts = (*texts, (role, snap_scale(value)))
        updated = replace(current, texts=texts)
        self._commit(with_view(self.selected_layout(), self._surface, updated))

    def set_panel_display(self, panel_id: str, display: str) -> None:
        mode = snap_display(display)
        current = self.selected_layout().view(VIEW_LIVE)
        panels = tuple((key, mode if key == panel_id else stored) for key, stored in current.panels)
        if panel_id not in {key for key, _mode in panels}:
            panels = (*panels, (panel_id, mode))
        self._commit(with_view(self.selected_layout(), VIEW_LIVE, replace(current, panels=panels)))

    def set_alignment(self, alignment: str) -> None:
        self._commit(replace(self.selected_layout(), alignment=snap_alignment(alignment)))

    def set_field_visible(self, field_id: str, visible: bool) -> None:
        self.set_field_display(field_id, DISPLAY_AUTO if visible else DISPLAY_HIDE)

    def set_field_display(self, field_id: str, display: str) -> None:
        mode = snap_display(display)
        style = self.selected_layout().field(field_id)
        self._commit(
            self._with_field(field_id, replace(style, visible=mode != DISPLAY_HIDE, display=mode))
        )

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
        """Inset the preview when it should use the live margins. Settings keep their width."""
        insets = live_hud_insets(self)
        if self.match_live():
            self._canvas_layout.setContentsMargins(
                insets.left, insets.top, insets.right, insets.bottom
            )
        else:
            self._canvas_layout.setContentsMargins(0, 0, 0, 0)
        self._place_lights()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._lock_settings()
        self._place_splitter()
        self.sync_chrome()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.ApplicationFontChange):
            self._lock_settings()

    def _lock_settings(self) -> None:
        """Give every control the width of its text, then refuse to draw the form narrower.

        A fixed sidebar used to be thinner than those texts. Qt then placed the
        next control on top of the previous one. The form now keeps this width
        and the column scrolls instead.
        """
        if getattr(self, "_locking", False) or not hasattr(self, "_form"):
            return
        self._locking = True
        try:
            for combo in self._form.findChildren(QComboBox):
                combo.setMinimumWidth(_combo_min_width(combo))
            for spin in self._form.findChildren(QSpinBox):
                spin.setMinimumWidth(_spin_min_width(spin))
            for button in self._form.findChildren(QPushButton):
                button.setMinimumWidth(_button_min_width(button))
            for label in self._form.findChildren(QLabel):
                if label.wordWrap() or not label.text():
                    continue
                label.setMinimumWidth(_label_min_width(label))
            layout = self._form.layout()
            if layout is None:
                return
            layout.invalidate()
            needed = max(layout.totalMinimumSize().width(), self._form.minimumSizeHint().width())
            if needed > 0:
                self._form.setMinimumWidth(needed)
            gutter = self._scroll.verticalScrollBar().sizeHint().width()
            self._scroll.setMinimumWidth(min(max(needed, 1), 280) + gutter)
        finally:
            self._locking = False

    def _place_splitter(self) -> None:
        """Give the settings their text width once. Later drags stay with the user."""
        if self._splitter_placed or self._splitter.width() <= 0:
            return
        wanted = self._form.minimumWidth() + self._scroll.frameWidth() * 2
        preview = max(self._splitter.width() - wanted, self.preview.minimumWidth())
        settings = max(self._splitter.width() - preview, self._scroll.minimumWidth())
        self._splitter.setSizes([settings, max(preview, 1)])
        self._splitter_placed = True

    def _build(self) -> None:
        tr = self._translator.translate
        form = QWidget()
        form.setObjectName("hud-editor-form")
        form.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        column = QVBoxLayout(form)
        column.setContentsMargins(SPACE.md, SPACE.md, SPACE.md, SPACE.md)
        column.setSpacing(SPACE.md)
        column.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        self._form = form
        self._groups: dict[str, QGroupBox] = {}
        self._live_groups: list[QWidget] = []
        self._board_only: list[QWidget] = []

        view_form = self._add_group(column, tr("hud.group.view"), "hud-group-view")
        self._match = QCheckBox(tr("hud.match_live"))
        self._match.setObjectName("hud-match-live")
        self._match.setToolTip(tr("hud.match_live.hint"))
        self._match.setChecked(True)
        self._match.toggled.connect(self._on_match)
        view_form.addRow(self._match)
        self._views = QComboBox()
        self._views.setObjectName("hud-view")
        for view_id in VIEW_IDS:
            self._views.addItem(tr(f"hud.view.{view_id}"), view_id)
        self._views.currentIndexChanged.connect(self._on_view)
        view_form.addRow(_form_label(tr("hud.group.view")), self._views)

        layout_form = self._add_group(column, tr("hud.group.layout"), "hud-group-layout")
        self._layouts = QComboBox()
        self._layouts.setObjectName("hud-layout")
        self._layouts.currentIndexChanged.connect(self._on_layout)
        self._name = QLineEdit()
        self._name.setObjectName("hud-layout-name")
        self._name.editingFinished.connect(self._on_name)
        self._default_mark = QLabel()
        self._default_mark.setObjectName("hud-default-mark")
        self._default_mark.setWordWrap(True)
        layout_form.addRow(_form_label(tr("hud.group.layout")), self._layouts)
        layout_form.addRow(_form_label(tr("hud.layout.name")), self._name)
        layout_form.addRow(self._default_mark)
        layout_buttons = self._groups["hud-group-layout"].layout()
        assert layout_buttons is not None
        self._new = _button("hud-layout-new", tr("hud.layout.new"), self._on_new)
        self._delete = _button("hud-layout-delete", tr("hud.layout.delete"), self.delete_selected)
        self._default = _button("hud-set-default", tr("hud.layout.default"), self.mark_default)
        layout_buttons.addWidget(self._new)
        layout_buttons.addWidget(self._delete)
        layout_buttons.addWidget(self._default)

        text_form = self._add_group(column, tr("hud.group.text"), "hud-group-text")
        self._font = _scale_spin("hud-font-scale")
        self._font.valueChanged.connect(self._on_font)
        text_form.addRow(_form_label(tr("hud.font_scale")), self._font)
        self._text_scales: dict[str, QSpinBox] = {}
        for role in TEXT_ROLES:
            scale = _scale_spin(f"hud-text-{role}")
            scale.valueChanged.connect(lambda value, name=role: self._on_text_scale(name, value))
            self._text_scales[role] = scale
            caption = _form_label(tr(f"hud.sample.{role}"))
            text_form.addRow(caption, scale)
            self._board_only.extend((caption, scale))

        alignment_form = self._add_group(column, tr("hud.group.alignment"), "hud-group-alignment")
        self._alignment = QComboBox()
        self._alignment.setObjectName("hud-alignment")
        for key in ("center", "left", "right"):
            self._alignment.addItem(tr(f"hud.alignment.{key}"), key)
        self._alignment.currentIndexChanged.connect(self._on_alignment)
        alignment_form.addRow(_form_label(tr("hud.alignment")), self._alignment)
        self._live_groups.append(self._groups["hud-group-alignment"])

        fields_form = self._add_group(column, tr("hud.group.fields"), "hud-group-fields")
        self._display: dict[str, QComboBox] = {}
        self._scales: dict[str, QSpinBox] = {}
        for field_id in FIELD_IDS:
            combo = QComboBox()
            combo.setObjectName(f"hud-display-{field_id}")
            for mode in DISPLAY_MODES:
                combo.addItem(tr(f"hud.display.{mode}"), mode)
            scale = _scale_spin(f"hud-scale-{field_id}")
            combo.currentIndexChanged.connect(
                lambda _index, field=field_id: self._on_display(field)
            )
            scale.valueChanged.connect(
                lambda value, field=field_id: self._on_field_scale(field, value)
            )
            self._display[field_id] = combo
            self._scales[field_id] = scale
            fields_form.addRow(_form_label(tr(f"hud.field.{field_id}")), _field_pair(combo, scale))
        records = QComboBox()
        records.setObjectName("hud-display-records")
        for mode in DISPLAY_MODES:
            records.addItem(tr(f"hud.display.{mode}"), mode)
        records.currentIndexChanged.connect(self._on_records)
        self._records = records
        fields_form.addRow(_form_label(tr("hud.panel.records")), records)
        self._live_groups.append(self._groups["hud-group-fields"])

        share_form = self._add_group(column, tr("hud.group.shares"), "hud-group-shares")
        self._shares: dict[str, QSpinBox] = {}
        for part in ("clock", "status", "lanes", "ranking"):
            spin = QSpinBox()
            spin.setObjectName(f"hud-share-{part}")
            spin.setRange(SHARE_MIN, SHARE_MAX)
            spin.valueChanged.connect(lambda value, name=part: self._on_share(name, value))
            self._shares[part] = spin
            share_form.addRow(_form_label(tr(f"hud.share.{part}")), spin)
        self._live_groups.append(self._groups["hud-group-shares"])

        lights_form = self._add_group(column, tr("hud.group.lights"), "hud-group-lights")
        self._lights_visible = QCheckBox(tr("hud.lights.visible"))
        self._lights_visible.setObjectName("hud-lights-visible")
        self._lights_visible.toggled.connect(self._on_lights_visible)
        lights_form.addRow(self._lights_visible)
        self._light_spins: dict[str, QSpinBox] = {}
        for axis in ("x", "y"):
            spin = QSpinBox()
            spin.setObjectName(f"hud-lights-{axis}")
            spin.setSuffix(" %")
            spin.setRange(0, 100)
            spin.valueChanged.connect(self._on_light_spin)
            self._light_spins[axis] = spin
            lights_form.addRow(_form_label(tr(f"hud.lights.{axis}")), spin)
        self._light_scale = QSlider(Qt.Orientation.Horizontal)
        self._light_scale.setObjectName("hud-lights-scale")
        self._light_scale.setRange(20, 200)
        self._light_scale.setMinimumWidth(80)
        self._light_scale.valueChanged.connect(self._on_light_scale)
        self._light_readout = QLabel("100 %")
        self._light_readout.setObjectName("hud-lights-scale-readout")
        lights_form.addRow(
            _form_label(tr("hud.lights.scale")),
            _field_pair(self._light_scale, self._light_readout),
        )
        reset_lights = _button("hud-lights-reset", tr("hud.lights.reset"), self.reset_lights)
        lights_layout = self._groups["hud-group-lights"].layout()
        assert lights_layout is not None
        lights_layout.addWidget(reset_lights)
        self._live_groups.append(self._groups["hud-group-lights"])

        preview_form = self._add_group(column, tr("hud.group.preview"), "hud-group-preview")
        self._status = QComboBox()
        self._status.setObjectName("hud-preview-status")
        for key, label_key in _PREVIEW_STATUSES:
            self._status.addItem(tr(label_key), key)
        self._status.currentIndexChanged.connect(self._on_preview_status)
        preview_form.addRow(_form_label(tr("hud.preview.status")), self._status)
        self._preview_mode_combo = QComboBox()
        self._preview_mode_combo.setObjectName("hud-preview-mode")
        self._preview_mode_combo.addItem(tr("race.wizard.mode.laps"), RaceMode.LAPS.value)
        self._preview_mode_combo.addItem(
            tr("race.wizard.mode.time_trial"), RaceMode.TIME_TRIAL.value
        )
        self._preview_mode_combo.currentIndexChanged.connect(self._on_preview_mode)
        preview_form.addRow(_form_label(tr("hud.preview.mode")), self._preview_mode_combo)
        self._lanes = QSpinBox()
        self._lanes.setObjectName("hud-preview-lanes")
        self._lanes.setRange(2, 4)
        self._lanes.valueChanged.connect(self._on_preview_lanes)
        preview_form.addRow(_form_label(tr("hud.preview.lanes")), self._lanes)
        self._live_groups.append(self._groups["hud-group-preview"])

        self._add_group(column, tr("hud.group.actions"), "hud-group-actions")
        action_buttons = self._groups["hud-group-actions"].layout()
        assert action_buttons is not None
        for button in (
            _button("hud-standard", tr("hud.standard"), self.apply_standard_layout),
            _button("hud-save-close", tr("hud.save_close"), self.save_and_close),
            _button("hud-revert", tr("hud.revert"), self.revert),
        ):
            action_buttons.addWidget(button)
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("hud-editor-sidebar")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(form)
        scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        scroll.setMinimumWidth(280)
        self._scroll = scroll

        canvas = QWidget()
        canvas.setObjectName("hud-live-canvas")
        canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        canvas.setMinimumWidth(self.preview.minimumWidth())
        canvas_layout = QVBoxLayout(canvas)
        canvas_layout.setSpacing(0)
        self.sample = SurfaceSample()
        canvas_layout.addWidget(self.preview, 1)
        canvas_layout.addWidget(self.sample, 1)
        self.sample.hide()
        self._canvas_layout = canvas_layout

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("hud-editor-splitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.addWidget(scroll)
        splitter.addWidget(canvas)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self._splitter = splitter
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter)
        self.sync_chrome()

    def _add_group(self, column: QVBoxLayout, title: str, object_name: str) -> QFormLayout:
        box = QGroupBox(title)
        box.setObjectName(object_name)
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(box)
        outer.setContentsMargins(SPACE.md, SPACE.lg, SPACE.md, SPACE.md)
        outer.setSpacing(SPACE.sm)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setHorizontalSpacing(SPACE.md)
        form.setVerticalSpacing(SPACE.sm)
        outer.addLayout(form)
        column.addWidget(box)
        self._groups[object_name] = box
        return form

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
        live = self._surface == VIEW_LIVE
        self.preview.setVisible(live)
        self.sample.setVisible(not live)
        if not live:
            self.lights.hide()
            self.handle.hide()
            self.sample.show_style(self.selected_layout().view(self._surface), self._translator)
            return
        self.stage.apply_layout(self.selected_layout())
        self._show_sample()
        self._place_lights()

    def _show_sample(self) -> None:
        snapshot, message_key = _sample(self._preview_status, self._preview_lanes)
        duration = 5 if self._preview_mode is RaceMode.TIME_TRIAL else None
        self.stage.show_standings(
            snapshot,
            mode=self._preview_mode,
            lane_count=self._preview_lanes,
            duration_minutes=duration,
        )
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
            live = self._surface == VIEW_LIVE
            for widget in self._live_groups:
                widget.setVisible(live)
            for widget in self._board_only:
                widget.setVisible(not live)
            self._lock_settings()
            view_index = self._views.findData(self._surface)
            self._views.setCurrentIndex(max(view_index, 0))
            surface = layout.view(self._surface)
            self._font.setValue(layout.font_scale if live else surface.font_scale)
            alignment = self._alignment.findData(layout.alignment)
            self._alignment.setCurrentIndex(max(alignment, 0))
            for role, spin in self._text_scales.items():
                spin.setValue(surface.text_scale(role))
            for field_id, combo in self._display.items():
                style = layout.field(field_id)
                mode = DISPLAY_HIDE if not style.visible else style.display
                combo.setCurrentIndex(max(combo.findData(mode), 0))
                self._scales[field_id].setValue(style.scale)
            records = layout.view(VIEW_LIVE).panel(PANEL_RECORDS)
            self._records.setCurrentIndex(max(self._records.findData(records), 0))
            mode_index = self._preview_mode_combo.findData(self._preview_mode.value)
            self._preview_mode_combo.setCurrentIndex(max(mode_index, 0))
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
        if frame.visible:
            if self.lights.isHidden():
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
        current = (
            self.selected_layout().font_scale
            if self._surface == VIEW_LIVE
            else self.selected_layout().view(self._surface).font_scale
        )
        if snapped != current:
            self.set_font_scale(snapped)

    def _on_alignment(self) -> None:
        if self._filling:
            return
        alignment = self._alignment.currentData()
        if isinstance(alignment, str):
            self.set_alignment(alignment)

    def _on_view(self) -> None:
        if self._filling:
            return
        view_id = self._views.currentData()
        if isinstance(view_id, str):
            self.select_view(view_id)

    def _on_display(self, field_id: str) -> None:
        if self._filling:
            return
        mode = self._display[field_id].currentData()
        if not isinstance(mode, str):
            return
        style = self.selected_layout().field(field_id)
        shown = DISPLAY_HIDE if not style.visible else style.display
        if mode != shown:
            self.set_field_display(field_id, mode)

    def _on_text_scale(self, role: str, value: int) -> None:
        if self._filling or self._surface == VIEW_LIVE:
            return
        snapped = snap_scale(value)
        if snapped != value:
            self._text_scales[role].setValue(snapped)
            return
        if snapped != self.selected_layout().view(self._surface).text_scale(role):
            self.set_text_scale(role, snapped)

    def _on_records(self) -> None:
        if self._filling:
            return
        mode = self._records.currentData()
        current = self.selected_layout().view(VIEW_LIVE).panel(PANEL_RECORDS)
        if isinstance(mode, str) and mode != current:
            self.set_panel_display(PANEL_RECORDS, mode)

    def _on_preview_mode(self) -> None:
        if self._filling:
            return
        value = self._preview_mode_combo.currentData()
        self._preview_mode = RaceMode.LAPS if value is None else RaceMode(str(value))
        self._show_sample()

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


def _form_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
    return label


def _field_pair(primary: QWidget, secondary: QWidget) -> QWidget:
    """One field: the main control grows, the second keeps the width of its text."""
    host = QWidget()
    line = QHBoxLayout(host)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(SPACE.sm)
    primary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    secondary.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    line.addWidget(primary, 1)
    line.addWidget(secondary, 0)
    return host


def _combo_min_width(combo: QComboBox) -> int:
    combo.ensurePolished()
    metrics = combo.fontMetrics()
    widest = 0
    for index in range(combo.count()):
        widest = max(widest, metrics.horizontalAdvance(combo.itemText(index)))
    dropdown = combo.style().pixelMetric(QStyle.PixelMetric.PM_MenuButtonIndicator, None, combo)
    frame = combo.style().pixelMetric(QStyle.PixelMetric.PM_ComboBoxFrameWidth, None, combo)
    return widest + dropdown + frame * 2 + SPACE.md


def _spin_min_width(spin: QSpinBox) -> int:
    spin.ensurePolished()
    sample = f"{spin.prefix()}{spin.textFromValue(spin.maximum())}{spin.suffix()}"
    text = spin.fontMetrics().horizontalAdvance(sample)
    arrow = spin.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, spin)
    frame = spin.style().pixelMetric(QStyle.PixelMetric.PM_SpinBoxFrameWidth, None, spin)
    return text + arrow + frame * 2 + SPACE.sm


def _button_min_width(button: QPushButton) -> int:
    button.ensurePolished()
    text = button.fontMetrics().horizontalAdvance(button.text())
    return text + _BUTTON_CHROME + _DIALOG_TEXT_SPARE


def _label_min_width(label: QLabel) -> int:
    label.ensurePolished()
    return label.fontMetrics().horizontalAdvance(label.text()) + SPACE.xs


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
