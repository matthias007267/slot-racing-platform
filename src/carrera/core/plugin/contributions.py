"""UI contributions of plugins, described without any UI toolkit."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass

from carrera.core.plugin.errors import PluginError

logger = logging.getLogger(__name__)


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
    """Collects navigation items per plugin and notifies the shell when they change."""

    def __init__(self) -> None:
        self._items: list[NavigationItem] = []
        self._listeners: list[Callable[[], None]] = []

    def add_navigation(self, owner: str, item: NavigationItem) -> None:
        if any(existing.id == item.id for existing in self._items):
            raise PluginError(f"navigation item {item.id!r} is already registered")
        self._items.append(dataclasses.replace(item, owner=owner))
        self._notify()

    def remove_owner(self, owner: str) -> None:
        remaining = [item for item in self._items if item.owner != owner]
        if len(remaining) != len(self._items):
            self._items = remaining
            self._notify()

    def navigation_items(self) -> list[NavigationItem]:
        return sorted(self._items, key=lambda item: (item.order, item.id))

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
