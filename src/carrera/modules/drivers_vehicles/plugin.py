from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from carrera.core.catalog import DriverCatalog, VehicleCatalog
from carrera.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from carrera.core.storage import Database
from carrera.modules.drivers_vehicles.service import DriverService, VehicleService
from carrera.modules.drivers_vehicles.translations import TRANSLATIONS

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class DriversVehiclesPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="drivers_vehicles",
        version="0.2.0",
        title="Drivers and vehicles",
        models_module="carrera.modules.drivers_vehicles.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = TRANSLATIONS

    def activate(self, context: PluginContext) -> None:
        database = context.get_service(Database)
        drivers = DriverService(database)
        vehicles = VehicleService(database)
        context.register_service(DriverCatalog, drivers)
        context.register_service(VehicleCatalog, vehicles)
        context.register_service(DriverService, drivers)
        context.register_service(VehicleService, vehicles)
        translator = context.translator

        def drivers_page() -> QWidget:
            from carrera.modules.drivers_vehicles.ui.drivers_page import DriversPage

            return DriversPage(translator, drivers)

        def vehicles_page() -> QWidget:
            from carrera.modules.drivers_vehicles.ui.vehicles_page import VehiclesPage

            return VehiclesPage(translator, vehicles, drivers)

        context.add_navigation(
            NavigationItem(
                id="drivers", title_key="nav.drivers", order=10, page_factory=drivers_page
            )
        )
        context.add_navigation(
            NavigationItem(
                id="vehicles", title_key="nav.vehicles", order=20, page_factory=vehicles_page
            )
        )
