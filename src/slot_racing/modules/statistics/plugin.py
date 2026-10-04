from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from slot_racing.core.catalog import TimeMeasurementCatalog, TrackCatalog
from slot_racing.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class StatisticsPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="statistics",
        version="0.2.0",
        title="Statistics",
        requires=("tracks",),
        optional=("races",),
    )
    translations: ClassVar[dict[str, dict[str, str]]] = {
        "de": {
            "plugin.statistics.title": "Statistiken",
            "nav.statistics": "Statistiken",
            "statistics.track": "Strecke",
            "statistics.lanes": "Beste Zeiten",
            "statistics.column.lane": "Bahn",
            "statistics.column.driver": "Fahrer",
            "statistics.column.vehicle": "Fahrzeug",
            "statistics.column.best": "Beste Zeit",
            "statistics.empty": "Keine Strecke vorhanden.",
        }
    }

    def activate(self, context: PluginContext) -> None:
        translator = context.translator

        def statistics_page() -> QWidget:
            from slot_racing.modules.statistics.ui.page import StatisticsPage

            return StatisticsPage(
                translator,
                context.get_service(TrackCatalog),
                lambda: context.find_service(TimeMeasurementCatalog),
            )

        context.add_navigation(
            NavigationItem(
                id="statistics",
                title_key="nav.statistics",
                order=60,
                page_factory=statistics_page,
            )
        )
