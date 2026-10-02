"""Main window with dynamic navigation built from module contributions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from slot_racing.app.pages import DashboardPage, MessagePage, SettingsPage
from slot_racing.app.runtime import Runtime
from slot_racing.core.events import PluginDisabled, PluginEnabled

logger = logging.getLogger(__name__)

_ROLE_ID = Qt.ItemDataRole.UserRole


@dataclass(frozen=True, slots=True)
class _Entry:
    id: str
    title: str
    factory: Callable[[], object] | None


class MainWindow(QMainWindow):
    """Navigation list on the left, pages on the right.

    The navigation is rebuilt whenever a module adds or removes contributions. Pages are created
    lazily; a page that fails to build is replaced by an error page instead of crashing the shell.
    """

    DASHBOARD_ID = "dashboard"
    SETTINGS_ID = "settings"

    def __init__(self, runtime: Runtime) -> None:
        super().__init__()
        self._runtime = runtime
        self._tr = runtime.translator.translate
        self.setWindowTitle(self._tr("app.title"))
        self.resize(1100, 700)

        self._nav = QListWidget()
        self._nav.setObjectName("navigation")
        self._nav.setFixedWidth(220)
        self._stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}
        self._dashboard = DashboardPage(runtime)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self._nav)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        self._nav.currentItemChanged.connect(self._on_selection_changed)
        self._remove_listener = runtime.contributions.add_listener(self.refresh_navigation)
        self._subscriptions = [
            runtime.bus.subscribe(PluginEnabled, lambda _event: self._dashboard.refresh()),
            runtime.bus.subscribe(PluginDisabled, lambda _event: self._dashboard.refresh()),
        ]
        self.refresh_navigation()

    def navigation_ids(self) -> list[str]:
        return [self._nav.item(i).data(_ROLE_ID) for i in range(self._nav.count())]

    def navigation_titles(self) -> list[str]:
        return [self._nav.item(i).text() for i in range(self._nav.count())]

    def select(self, entry_id: str) -> None:
        for index, known_id in enumerate(self.navigation_ids()):
            if known_id == entry_id:
                self._nav.setCurrentRow(index)
                return

    def current_id(self) -> str | None:
        item = self._nav.currentItem()
        return None if item is None else str(item.data(_ROLE_ID))

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

        self._nav.blockSignals(True)
        self._nav.clear()
        for entry in entries:
            item = QListWidgetItem(entry.title)
            item.setData(_ROLE_ID, entry.id)
            self._nav.addItem(item)
        self._nav.blockSignals(False)

        self._factories = {entry.id: entry for entry in entries}
        target = previous if previous in ids else self.DASHBOARD_ID
        self.select(target)
        self._show(target)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._remove_listener()
        for subscription in self._subscriptions:
            subscription.cancel()
        super().closeEvent(event)

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

    def _on_selection_changed(self, current: QListWidgetItem | None) -> None:
        if current is not None:
            self._show(str(current.data(_ROLE_ID)))

    def _show(self, entry_id: str) -> None:
        page = self._pages.get(entry_id)
        if page is None:
            page = self._build_page(self._factories[entry_id])
            self._pages[entry_id] = page
            self._stack.addWidget(page)
        self._stack.setCurrentWidget(page)

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
