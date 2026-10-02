from __future__ import annotations

from typing import ClassVar

from carrera.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest


class TracksPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="tracks",
        version="0.1.0",
        title="Tracks",
        models_module="carrera.modules.tracks.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {"plugin.tracks.title": "Strecken", "nav.tracks": "Strecken"}
    }

    def activate(self, context: PluginContext) -> None:
        context.add_navigation(NavigationItem(id="tracks", title_key="nav.tracks", order=30))
