"""Settings editor for the race HUD. Drag and resize write the same configuration."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import (
    WIDGET_IDS,
    HudConfiguration,
    HudConfigurationStore,
    HudWidgetConfig,
    default_hud_configuration,
    stacking_order,
    to_pixels,
    with_widget,
)
from slot_racing.uikit.theme import SPACE, configure_page, polish, set_role

_HANDLE = 12
_GRID_X = 16
_GRID_Y = 9
_PREVIEW_OPACITY = 0.9


def snap_axis(value: float, parts: int) -> float:
    """Nearest grid line. ``parts`` is the number of cells along that axis."""
    return round(value * parts) / parts


def _paint_grid(
    painter: QPainter,
    canvas_width: int,
    canvas_height: int,
    origin_x: int,
    origin_y: int,
    view_width: int,
    view_height: int,
) -> None:
    """Draw the canvas grid through one rectangle. ``origin`` is that rectangle's top left."""
    if canvas_width <= 1 or canvas_height <= 1 or view_width <= 0 or view_height <= 0:
        return
    minor = QPen(QColor(255, 255, 255, 90))
    major = QPen(QColor(255, 196, 64, 220))
    for index in range(_GRID_X + 1):
        x = round(index * canvas_width / _GRID_X) - origin_x
        if 0 <= x <= view_width:
            painter.setPen(major if index in (0, _GRID_X // 2, _GRID_X) else minor)
            painter.drawLine(x, 0, x, view_height)
    for index in range(_GRID_Y + 1):
        y = round(index * canvas_height / _GRID_Y) - origin_y
        if 0 <= y <= view_height:
            painter.setPen(major if index in (0, _GRID_Y) else minor)
            painter.drawLine(0, y, view_width, y)
    center_y = canvas_height // 2 - origin_y
    if 0 <= center_y <= view_height:
        painter.setPen(major)
        painter.drawLine(0, center_y, view_width, center_y)


class HudEditor(QWidget):
    """Preview plus the element list. Standard loads the factory layout; save writes it."""

    def __init__(self, translator: Translator, store: HudConfigurationStore) -> None:
        super().__init__()
        self.setObjectName("race-hud-editor")
        self._translator = translator
        self._store = store
        self._config = store.load()
        self._selected: str | None = LIVE_RANKING_DEFAULT
        self._checks: dict[str, QCheckBox] = {}
        self._fullscreen: HudPreviewWindow | None = None
        tr = translator.translate

        self.preview = HudPreview(translator, self)
        self._previews: list[HudPreview] = [self.preview]

        layout = QVBoxLayout(self)
        configure_page(layout)
        preview_title = QLabel(tr("hud.preview"))
        set_role(preview_title, "card-title")
        layout.addWidget(preview_title)
        layout.addWidget(self.preview)

        elements = QLabel(tr("hud.elements"))
        set_role(elements, "section")
        layout.addWidget(elements)
        for widget_id in WIDGET_IDS:
            layout.addWidget(self._element_row(widget_id))

        layout.addLayout(self._actions())
        self._set_config(self._config)

    def configuration(self) -> HudConfiguration:
        return self._config

    @property
    def selected_id(self) -> str | None:
        return self._selected

    @property
    def fullscreen(self) -> HudPreviewWindow | None:
        return self._fullscreen

    def box(self, widget_id: str) -> HudBox | None:
        return self.preview.box(widget_id)

    def select(self, widget_id: str) -> None:
        if self._config.widget(widget_id) is None:
            return
        self._selected = widget_id
        self._show_selection()

    def set_visible(self, widget_id: str, visible: bool) -> None:
        current = self._require(widget_id)
        self._replace(replace(current, visible=visible))

    def set_widget_geometry(
        self, widget_id: str, x: float, y: float, width: float, height: float
    ) -> None:
        current = self._require(widget_id)
        self._replace(replace(current, x=x, y=y, width=width, height=height))

    def bring_forward(self) -> None:
        current = self._selected_widget()
        if current is None:
            return
        top = max(item.z_index for item in self._config.widgets)
        self._replace(replace(current, z_index=top + 1))

    def send_backward(self) -> None:
        current = self._selected_widget()
        if current is None:
            return
        bottom = min(item.z_index for item in self._config.widgets)
        self._replace(replace(current, z_index=bottom - 1))

    def apply_standard_layout(self) -> None:
        """Show the factory layout in the editor. It is stored only when the user saves."""
        self._set_config(default_hud_configuration())

    def revert(self) -> None:
        """Discard unsaved edits and show the last stored layout."""
        self._set_config(self._store.load())

    def save(self) -> HudConfiguration:
        stored = self._store.save(self._config)
        self._set_config(stored)
        return stored

    def save_persistent(self) -> None:
        """Write the layout that is on screen, including edits that were not saved yet."""
        self.save()

    def open_fullscreen_preview(self) -> HudPreviewWindow:
        """Open the layout in its own fullscreen view, at the screen's aspect ratio."""
        if self._fullscreen is not None:
            self._fullscreen.raise_()
            self._fullscreen.activateWindow()
            return self._fullscreen
        window = HudPreviewWindow(self._translator, self)
        self._fullscreen = window
        self.add_preview(window.preview)
        window.showFullScreen()
        return window

    def add_preview(self, preview: HudPreview) -> None:
        if preview not in self._previews:
            self._previews.append(preview)
        preview.apply(self._config)
        preview.set_selected(self._selected)

    def note_preview_closed(self, window: HudPreviewWindow) -> None:
        if window.preview in self._previews and window.preview is not self.preview:
            self._previews.remove(window.preview)
        if self._fullscreen is window:
            self._fullscreen = None

    def _element_row(self, widget_id: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        checkbox = QCheckBox(self._title(widget_id))
        checkbox.setObjectName(f"hud-visible-{widget_id}")
        checkbox.toggled.connect(lambda checked, name=widget_id: self.set_visible(name, checked))
        choose = QPushButton(self._translator.translate("hud.edit"))
        choose.setObjectName(f"hud-select-{widget_id}")
        set_role(choose, "ghost")
        choose.clicked.connect(lambda _checked=False, name=widget_id: self.select(name))
        layout.addWidget(checkbox, 1)
        layout.addWidget(choose)
        self._checks[widget_id] = checkbox
        return row

    def _actions(self) -> QHBoxLayout:
        translate = self._translator.translate
        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE.sm)
        preview = _action("hud-open-preview", translate("hud.open_preview"), "primary")
        standard = _action("hud-standard", translate("hud.standard"), "secondary")
        save = _action("hud-save", translate("hud.save"), "primary")
        revert = _action("hud-revert", translate("hud.revert"), "ghost")
        forward = _action("hud-forward", translate("hud.forward"), "secondary")
        backward = _action("hud-backward", translate("hud.backward"), "secondary")
        preview.clicked.connect(self.open_fullscreen_preview)
        standard.clicked.connect(self.apply_standard_layout)
        save.clicked.connect(self.save)
        revert.clicked.connect(self.revert)
        forward.clicked.connect(self.bring_forward)
        backward.clicked.connect(self.send_backward)
        for button in (preview, standard, save, revert, forward, backward):
            buttons.addWidget(button)
        buttons.addStretch(1)
        return buttons

    def _title(self, widget_id: str) -> str:
        return self._translator.translate(f"hud.widget.{widget_id}")

    def _require(self, widget_id: str) -> HudWidgetConfig:
        current = self._config.widget(widget_id)
        if current is None:
            raise KeyError(widget_id)
        return current

    def _selected_widget(self) -> HudWidgetConfig | None:
        if self._selected is None:
            return None
        return self._config.widget(self._selected)

    def _replace(self, widget: HudWidgetConfig) -> None:
        self._config = with_widget(self._config, widget)
        self._refresh_checks()
        self._show_config()

    def _set_config(self, config: HudConfiguration) -> None:
        self._config = config
        if self._selected is None or config.widget(self._selected) is None:
            self._selected = config.widgets[0].id if config.widgets else None
        self._refresh_checks()
        self._show_config()

    def _show_config(self) -> None:
        for preview in self._previews:
            preview.apply(self._config)
            preview.set_selected(self._selected)

    def _show_selection(self) -> None:
        for preview in self._previews:
            preview.set_selected(self._selected)

    def _refresh_checks(self) -> None:
        for widget_id, checkbox in self._checks.items():
            item = self._config.widget(widget_id)
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(item and item.visible))
            checkbox.blockSignals(False)


LIVE_RANKING_DEFAULT = "live_ranking"


class HudPreviewWindow(QDialog):
    """Fullscreen editing view at the screen resolution. It is not the live race."""

    def __init__(self, translator: Translator, editor: HudEditor) -> None:
        super().__init__(editor)
        self.setObjectName("hud-fullscreen-preview")
        self.setWindowTitle(translator.translate("hud.preview"))
        self.setModal(False)
        self.setWindowOpacity(_PREVIEW_OPACITY)
        self._editor = editor
        self.preview = HudPreview(translator, editor)
        self.preview.setObjectName("hud-fullscreen-canvas")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.preview)
        self.caption = QLabel(translator.translate("hud.preview_hint"), self)
        self.caption.setObjectName("hud-preview-hint")
        set_role(self.caption, "caption")
        self.close_button = QPushButton(translator.translate("hud.preview_close"), self)
        self.close_button.setObjectName("hud-preview-close")
        set_role(self.close_button, "secondary")
        self.close_button.clicked.connect(self.close)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.caption.adjustSize()
        self.close_button.adjustSize()
        self.caption.move(16, 12)
        self.close_button.move(max(16, self.width() - self.close_button.width() - 16), 8)
        self.caption.raise_()
        self.close_button.raise_()

    def done(self, result: int) -> None:
        self._editor.note_preview_closed(self)
        super().done(result)


class HudPreview(QWidget):
    """Scaled canvas. Hidden elements stay out of the picture until they are enabled again."""

    def __init__(self, translator: Translator, editor: HudEditor) -> None:
        super().__init__()
        self.setObjectName("hud-preview")
        self.setMinimumHeight(200)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._editor = editor
        self._translator = translator
        self._boxes: dict[str, HudBox] = {}
        self._config = default_hud_configuration()
        self.grid = HudGrid(self)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return max(180, int(width * 9 / 16))

    def box(self, widget_id: str) -> HudBox | None:
        return self._boxes.get(widget_id)

    def apply(self, config: HudConfiguration) -> None:
        self._config = config
        ids = {item.id for item in config.widgets}
        for extra in list(self._boxes):
            if extra not in ids:
                self._boxes.pop(extra).deleteLater()
        for item in config.widgets:
            box = self._boxes.get(item.id)
            if box is None:
                title = self._translator.translate(f"hud.widget.{item.id}")
                box = HudBox(item.id, title, self)
                self._boxes[item.id] = box
            box.setVisible(item.visible)
        self.relayout()

    def set_selected(self, widget_id: str | None) -> None:
        for box_id, box in self._boxes.items():
            active = box_id == widget_id
            box.setProperty("active", "true" if active else "false")
            polish(box)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout()

    def relayout(self) -> None:
        width = self.width()
        height = self.height()
        for item in stacking_order(self._config.widgets):
            box = self._boxes.get(item.id)
            if box is None or not item.visible or width <= 0 or height <= 0:
                continue
            rect = to_pixels(item, width, height)
            box.setGeometry(rect.x, rect.y, rect.width, rect.height)
            box.raise_()
        self.grid.setGeometry(0, 0, max(width, 0), max(height, 0))
        self.grid.lower()
        self.grid.update()

    def move_widget(self, widget_id: str, x: float, y: float, width: float, height: float) -> None:
        self._editor.set_widget_geometry(
            widget_id,
            snap_axis(x, _GRID_X),
            snap_axis(y, _GRID_Y),
            snap_axis(width, _GRID_X),
            snap_axis(height, _GRID_Y),
        )

    def select_widget(self, widget_id: str) -> None:
        self._editor.select(widget_id)


class HudGrid(QWidget):
    """Alignment grid. Mouse events pass through to the elements underneath."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("hud-grid")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        parent = self.parentWidget()
        canvas_width = parent.width() if parent is not None else self.width()
        canvas_height = parent.height() if parent is not None else self.height()
        _paint_grid(
            painter, canvas_width, canvas_height, self.x(), self.y(), self.width(), self.height()
        )


class HudBox(QFrame):
    """One element in the preview. A drag moves it; the corner changes its size."""

    def __init__(self, widget_id: str, title: str, preview: HudPreview) -> None:
        super().__init__(preview)
        self.widget_id = widget_id
        self._preview = preview
        self.setObjectName(f"hud-box-{widget_id}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(0, 0)
        self.setMouseTracking(True)
        set_role(self, "hud-box")
        self._title = QLabel(title, self)
        self._title.setWordWrap(True)
        self._title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        set_role(self._title, "caption")
        self._handle = QFrame(self)
        self._handle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        set_role(self._handle, "hud-handle")
        self._press: tuple[float, float] | None = None
        self._origin: tuple[float, float, float, float] | None = None
        self._resizing = False

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        _paint_grid(
            painter,
            self._preview.width(),
            self._preview.height(),
            self.x(),
            self.y(),
            self.width(),
            self.height(),
        )

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._title.setGeometry(6, 4, max(0, self.width() - 16), max(0, self.height() - 16))
        self._handle.setGeometry(
            max(0, self.width() - _HANDLE), max(0, self.height() - _HANDLE), 10, 10
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        widget = self._preview._config.widget(self.widget_id)
        if widget is None:
            return
        self._preview.select_widget(self.widget_id)
        self._press = (event.globalPosition().x(), event.globalPosition().y())
        self._origin = (widget.x, widget.y, widget.width, widget.height)
        local = event.position()
        near_right = local.x() >= self.width() - _HANDLE
        near_bottom = local.y() >= self.height() - _HANDLE
        self._resizing = near_right and near_bottom
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._press is None or self._origin is None:
            return
        parent = self._preview
        dx = (event.globalPosition().x() - self._press[0]) / max(1, parent.width())
        dy = (event.globalPosition().y() - self._press[1]) / max(1, parent.height())
        x, y, width, height = self._origin
        if self._resizing:
            self._preview.move_widget(self.widget_id, x, y, width + dx, height + dy)
        else:
            self._preview.move_widget(self.widget_id, x + dx, y + dy, width, height)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._press = None
        self._origin = None
        self._resizing = False
        event.accept()


def _action(object_name: str, text: str, role: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    set_role(button, role)
    return button
