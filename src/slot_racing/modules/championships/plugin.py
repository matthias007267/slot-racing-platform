from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from slot_racing.core.catalog import DriverCatalog, RaceHistoryCatalog
from slot_racing.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from slot_racing.core.storage import Database
from slot_racing.modules.championships.service import ChampionshipService
from slot_racing.modules.championships.translations import TRANSLATIONS

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class ChampionshipsPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="championships",
        version="0.1.0",
        title="Championships",
        requires=("races", "drivers_vehicles"),
        models_module="slot_racing.modules.championships.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = TRANSLATIONS

    def activate(self, context: PluginContext) -> None:
        service = ChampionshipService(
            context.get_service(Database),
            context.get_service(RaceHistoryCatalog),
            context.get_service(DriverCatalog),
        )
        context.register_service(ChampionshipService, service)
        translator = context.translator
        drivers = context.get_service(DriverCatalog)

        def championships_page() -> QWidget:
            from slot_racing.modules.championships.ui.page import ChampionshipsPage

            return ChampionshipsPage(translator, service, drivers)

        context.add_navigation(
            NavigationItem(
                id="championships",
                title_key="nav.championships",
                order=45,
                page_factory=championships_page,
            )
        )
