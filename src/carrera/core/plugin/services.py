"""Registry for services that plugins offer to each other through core interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from carrera.core.plugin.errors import PluginError, ServiceNotFoundError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _Entry:
    interface: type[Any]
    name: str
    owner: str
    implementation: Any


class ServiceRegistry:
    """Maps interfaces to implementations. Several implementations may share an interface
    (for example several ``TimingSource`` s) as long as their names differ."""

    def __init__(self) -> None:
        self._entries: list[_Entry] = []

    def register(
        self, interface: type[T], implementation: T, *, owner: str, name: str | None = None
    ) -> None:
        entry_name = name or owner
        if any(e.interface is interface and e.name == entry_name for e in self._entries):
            raise PluginError(f"service {interface.__name__}/{entry_name} is already registered")
        self._entries.append(_Entry(interface, entry_name, owner, implementation))

    def find_all(self, interface: type[T]) -> list[T]:
        return [e.implementation for e in self._entries if e.interface is interface]

    def find(self, interface: type[T], name: str | None = None) -> T | None:
        for entry in self._entries:
            if entry.interface is interface and (name is None or entry.name == name):
                return entry.implementation  # type: ignore[no-any-return]
        return None

    def get(self, interface: type[T], name: str | None = None) -> T:
        service = self.find(interface, name)
        if service is None:
            raise ServiceNotFoundError(f"no service registered for {interface.__name__}")
        return service

    def remove_owner(self, owner: str) -> None:
        self._entries = [e for e in self._entries if e.owner != owner]
