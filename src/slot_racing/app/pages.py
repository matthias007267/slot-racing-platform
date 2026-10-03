"""Built-in pages of the shell."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QScrollArea, QVBoxLayout, QWidget

from slot_racing.app.runtime import Runtime
from slot_racing.core.plugin import PluginError, PluginState, SettingsSection
from slot_racing.uikit.theme import configure_page, set_role, set_tone

logger = logging.getLogger(__name__)


class MessagePage(QWidget):
    """Message used for modules without a page yet and for failed pages.

    The shell header already shows the page title, so it is not repeated here.
    """

    def __init__(self, title: str, message: str, detail: str | None = None) -> None:
        super().__init__()
        self.setAccessibleDescription(title)
        layout = QVBoxLayout(self)
        configure_page(layout)
        body = QLabel(message if detail is None else f"{message}\n\n{detail}")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        set_role(body, "caption")
        layout.addWidget(body, 1)


class SettingsPage(QWidget):
    """Lets the user switch modules on and off at runtime."""

    def __init__(self, runtime: Runtime, on_plugins_changed: Callable[[], None]) -> None:
        super().__init__()
        self._runtime = runtime
        self._on_plugins_changed = on_plugins_changed
        self._layout = QVBoxLayout(self)
        configure_page(self._layout)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("settings-scroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._body = QWidget()
        self._body.setObjectName("settings-body")
        self._body_layout = QVBoxLayout(self._body)
        configure_page(self._body_layout)
        self._body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._body)
        self._layout.addWidget(self._scroll)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._message = QLabel()
        self._message.setObjectName("settings-message")
        self._message.setWordWrap(True)
        self._build()

    def checkbox(self, plugin_name: str) -> QCheckBox:
        return self._checkboxes[plugin_name]

    def _build(self) -> None:
        translate = self._runtime.translator.translate
        modules = QLabel(translate("settings.modules") + ":")
        set_role(modules, "section")
        self._body_layout.addWidget(modules)
        for status in self._runtime.plugins.statuses():
            state = translate(f"settings.state.{status.state.value}")
            checkbox = QCheckBox(f"{status.title} ({state})")
            checkbox.setChecked(status.state is PluginState.ENABLED)
            if status.error:
                checkbox.setToolTip(status.error)
            if status.version:
                checkbox.toggled.connect(
                    lambda checked, name=status.name: self._toggle(name, checked)
                )
            else:
                checkbox.setEnabled(False)
            self._checkboxes[status.name] = checkbox
            self._body_layout.addWidget(checkbox)
        for section in self._runtime.contributions.settings_sections():
            self._add_section(section)
        self._body_layout.addWidget(self._message)

    def _toggle(self, name: str, enabled: bool) -> None:
        message = ""
        try:
            self._runtime.set_plugin_enabled(name, enabled)
        except PluginError as error:
            message = str(error)
        self._rebuild()
        self._message.setText(message)
        set_tone(self._message, "error" if message else "")
        self._on_plugins_changed()

    def _add_section(self, section: SettingsSection) -> None:
        title = QLabel(self._runtime.translator.translate(section.title_key))
        title.setObjectName(f"settings-section-{section.id}")
        set_role(title, "section")
        self._body_layout.addWidget(title)
        if section.factory is None:
            return
        try:
            widget = section.factory()
        except Exception as error:
            logger.exception("Could not build settings section %s", section.id)
            failed = QLabel(str(error))
            failed.setWordWrap(True)
            set_tone(failed, "error")
            self._body_layout.addWidget(failed)
            return
        if not isinstance(widget, QWidget):
            logger.error("Settings section %s did not return a widget", section.id)
            return
        self._body_layout.addWidget(widget)

    def _rebuild(self) -> None:
        while (item := self._body_layout.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None and widget is not self._message:
                widget.setParent(None)
                widget.deleteLater()
        self._checkboxes.clear()
        self._build()
