from __future__ import annotations

from typing import ClassVar

from slot_racing.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest


class TrackPlannerPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="track_planner",
        version="0.1.0",
        title="Track planner",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {
            "plugin.track_planner.title": "Streckenplaner",
            "nav.track_planner": "Streckenplaner",
        }
    }

    def activate(self, context: PluginContext) -> None:
        context.add_navigation(
            NavigationItem(id="track_planner", title_key="nav.track_planner", order=70)
        )
