"""Plugin contract."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from carrera.core.plugin.context import PluginContext

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Static description of a plugin."""

    name: str
    version: str
    title: str
    """English fallback title. The German title is the catalog key ``plugin.<name>.title``."""
    requires: tuple[str, ...] = ()
    """Plugins that must be enabled for this one to work."""
    optional: tuple[str, ...] = ()
    """Plugins that are used when present. They only influence activation order."""
    enabled_by_default: bool = True
    models_module: str | None = None
    """Dotted path of a module defining SQLAlchemy models owned by this plugin."""

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(f"invalid plugin name {self.name!r}")
        if self.name in self.requires or self.name in self.optional:
            raise ValueError("a plugin cannot depend on itself")


class Plugin:
    """Base class of all modules.

    Subclasses define ``manifest`` and override ``activate``/``deactivate`` as needed. Everything
    registered through the :class:`PluginContext` is removed automatically on deactivation.
    """

    manifest: ClassVar[PluginManifest]
    translations: ClassVar[Mapping[str, Mapping[str, str]]] = {}
    """Message catalogs per language. Registered when the plugin is registered, so titles of
    disabled plugins stay translatable."""

    def activate(self, context: PluginContext) -> None:
        """Register services, subscriptions and UI contributions."""

    def deactivate(self) -> None:
        """Release resources the plugin created itself (threads, devices, ...)."""
