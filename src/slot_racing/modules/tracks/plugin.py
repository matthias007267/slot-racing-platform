from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from slot_racing.core.storage import Database
from slot_racing.core.timing import TimingSetupService
from slot_racing.core.timing_registry import TimingProviderRegistry
from slot_racing.modules.tracks.service import TrackService
from slot_racing.modules.tracks.translations import TRANSLATIONS

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class TracksPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="tracks",
        version="0.3.0",
        title="Tracks",
        optional=("timing",),
        models_module="slot_racing.modules.tracks.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = TRANSLATIONS

    def activate(self, context: PluginContext) -> None:
        service = TrackService(context.get_service(Database))
        context.register_service(TrackCatalog, service)
        context.register_service(TrackService, service)
        translator = context.translator

        def tracks_page() -> QWidget:
            from slot_racing.modules.tracks.ui.tracks_area import TracksArea

            return TracksArea(
                translator,
                service,
                setups=lambda: context.find_service(TimingSetupService),
                providers=context.get_service(TimingProviderRegistry),
                clock=context.clock,
            )

        context.add_navigation(
            NavigationItem(id="tracks", title_key="nav.tracks", order=30, page_factory=tracks_page)
        )
