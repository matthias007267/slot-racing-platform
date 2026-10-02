"""Plugin system: manifests, lifecycle, services and UI contributions."""

from carrera.core.plugin.context import PluginContext, ScopedEventBus
from carrera.core.plugin.contributions import ContributionRegistry, NavigationItem
from carrera.core.plugin.discovery import (
    PLUGIN_ENTRY_POINT_GROUP,
    DiscoveryResult,
    discover_plugins,
)
from carrera.core.plugin.errors import (
    PluginActivationError,
    PluginDependencyError,
    PluginError,
    ServiceNotFoundError,
)
from carrera.core.plugin.manager import PluginManager, PluginState, PluginStatus
from carrera.core.plugin.plugin import Plugin, PluginManifest
from carrera.core.plugin.services import ServiceRegistry

__all__ = [
    "PLUGIN_ENTRY_POINT_GROUP",
    "ContributionRegistry",
    "DiscoveryResult",
    "NavigationItem",
    "Plugin",
    "PluginActivationError",
    "PluginContext",
    "PluginDependencyError",
    "PluginError",
    "PluginManager",
    "PluginManifest",
    "PluginState",
    "PluginStatus",
    "ScopedEventBus",
    "ServiceNotFoundError",
    "ServiceRegistry",
    "discover_plugins",
]
