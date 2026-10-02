from __future__ import annotations

from typing import ClassVar

from carrera.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest


class TimingPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="timing",
        version="0.1.0",
        title="Timing",
        models_module="carrera.modules.timing.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {"plugin.timing.title": "Zeitmessung", "nav.timing": "Zeitmessung"}
    }

    def activate(self, context: PluginContext) -> None:
        context.add_navigation(NavigationItem(id="timing", title_key="nav.timing", order=50))
