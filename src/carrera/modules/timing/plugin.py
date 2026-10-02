from __future__ import annotations

from typing import ClassVar

from carrera.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from carrera.core.storage import Database
from carrera.core.timing import TimingSetupService, TimingSourceFactory
from carrera.modules.timing.service import TimingSetupManager
from carrera.modules.timing.simulation import SimulationTimingFactory


class TimingPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="timing",
        version="0.2.0",
        title="Timing",
        models_module="carrera.modules.timing.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {"plugin.timing.title": "Zeitmessung", "nav.timing": "Zeitmessung"}
    }

    def activate(self, context: PluginContext) -> None:
        context.add_navigation(NavigationItem(id="timing", title_key="nav.timing", order=50))
        context.register_service(
            TimingSetupService, TimingSetupManager(context.get_service(Database))
        )
        context.register_service(
            TimingSourceFactory, SimulationTimingFactory(context.clock), name="simulation"
        )
