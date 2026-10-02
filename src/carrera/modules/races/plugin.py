from __future__ import annotations

from typing import ClassVar

from carrera.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest


class RacesPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="races",
        version="0.1.0",
        title="Races",
        models_module="carrera.modules.races.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {"plugin.races.title": "Rennen", "nav.races": "Rennen"}
    }

    def activate(self, context: PluginContext) -> None:
        context.add_navigation(NavigationItem(id="races", title_key="nav.races", order=40))
