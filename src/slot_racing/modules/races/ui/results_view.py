"""Stored results of a race, ordered exactly as the race engine ranked them."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHeaderView, QLabel, QPushButton, QVBoxLayout, QWidget

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceId
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.service import RaceService
from slot_racing.modules.races.ui.formatting import participant_status_key, start_number_text
from slot_racing.uikit import fill_table, heading, make_table
from slot_racing.uikit.theme import configure_page, set_role


class ResultsView(QWidget):
    back_requested = Signal()

    def __init__(self, translator: Translator, service: RaceService) -> None:
        super().__init__()
        self.translator = translator
        self._service = service
        tr = translator.translate
        self.header = heading(tr("race.results.title"))
        self.header.setObjectName("results-header")
        self.summary = QLabel()
        self.summary.setObjectName("results-summary")
        self.table = make_table(
            [
                tr("race.column.position"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
                tr("race.column.start_number"),
                tr("race.column.lane"),
                tr("race.column.laps_done"),
                tr("race.column.total_time"),
                tr("race.column.best_lap"),
                tr("race.column.last_lap"),
                tr("race.column.average_lap"),
                tr("race.column.status"),
            ],
            "results-table",
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.laps_table = make_table(
            [
                tr("race.column.lane"),
                tr("race.column.driver"),
                tr("race.column.lap"),
                tr("race.column.lap_time"),
                tr("race.column.sectors"),
            ],
            "results-laps",
        )
        self.back_button = QPushButton(tr("race.results.back"))
        self.back_button.setObjectName("results-back")
        set_role(self.back_button, "ghost")
        self.back_button.clicked.connect(self.back_requested.emit)
        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addWidget(self.header)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 2)
        layout.addWidget(QLabel(tr("race.results.laps")))
        layout.addWidget(self.laps_table, 2)
        layout.addWidget(self.back_button)

    def show_race(self, race_id: RaceId) -> None:
        tr = self.translator.translate
        race = self._service.require_race(race_id)
        self.summary.setText(
            self.translator.format(
                "race.results.header",
                name=race.name,
                track=race.track_name,
                status=tr(f"race.status.{race.status.value}"),
            )
        )
        results = self._service.get_results(race_id)
        ended = race.is_over
        fill_table(
            self.table,
            [
                (
                    "-" if row.position is None else str(row.position),
                    row.driver_label,
                    row.vehicle_label,
                    start_number_text(row.start_number),
                    str(row.lane),
                    str(row.laps_completed),
                    format_duration(row.total_time_ns),
                    format_duration(row.best_lap_ns),
                    format_duration(row.last_lap_ns),
                    format_duration(row.average_lap_ns),
                    tr(participant_status_key(finished=row.finished, paused=False, ended=ended)),
                )
                for row in results
            ],
            keep_selection=False,
        )
        drivers = {row.lane: row.driver_label for row in results}
        laps = self._service.get_laps(race_id)
        fill_table(
            self.laps_table,
            [
                (
                    str(lap.lane),
                    drivers.get(lap.lane, ""),
                    str(lap.lap_number),
                    format_duration(lap.lap_time_ns),
                    " | ".join(format_duration(t) for t in lap.sector_times_ns),
                )
                for lap in laps
            ],
            keep_selection=False,
        )
