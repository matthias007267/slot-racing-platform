from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from carrera.core.catalog import TrackCatalog
from carrera.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from carrera.core.storage import Database
from carrera.modules.tracks.service import TrackService
from carrera.modules.tracks.translations import TRANSLATIONS

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class TracksPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="tracks",
        version="0.2.0",
        title="Tracks",
        models_module="carrera.modules.tracks.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = TRANSLATIONS

    def activate(self, context: PluginContext) -> None:
        service = TrackService(context.get_service(Database))
        context.register_service(TrackCatalog, service)
        context.register_service(TrackService, service)
        translator = context.translator

        def tracks_page() -> QWidget:
            from carrera.modules.tracks.ui.tracks_page import TracksPage

            return TracksPage(translator, service)

        context.add_navigation(
            NavigationItem(id="tracks", title_key="nav.tracks", order=30, page_factory=tracks_page)
        )
