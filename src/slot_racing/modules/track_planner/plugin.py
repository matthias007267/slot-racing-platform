from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.plugin import NavigationItem, Plugin, PluginContext, PluginManifest
from slot_racing.core.storage import Database
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.translations import TRANSLATIONS

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class TrackPlannerPlugin(Plugin):
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="track_planner",
        version="0.3.0",
        title="Track planner",
        requires=("tracks",),
        models_module="slot_racing.modules.track_planner.models",
    )
    translations: ClassVar[dict[str, dict[str, str]]] = TRANSLATIONS

    def activate(self, context: PluginContext) -> None:
        planner = TrackPlannerService(
            context.get_service(Database), context.get_service(TrackCatalog)
        )
        context.register_service(TrackPlannerService, planner)
        translator = context.translator

        def planner_page() -> QWidget:
            from slot_racing.modules.track_planner.ui.page import PlannerPage

            return PlannerPage(
                translator,
                context.get_service(TrackCatalog),
                planner,
                context.config,
                context.config_path,
            )

        context.add_navigation(
            NavigationItem(
                id="track_planner",
                title_key="nav.track_planner",
                order=70,
                page_factory=planner_page,
            )
        )
