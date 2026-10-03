"""Plugin system: manifests, lifecycle, services and UI contributions."""

from slot_racing.core.plugin.context import PluginContext, ScopedEventBus
from slot_racing.core.plugin.contributions import (
    ContributionRegistry,
    NavigationItem,
    SettingsSection,
)
from slot_racing.core.plugin.discovery import (
    PLUGIN_ENTRY_POINT_GROUP,
    DiscoveryResult,
    discover_plugins,
)
from slot_racing.core.plugin.errors import (
    PluginActivationError,
    PluginDependencyError,
    PluginError,
    ServiceNotFoundError,
)
from slot_racing.core.plugin.manager import PluginManager, PluginState, PluginStatus
from slot_racing.core.plugin.plugin import Plugin, PluginManifest
from slot_racing.core.plugin.services import ServiceRegistry

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
    "SettingsSection",
    "discover_plugins",
]
