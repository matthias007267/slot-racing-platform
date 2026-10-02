from __future__ import annotations

from typing import ClassVar

from carrera.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest


class DriversVehiclesPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="drivers_vehicles",
        version="0.1.0",
        title="Drivers and vehicles",
        models_module="carrera.modules.drivers_vehicles.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {
            "plugin.drivers_vehicles.title": "Fahrer und Fahrzeuge",
            "nav.drivers": "Fahrer",
            "nav.vehicles": "Fahrzeuge",
        }
    }

    def activate(self, context: PluginContext) -> None:
        context.add_navigation(NavigationItem(id="drivers", title_key="nav.drivers", order=10))
        context.add_navigation(NavigationItem(id="vehicles", title_key="nav.vehicles", order=20))
