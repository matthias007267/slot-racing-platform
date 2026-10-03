from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from slot_racing.core.catalog import DriverCatalog, RaceCatalog, TrackCatalog, VehicleCatalog
from slot_racing.core.plugin import (
    NavigationItem,
    Plugin,
    PluginContext,
    PluginManifest,
    SettingsSection,
)
from slot_racing.core.storage import Database
from slot_racing.core.timing import TimingSetupService
from slot_racing.core.timing_registry import TimingProviderRegistry
from slot_racing.modules.races.hud import HudConfigurationStore
from slot_racing.modules.races.overview import RaceOverview
from slot_racing.modules.races.recorder import RaceRecorder
from slot_racing.modules.races.runner import RaceController
from slot_racing.modules.races.service import RaceService
from slot_racing.modules.races.translations import TRANSLATIONS

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class RacesPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="races",
        version="0.3.0",
        title="Races",
        requires=("drivers_vehicles", "tracks"),
        optional=("timing",),
        models_module="slot_racing.modules.races.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = TRANSLATIONS

    def __init__(self) -> None:
        self._controller: RaceController | None = None
        self._recorder: RaceRecorder | None = None

    def activate(self, context: PluginContext) -> None:
        drivers = context.get_service(DriverCatalog)
        vehicles = context.get_service(VehicleCatalog)
        tracks = context.get_service(TrackCatalog)
        providers = context.get_service(TimingProviderRegistry)
        service = RaceService(
            context.get_service(Database),
            drivers,
            vehicles,
            tracks,
            providers,
            context.config.timing_source,
        )
        service.abort_stale_races()

        recorder = RaceRecorder(service, context.events)
        controller = RaceController(
            service,
            context.events,
            context.clock,
            providers=providers,
            storage_errors=lambda: recorder.errors,
            setups=lambda: context.find_service(TimingSetupService),
        )
        self._recorder = recorder
        self._controller = controller
        context.register_service(RaceService, service)
        context.register_service(RaceController, controller)
        context.register_service(RaceCatalog, RaceOverview(service, controller))
        hud_store = HudConfigurationStore(context.get_service(Database))
        context.register_service(HudConfigurationStore, hud_store)
        translator = context.translator

        def races_page() -> QWidget:
            from slot_racing.modules.races.ui.races_page import RacesPage

            return RacesPage(
                translator,
                service,
                controller,
                drivers,
                vehicles,
                tracks,
                providers,
                hud_store,
            )

        def hud_settings() -> QWidget:
            from slot_racing.modules.races.ui.hud_editor import HudEditor

            return HudEditor(translator, hud_store)

        context.add_navigation(
            NavigationItem(id="races", title_key="nav.races", order=40, page_factory=races_page)
        )
        context.add_settings_section(
            SettingsSection(
                id="race_hud",
                title_key="hud.settings.title",
                order=10,
                factory=hud_settings,
            )
        )

    def deactivate(self) -> None:
        if self._controller is not None:
            self._controller.shutdown()
            self._controller = None
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None
