"""Plugin discovery through Python entry points."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points

from carrera.core.plugin.plugin import Plugin

logger = logging.getLogger(__name__)

PLUGIN_ENTRY_POINT_GROUP = "carrera.plugins"


@dataclass(slots=True)
class DiscoveryResult:
    plugins: list[Plugin] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    """Entry point name -> error for plugins that could not be imported."""


def discover_plugins(group: str = PLUGIN_ENTRY_POINT_GROUP) -> DiscoveryResult:
    """Instantiate all plugins registered under ``group``; a broken one never aborts discovery."""
    result = DiscoveryResult()
    for entry_point in entry_points(group=group):
        try:
            plugin_class = entry_point.load()
            plugin = plugin_class()
            if not isinstance(plugin, Plugin):
                raise TypeError(f"{entry_point.value} is not a Plugin subclass")
            result.plugins.append(plugin)
        except Exception as error:
            logger.exception("Could not load plugin entry point %s", entry_point.name)
            result.failures[entry_point.name] = f"{type(error).__name__}: {error}"
    return result
