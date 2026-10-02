from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from carrera.core.catalog import DriverCatalog, TrackCatalog, VehicleCatalog
from carrera.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from carrera.core.storage import Database
from carrera.core.timing import TimingSetupService, TimingSourceFactory
from carrera.modules.races.recorder import RaceRecorder
from carrera.modules.races.runner import RaceController
from carrera.modules.races.service import RaceService
from carrera.modules.races.translations import TRANSLATIONS

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class RacesPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="races",
        version="0.2.0",
        title="Races",
        requires=("drivers_vehicles", "tracks"),
        optional=("timing",),
        models_module="carrera.modules.races.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = TRANSLATIONS

    def __init__(self) -> None:
        self._controller: RaceController | None = None
        self._recorder: RaceRecorder | None = None

    def activate(self, context: PluginContext) -> None:
        drivers = context.get_service(DriverCatalog)
        vehicles = context.get_service(VehicleCatalog)
        tracks = context.get_service(TrackCatalog)
        service = RaceService(context.get_service(Database), drivers, vehicles, tracks)
        service.abort_stale_races()

        recorder = RaceRecorder(service, context.events)
        controller = RaceController(
            service,
            context.events,
            context.clock,
            factories=lambda: context.find_services(TimingSourceFactory),
            preferred_source=context.config.timing_source,
            storage_errors=lambda: recorder.errors,
            setups=lambda: context.find_service(TimingSetupService),
        )
        self._recorder = recorder
        self._controller = controller
        context.register_service(RaceService, service)
        context.register_service(RaceController, controller)
        translator = context.translator

        def races_page() -> QWidget:
            from carrera.modules.races.ui.races_page import RacesPage

            return RacesPage(translator, service, controller, drivers, vehicles, tracks)

        context.add_navigation(
            NavigationItem(id="races", title_key="nav.races", order=40, page_factory=races_page)
        )

    def deactivate(self) -> None:
        if self._controller is not None:
            self._controller.shutdown()
            self._controller = None
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None
