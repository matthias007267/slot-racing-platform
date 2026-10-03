"""UI contributions of plugins, described without any UI toolkit."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass

from slot_racing.core.plugin.errors import PluginError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SettingsSection:
    """A block on the settings page, contributed by a plugin.

    ``factory`` creates the section widget lazily. It is typed as ``object`` because the core is
    toolkit independent; the settings page expects a ``QWidget``.
    """

    id: str
    title_key: str
    order: int = 100
    factory: Callable[[], object] | None = None
    owner: str = ""


@dataclass(frozen=True, slots=True)
class NavigationItem:
    """An entry in the main navigation.

    ``page_factory`` creates the page widget lazily. It is typed as ``object`` because the core
    is toolkit independent; the app shell expects a ``QWidget``. Without a factory the shell
    shows a placeholder page.
    """

    id: str
    title_key: str
    order: int = 100
    page_factory: Callable[[], object] | None = None
    owner: str = ""


class ContributionRegistry:
    """Collects navigation items and settings sections and notifies the shell on change."""

    def __init__(self) -> None:
        self._items: list[NavigationItem] = []
        self._sections: list[SettingsSection] = []
        self._listeners: list[Callable[[], None]] = []

    def add_navigation(self, owner: str, item: NavigationItem) -> None:
        if any(existing.id == item.id for existing in self._items):
            raise PluginError(f"navigation item {item.id!r} is already registered")
        self._items.append(dataclasses.replace(item, owner=owner))
        self._notify()

    def add_settings_section(self, owner: str, section: SettingsSection) -> None:
        if any(existing.id == section.id for existing in self._sections):
            raise PluginError(f"settings section {section.id!r} is already registered")
        self._sections.append(dataclasses.replace(section, owner=owner))
        self._notify()

    def remove_owner(self, owner: str) -> None:
        items = [item for item in self._items if item.owner != owner]
        sections = [section for section in self._sections if section.owner != owner]
        changed = len(items) != len(self._items) or len(sections) != len(self._sections)
        self._items = items
        self._sections = sections
        if changed:
            self._notify()

    def navigation_items(self) -> list[NavigationItem]:
        return sorted(self._items, key=lambda item: (item.order, item.id))

    def settings_sections(self) -> list[SettingsSection]:
        return sorted(self._sections, key=lambda section: (section.order, section.id))

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a change listener. Returns a function that removes it."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                logger.exception("Contribution listener failed")
