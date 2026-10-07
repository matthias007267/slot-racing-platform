"""Main window: sidebar, header and a page stack built from module contributions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from slot_racing.app.dashboard import DashboardPage
from slot_racing.app.diagnostics_ui import (
    DiagnosticsSettings,
    bind_crash_dialog,
    offer_previous_session,
)
from slot_racing.app.pages import MessagePage, SettingsPage
from slot_racing.app.runtime import Runtime
from slot_racing.app.shell import ShellHeader, Sidebar, timing_state
from slot_racing.core.config import save_config
from slot_racing.core.diagnostics import record
from slot_racing.core.events import PluginDisabled, PluginEnabled
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import SPACE, apply_theme, set_role

logger = logging.getLogger(__name__)

_MIN_WINDOW_WIDTH = 640
_MIN_WINDOW_HEIGHT = 400
_DEFAULT_WINDOW_WIDTH = 1100
_DEFAULT_WINDOW_HEIGHT = 700
_MAX_WINDOW_EXTENT = 10000

# Room past the measured text so the last letter is not flush with the clip edge.
_DIALOG_TEXT_SPARE = 12
_BUTTON_CHROME = 14 * 2 + 2


def _valid_position(value: int) -> bool:
    return abs(value) <= _MAX_WINDOW_EXTENT


def _keep_dialog_text_visible(box: QMessageBox) -> None:
    """Widen the message and its buttons. Stylesheet padding otherwise clips the last letters."""
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


@dataclass(frozen=True, slots=True)
class _Entry:
    id: str
    title: str
    factory: Callable[[], object] | None


class MainWindow(QMainWindow):
    """Sidebar on the left, header and page stack on the right.

    The navigation is rebuilt whenever a module adds or removes contributions. Pages are created
    lazily; a page that fails to build is replaced by an error page instead of crashing the shell.
    """

    DASHBOARD_ID = "dashboard"
    SETTINGS_ID = "settings"

    def __init__(self, runtime: Runtime) -> None:
        super().__init__()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app)
        self._runtime = runtime
        self._tr = runtime.translator.translate
        self.setWindowTitle(self._tr("app.title"))
        self.setMinimumSize(_MIN_WINDOW_WIDTH, _MIN_WINDOW_HEIGHT)
        self._restore_window_geometry()

        self._sidebar = Sidebar(self._tr("shell.brand"))
        self._quit_button = QPushButton(self._tr("nav.quit"))
        self._quit_button.setObjectName("app-quit")
        set_role(self._quit_button, "nav")
        self._quit_button.clicked.connect(self.confirm_quit)
        self._sidebar.set_quit_button(self._quit_button)
        self._header = ShellHeader()
        self._stack = QStackedWidget()
        self._stack.setObjectName("page-host")
        self._pages: dict[str, QWidget] = {}
        self._factories: dict[str, _Entry] = {}
        self._dashboard = DashboardPage(runtime, self._open_page)

        content = QWidget()
        content.setObjectName("shell-content")
        column = QVBoxLayout(content)
        column.setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.lg)
        column.setSpacing(SPACE.md)
        column.addWidget(self._header)
        column.addWidget(self._stack, 1)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._sidebar)
        layout.addWidget(content, 1)
        self.setCentralWidget(central)

        self._shown_id: str | None = None
        self._sidebar.selected.connect(self._show)
        self._remove_listener = runtime.contributions.add_listener(self.refresh_navigation)
        self._subscriptions = [
            runtime.bus.subscribe(PluginEnabled, lambda _event: self._dashboard.refresh()),
            runtime.bus.subscribe(PluginDisabled, lambda _event: self._dashboard.refresh()),
        ]
        self.refresh_navigation()
        bind_crash_dialog(self, self._tr)
        offer_previous_session(self, self._tr, on_create=self._export_diagnostics)

    def navigation_ids(self) -> list[str]:
        return self._sidebar.ids()

    def navigation_titles(self) -> list[str]:
        return self._sidebar.titles()

    def select(self, entry_id: str) -> None:
        self._sidebar.select(entry_id)

    def current_id(self) -> str | None:
        return self._sidebar.current_id()

    def current_page(self) -> QWidget:
        page = self._stack.currentWidget()
        if page is None:
            raise RuntimeError("no page is shown")
        return page

    def refresh_navigation(self) -> None:
        previous = self.current_id()
        entries = self._entries()
        ids = {entry.id for entry in entries}

        for entry_id in [known for known in self._pages if known not in ids]:
            self._remove_page(entry_id)

        self._factories = {entry.id: entry for entry in entries}
        self._sidebar.set_entries(
            [(entry.id, entry.title) for entry in entries], footer_id=self.SETTINGS_ID
        )
        target = previous if previous in ids else self.DASHBOARD_ID
        self.select(target)

    def confirm_quit(self) -> None:
        """Ask, save durable settings, and close only after that save succeeds."""
        if not self._ask_quit():
            return
        try:
            self._save_persistent_state()
        except Exception as error:
            logger.exception("Could not save before quitting")
            self._report_quit_error(error)
            return
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._remember_window_geometry()
        path = self._runtime.config_path
        if path is not None:
            try:
                save_config(self._runtime.config, path)
            except OSError:
                logger.exception("Could not store the window size")
        self._remove_listener()
        for subscription in self._subscriptions:
            subscription.cancel()
        super().closeEvent(event)

    def _ask_quit(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self._tr("nav.quit"))
        box.setText(self._tr("app.quit.confirm"))
        accept = box.addButton(
            self._tr("app.quit.confirm_button"), QMessageBox.ButtonRole.AcceptRole
        )
        cancel = box.addButton(
            self._tr("app.quit.cancel_button"), QMessageBox.ButtonRole.RejectRole
        )
        accept.setObjectName("quit-confirm")
        cancel.setObjectName("quit-cancel")
        box.setDefaultButton(cancel)
        _keep_dialog_text_visible(box)
        box.exec()
        return box.clickedButton() is accept

    def _report_quit_error(self, error: BaseException) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setObjectName("quit-error")
        box.setWindowTitle(self._tr("nav.quit"))
        detail = describe_error(self._runtime.translator, error)
        box.setText(f"{self._tr('app.quit.failed')}\n\n{detail}")
        _keep_dialog_text_visible(box)
        box.exec()

    def _restore_window_geometry(self) -> None:
        config = self._runtime.config
        width = config.window_width
        height = config.window_height
        if (
            isinstance(width, int)
            and isinstance(height, int)
            and _MIN_WINDOW_WIDTH <= width <= _MAX_WINDOW_EXTENT
            and _MIN_WINDOW_HEIGHT <= height <= _MAX_WINDOW_EXTENT
        ):
            self.resize(width, height)
        else:
            self.resize(_DEFAULT_WINDOW_WIDTH, _DEFAULT_WINDOW_HEIGHT)
        x = config.window_x
        y = config.window_y
        if isinstance(x, int) and isinstance(y, int) and _valid_position(x) and _valid_position(y):
            self.move(x, y)

    def _remember_window_geometry(self) -> None:
        if self.isMinimized():
            return
        width = self.width()
        height = self.height()
        if not (
            _MIN_WINDOW_WIDTH <= width <= _MAX_WINDOW_EXTENT
            and _MIN_WINDOW_HEIGHT <= height <= _MAX_WINDOW_EXTENT
        ):
            return
        config = self._runtime.config
        config.window_width = width
        config.window_height = height
        config.window_x = self.x()
        config.window_y = self.y()

    def _save_persistent_state(self) -> None:
        """Flush open editors and the application config. Unfinished races are left as they are."""
        self._remember_window_geometry()
        for widget in self.findChildren(QWidget):
            save = getattr(widget, "save_persistent", None)
            if callable(save):
                save()
        path = self._runtime.config_path
        if path is not None:
            save_config(self._runtime.config, path)

    def _entries(self) -> list[_Entry]:
        entries = [_Entry(self.DASHBOARD_ID, self._tr("nav.dashboard"), lambda: self._dashboard)]
        entries.extend(
            _Entry(item.id, self._tr(item.title_key, item.id), item.page_factory)
            for item in self._runtime.contributions.navigation_items()
        )
        entries.append(
            _Entry(
                self.SETTINGS_ID,
                self._tr("nav.settings"),
                lambda: SettingsPage(self._runtime, self.refresh_navigation),
            )
        )
        return entries

    def _export_diagnostics(self) -> None:
        self.select("settings")
        panel = self.current_page().findChild(DiagnosticsSettings)
        if panel is not None:
            panel.create_bundle()

    def _open_page(self, entry_id: str, action: str | None = None) -> None:
        if entry_id not in {entry.id for entry in self._entries()}:
            return
        self.select(entry_id)
        if action is None:
            return
        method = getattr(self.current_page(), action, None)
        if callable(method):
            method()

    def _show(self, entry_id: str) -> None:
        page = self._pages.get(entry_id)
        if page is None:
            entry = self._factories.get(entry_id)
            if entry is None:
                return
            page = self._build_page(entry)
            self._pages[entry_id] = page
            self._stack.addWidget(page)
        self._stack.setCurrentWidget(page)
        if entry_id != self._shown_id:
            previous = "" if self._shown_id is None else self._shown_id
            self._shown_id = entry_id
            record(
                "NAVIGATION",
                module="app",
                page=entry_id,
                result="shown",
                **{"from": previous, "to": entry_id},
            )
        entry = self._factories.get(entry_id)
        if entry is not None:
            self._header.set_title(entry.title)
        text, tone = timing_state(self._runtime)
        self._header.set_timing(text, tone)

    def _build_page(self, entry: _Entry) -> QWidget:
        if entry.factory is None:
            return MessagePage(entry.title, self._tr("page.placeholder"))
        try:
            page = entry.factory()
            if not isinstance(page, QWidget):
                raise TypeError(f"page factory returned {type(page).__name__}, expected a QWidget")
            return page
        except Exception as error:
            logger.exception("Could not build page %s", entry.id)
            return MessagePage(entry.title, self._tr("page.error"), str(error))

    def _remove_page(self, entry_id: str) -> None:
        page = self._pages.pop(entry_id)
        self._stack.removeWidget(page)
        page.deleteLater()
