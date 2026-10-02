"""Race area: list of races, configuration flow, live view and results."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from carrera.core.catalog import DriverCatalog, TrackCatalog, VehicleCatalog
from carrera.core.domain import RaceId
from carrera.core.i18n import Translator
from carrera.modules.races.runner import RaceController
from carrera.modules.races.service import RaceService
from carrera.modules.races.ui.live_view import LiveRaceView
from carrera.modules.races.ui.results_view import ResultsView
from carrera.modules.races.ui.wizard import RaceWizard
from carrera.uikit import StatusLabel, describe_error, fill_table, heading, make_table, selected_id
from carrera.uikit.errors import is_expected
from carrera.uikit.widgets import format_datetime

logger = logging.getLogger(__name__)


class RacesPage(QWidget):
    def __init__(
        self,
        translator: Translator,
        service: RaceService,
        controller: RaceController,
        drivers: DriverCatalog,
        vehicles: VehicleCatalog,
        tracks: TrackCatalog,
    ) -> None:
        super().__init__()
        self.translator = translator
        self._service = service
        self._controller = controller
        self.confirm: Callable[[str], bool] = lambda text: (
            QMessageBox.question(self, translator.translate("common.confirm"), text)
            == QMessageBox.StandardButton.Yes
        )
        tr = translator.translate

        self.table = make_table(
            [
                tr("race.column.name"),
                tr("race.column.track"),
                tr("race.column.status"),
                tr("race.column.laps"),
                tr("race.column.participants"),
                tr("race.column.created"),
            ],
            "races-table",
        )
        self.status = StatusLabel("races-status")
        self.buttons: dict[str, QPushButton] = {}
        button_row = QHBoxLayout()
        for key in ("new", "edit", "start", "live", "results", "delete"):
            button = QPushButton(tr(f"race.list.{key}"))
            button.setObjectName(f"races-{key}")
            self.buttons[key] = button
            button_row.addWidget(button)
        button_row.addStretch(1)

        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.addWidget(heading(tr("nav.races")))
        list_layout.addLayout(button_row)
        list_layout.addWidget(self.table, 1)
        list_layout.addWidget(self.status)

        self.wizard = RaceWizard(translator, service, drivers, vehicles, tracks)
        self.live = LiveRaceView(translator, controller)
        self.results = ResultsView(translator, service)
        self.stack = QStackedWidget()
        self.list_page = list_page
        for page in (list_page, self.wizard, self.live, self.results):
            self.stack.addWidget(page)
        layout = QVBoxLayout(self)
        layout.addWidget(self.stack)

        self.buttons["new"].clicked.connect(lambda: self.new_race())
        self.buttons["edit"].clicked.connect(lambda: self.edit_selected())
        self.buttons["start"].clicked.connect(lambda: self.start_selected())
        self.buttons["live"].clicked.connect(lambda: self.show_live())
        self.buttons["results"].clicked.connect(lambda: self.show_results_selected())
        self.buttons["delete"].clicked.connect(lambda: self.delete_selected())
        self.wizard.closed.connect(self.show_list)
        self.wizard.start_requested.connect(lambda race_id: self.start_race(RaceId(race_id)))
        self.live.race_over.connect(lambda race_id: self.show_results(RaceId(race_id)))
        self.results.back_requested.connect(self.show_list)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._races: dict[int, str] = {}

    def current_view(self) -> QWidget | None:
        return self.stack.currentWidget()

    def refresh(self) -> None:
        self._guard(self._reload)

    def show_list(self) -> None:
        self.stack.setCurrentWidget(self.list_page)
        self.refresh()

    def new_race(self) -> None:
        self._guard(lambda: self._open_wizard(None))

    def edit_selected(self) -> None:
        race_id = selected_id(self.table)
        if race_id is not None:
            self._guard(lambda: self._open_wizard(RaceId(race_id)))

    def start_selected(self) -> None:
        race_id = selected_id(self.table)
        if race_id is not None:
            self.start_race(RaceId(race_id))

    def start_race(self, race_id: RaceId) -> bool:
        return self._guard(lambda: self._start(race_id))

    def show_live(self) -> None:
        runner = self._controller.active
        if runner is None:
            self.status.show_error(self.translator.translate("race.live.no_race"))
            return
        self.live.show_runner(runner)
        self.stack.setCurrentWidget(self.live)

    def show_results_selected(self) -> None:
        race_id = selected_id(self.table)
        if race_id is not None:
            self.show_results(RaceId(race_id))

    def show_results(self, race_id: RaceId) -> None:
        def show() -> None:
            self.results.show_race(race_id)
            self.stack.setCurrentWidget(self.results)

        self._guard(show)

    def delete_selected(self) -> None:
        race_id = selected_id(self.table)
        if race_id is None:
            return
        name = self._races.get(race_id, "")
        if self.confirm(self.translator.format("common.confirm_delete", name=name)):
            self._guard(lambda: self._delete(RaceId(race_id)))

    def showEvent(self, event: object) -> None:  # noqa: N802
        super().showEvent(event)  # type: ignore[arg-type]
        if self.stack.currentWidget() is self.list_page:
            self.refresh()

    def _open_wizard(self, race_id: RaceId | None) -> None:
        self.wizard.begin(race_id)
        self.stack.setCurrentWidget(self.wizard)

    def _start(self, race_id: RaceId) -> None:
        runner = self._controller.start_race(race_id)
        self.live.show_runner(runner)
        self.stack.setCurrentWidget(self.live)

    def _delete(self, race_id: RaceId) -> None:
        self._service.delete_race(race_id)
        self._reload()
        self.status.show_info(self.translator.translate("common.deleted"))

    def _reload(self) -> None:
        tr = self.translator.translate
        races = self._service.list_races()
        self._races = {race.id: race.name for race in races}
        fill_table(
            self.table,
            [
                (
                    race.name,
                    race.track_name,
                    tr(f"race.status.{race.status.value}"),
                    str(race.laps),
                    str(len(race.participants)),
                    format_datetime(race.created_at),
                )
                for race in races
            ],
            [race.id for race in races],
        )
        self._update_buttons()

    def _update_buttons(self) -> None:
        race_id = selected_id(self.table)
        selected = None if race_id is None else self._service.get_race(RaceId(race_id))
        self.buttons["edit"].setEnabled(selected is not None and selected.is_editable)
        self.buttons["start"].setEnabled(selected is not None and selected.is_editable)
        self.buttons["results"].setEnabled(selected is not None)
        self.buttons["delete"].setEnabled(selected is not None)
        self.buttons["live"].setEnabled(self._controller.active is not None)

    def _guard(self, action: Callable[[], None]) -> bool:
        try:
            action()
        except Exception as error:
            if not is_expected(error):
                logger.exception("Race page action failed")
            self.status.show_error(describe_error(self.translator, error))
            return False
        return True
