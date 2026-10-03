"""Settings editor for the race HUD. Drag, resize and numbers all write the same configuration."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
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
_SPIN_KEYS = ("x", "y", "width", "height")


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
        tr = translator.translate

        self.preview = HudPreview(translator, self)
        self._spins = {key: _spin(key) for key in _SPIN_KEYS}
        for spin in self._spins.values():
            spin.valueChanged.connect(self._on_spin)

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

        layout.addWidget(self._geometry_row())
        layout.addLayout(self._actions())
        self._set_config(self._config)

    def configuration(self) -> HudConfiguration:
        return self._config

    @property
    def selected_id(self) -> str | None:
        return self._selected

    def box(self, widget_id: str) -> HudBox | None:
        return self.preview.box(widget_id)

    def select(self, widget_id: str) -> None:
        if self._config.widget(widget_id) is None:
            return
        self._selected = widget_id
        self.preview.set_selected(widget_id)
        self._sync_spins()

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

    def _geometry_row(self) -> QWidget:
        translate = self._translator.translate
        frame = QFrame()
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_role(frame, "card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(SPACE.md, SPACE.md, SPACE.md, SPACE.md)
        position = QLabel(translate("hud.position"))
        set_role(position, "card-title")
        layout.addWidget(position)
        layout.addLayout(self._spin_line(("x", "X"), ("y", "Y")))
        size = QLabel(translate("hud.size"))
        set_role(size, "card-title")
        layout.addWidget(size)
        layout.addLayout(
            self._spin_line(("width", translate("hud.width")), ("height", translate("hud.height")))
        )
        return frame

    def _spin_line(self, left: tuple[str, str], right: tuple[str, str]) -> QHBoxLayout:
        line = QHBoxLayout()
        line.setSpacing(SPACE.sm)
        for key, label in (left, right):
            caption = QLabel(label)
            set_role(caption, "caption")
            line.addWidget(caption)
            line.addWidget(self._spins[key], 1)
        return line

    def _actions(self) -> QHBoxLayout:
        translate = self._translator.translate
        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE.sm)
        standard = _action("hud-standard", translate("hud.standard"), "secondary")
        save = _action("hud-save", translate("hud.save"), "primary")
        revert = _action("hud-revert", translate("hud.revert"), "ghost")
        forward = _action("hud-forward", translate("hud.forward"), "secondary")
        backward = _action("hud-backward", translate("hud.backward"), "secondary")
        standard.clicked.connect(self.apply_standard_layout)
        save.clicked.connect(self.save)
        revert.clicked.connect(self.revert)
        forward.clicked.connect(self.bring_forward)
        backward.clicked.connect(self.send_backward)
        for button in (standard, save, revert, forward, backward):
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
        self.preview.apply(self._config)
        self.preview.set_selected(self._selected)
        if self._selected == widget.id:
            self._sync_spins()

    def _set_config(self, config: HudConfiguration) -> None:
        self._config = config
        if self._selected is None or config.widget(self._selected) is None:
            self._selected = config.widgets[0].id if config.widgets else None
        self._refresh_checks()
        self.preview.apply(config)
        self.preview.set_selected(self._selected)
        self._sync_spins()

    def _refresh_checks(self) -> None:
        for widget_id, checkbox in self._checks.items():
            item = self._config.widget(widget_id)
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(item and item.visible))
            checkbox.blockSignals(False)

    def _sync_spins(self) -> None:
        widget = self._selected_widget()
        for spin in self._spins.values():
            spin.setEnabled(widget is not None)
        if widget is None:
            return
        values = {
            "x": widget.x,
            "y": widget.y,
            "width": widget.width,
            "height": widget.height,
        }
        for key, spin in self._spins.items():
            spin.blockSignals(True)
            spin.setValue(values[key])
            spin.blockSignals(False)

    def _on_spin(self, _value: float) -> None:
        if self._selected is None:
            return
        self.set_widget_geometry(
            self._selected,
            self._spins["x"].value(),
            self._spins["y"].value(),
            self._spins["width"].value(),
            self._spins["height"].value(),
        )


LIVE_RANKING_DEFAULT = "live_ranking"


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

    def move_widget(self, widget_id: str, x: float, y: float, width: float, height: float) -> None:
        self._editor.set_widget_geometry(widget_id, x, y, width, height)

    def select_widget(self, widget_id: str) -> None:
        self._editor.select(widget_id)


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


def _spin(key: str) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setObjectName(f"hud-{key}")
    spin.setDecimals(2)
    spin.setSingleStep(0.01)
    spin.setRange(0.0, 1.0)
    return spin


def _action(object_name: str, text: str, role: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    set_role(button, role)
    return button
