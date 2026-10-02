"""Built-in pages of the shell."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from slot_racing.app.runtime import Runtime
from slot_racing.core.plugin import PluginError, PluginState
from slot_racing.uikit.theme import configure_page, set_role, set_tone


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
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
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
        self._layout.addWidget(modules)
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
            self._layout.addWidget(checkbox)
        self._layout.addWidget(self._message)

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

    def _rebuild(self) -> None:
        while (item := self._layout.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None and widget is not self._message:
                widget.setParent(None)
                widget.deleteLater()
        self._checkboxes.clear()
        self._build()
