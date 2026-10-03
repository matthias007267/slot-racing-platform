"""Places HUD panels from a normalized configuration. Pixels are computed from the current size."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from slot_racing.modules.races.hud import (
    HudConfiguration,
    default_hud_configuration,
    stacking_order,
    to_pixels,
)


class HudStage(QWidget):
    """The race display. A later fullscreen window can host this same stage."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("hud-stage")
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._config = default_hud_configuration()
        self._panels: dict[str, QWidget] = {}

    @property
    def configuration(self) -> HudConfiguration:
        return self._config

    def bind(self, widget_id: str, panel: QWidget) -> None:
        panel.setParent(self)
        self._panels[widget_id] = panel

    def apply(self, configuration: HudConfiguration) -> None:
        self._config = configuration
        self.relayout()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout()

    def relayout(self) -> None:
        width = self.width()
        height = self.height()
        known = {item.id for item in self._config.widgets}
        for widget_id, panel in self._panels.items():
            if widget_id not in known:
                panel.setVisible(False)
        for item in stacking_order(self._config.widgets):
            placed = self._panels.get(item.id)
            if placed is None:
                continue
            placed.setVisible(item.visible)
            if not item.visible or width <= 0 or height <= 0:
                continue
            rect = to_pixels(item, width, height)
            placed.setGeometry(rect.x, rect.y, rect.width, rect.height)
            placed.raise_()
