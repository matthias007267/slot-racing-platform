"""Built-in pages of the shell."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from slot_racing.app.runtime import Runtime
from slot_racing.core.plugin import PluginError, PluginState


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSize(font.pointSize() + 6)
    font.setBold(True)
    label.setFont(font)
    return label


class MessagePage(QWidget):
    """Centered message, used for modules without a page yet and for failed pages."""

    def __init__(self, title: str, message: str, detail: str | None = None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(_heading(title))
        body = QLabel(message if detail is None else f"{message}\n\n{detail}")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(body, 1)


class DashboardPage(QWidget):
    def __init__(self, runtime: Runtime) -> None:
        super().__init__()
        self._runtime = runtime
        layout = QVBoxLayout(self)
        layout.addWidget(_heading(runtime.translator.translate("dashboard.heading")))
        self._modules = QLabel()
        self._modules.setObjectName("dashboard-modules")
        self._modules.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._modules, 1)
        self.refresh()

    def refresh(self) -> None:
        tr = self._runtime.translator.translate
        enabled = [
            f"{status.title} ({status.version})"
            for status in self._runtime.plugins.statuses()
            if status.state is PluginState.ENABLED
        ]
        if enabled:
            text = (
                tr("dashboard.active_modules") + ":\n" + "\n".join(f"- {line}" for line in enabled)
            )
        else:
            text = tr("dashboard.no_modules")
        self._modules.setText(text)


class SettingsPage(QWidget):
    """Lets the user switch modules on and off at runtime."""

    def __init__(self, runtime: Runtime, on_plugins_changed: Callable[[], None]) -> None:
        super().__init__()
        self._runtime = runtime
        self._on_plugins_changed = on_plugins_changed
        self._layout = QVBoxLayout(self)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._message = QLabel()
        self._message.setObjectName("settings-message")
        self._message.setWordWrap(True)
        self._build()

    def checkbox(self, plugin_name: str) -> QCheckBox:
        return self._checkboxes[plugin_name]

    def _build(self) -> None:
        tr = self._runtime.translator.translate
        self._layout.addWidget(_heading(tr("settings.heading")))
        self._layout.addWidget(QLabel(tr("settings.modules") + ":"))
        for status in self._runtime.plugins.statuses():
            state = tr(f"settings.state.{status.state.value}")
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
        self._on_plugins_changed()

    def _rebuild(self) -> None:
        while (item := self._layout.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None and widget is not self._message:
                widget.deleteLater()
        self._checkboxes.clear()
        self._build()
