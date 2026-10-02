from __future__ import annotations

from typing import ClassVar

from carrera.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest


class StatisticsPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="statistics",
        version="0.1.0",
        title="Statistics",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {"plugin.statistics.title": "Statistiken", "nav.statistics": "Statistiken"}
    }

    def activate(self, context: PluginContext) -> None:
        context.add_navigation(
            NavigationItem(id="statistics", title_key="nav.statistics", order=60)
        )
