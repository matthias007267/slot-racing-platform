from __future__ import annotations

from typing import ClassVar

from slot_racing.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from slot_racing.core.storage import Database
from slot_racing.core.timing import TimingSetupService
from slot_racing.modules.timing.service import TimingSetupManager
from slot_racing.modules.timing.simulation import SimulationTimingFactory


class TimingPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="timing",
        version="0.3.0",
        title="Timing",
        models_module="slot_racing.modules.timing.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {
            "plugin.timing.title": "Zeitmessung",
            "nav.timing": "Zeitmessung",
            "timing.provider.simulation": "Simulation",
        }
    }

    def activate(self, context: PluginContext) -> None:
        context.add_navigation(NavigationItem(id="timing", title_key="nav.timing", order=50))
        context.register_service(
            TimingSetupService, TimingSetupManager(context.get_service(Database))
        )
        context.register_timing_provider(SimulationTimingFactory(context.clock))
