"""Stored results of a race.

Lap races stay in the order the race engine ranked them. A time trial shows every measurement,
with driver, vehicle, lane, time and the moment it was recorded, plus best times that never mix
lanes.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHeaderView, QLabel, QPushButton, QVBoxLayout, QWidget

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceId, RaceMode, TrackId
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.service import RaceService
from slot_racing.modules.races.types import RaceInfo, TimeBest
from slot_racing.modules.races.ui.formatting import participant_status_key, start_number_text
from slot_racing.modules.races.ui.time_trial_board_view import fill_lane_table
from slot_racing.uikit import fill_table, heading, make_table
from slot_racing.uikit.theme import configure_page, set_role
from slot_racing.uikit.widgets import format_datetime


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
        self.laps_heading = QLabel(tr("race.results.laps"))
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
        self.records_heading = QLabel(tr("race.time_trial.records"))
        self.records_heading.setObjectName("time-trial-result-records-heading")
        set_role(self.records_heading, "section")
        self.records_table = make_table(
            [
                tr("race.time_trial.column.lane"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
                tr("race.time_trial.column.best"),
            ],
            "time-trial-result-records",
        )
        self.measurements_heading = QLabel(tr("race.results.measurements"))
        self.measurements_table = make_table(
            [
                tr("race.column.driver"),
                tr("race.column.vehicle"),
                tr("race.column.lane"),
                tr("race.column.measured_time"),
                tr("race.column.measured_at"),
            ],
            "time-measurements",
        )
        self.bests_heading = QLabel(tr("race.results.bests"))
        self.bests_table = make_table(
            [
                tr("race.column.lane"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
                tr("race.column.measured_time"),
                tr("race.column.measured_at"),
            ],
            "time-bests",
        )
        self.ranking_heading = QLabel(tr("race.results.lane_ranking"))
        self.ranking_table = make_table(
            [
                tr("race.column.lane"),
                tr("race.column.position"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
                tr("race.column.measured_time"),
            ],
            "time-lane-ranking",
        )
        self.back_button = QPushButton(tr("race.results.back"))
        self.back_button.setObjectName("results-back")
        set_role(self.back_button, "ghost")
        self.back_button.clicked.connect(self.back_requested.emit)
        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addWidget(self.header)
        layout.addWidget(self.summary)
        layout.addWidget(self.records_heading)
        layout.addWidget(self.records_table, 2)
        layout.addWidget(self.table, 2)
        layout.addWidget(self.laps_heading)
        layout.addWidget(self.laps_table, 2)
        layout.addWidget(self.measurements_heading)
        layout.addWidget(self.measurements_table, 2)
        layout.addWidget(self.bests_heading)
        layout.addWidget(self.bests_table, 2)
        layout.addWidget(self.ranking_heading)
        layout.addWidget(self.ranking_table, 2)
        layout.addWidget(self.back_button)

    def show_race(self, race_id: RaceId) -> None:
        race = self._service.require_race(race_id)
        time_trial = race.mode is RaceMode.TIME_TRIAL
        for widget in (self.table, self.laps_heading, self.laps_table):
            widget.setVisible(not time_trial)
        for widget in (
            self.records_heading,
            self.records_table,
            self.measurements_heading,
            self.measurements_table,
            self.bests_heading,
            self.bests_table,
            self.ranking_heading,
            self.ranking_table,
        ):
            widget.setVisible(time_trial)
        if time_trial:
            self._show_time_trial(race)
            return
        self.summary.setText(self._header(race))
        self._show_lap_race(race_id, race.is_over)

    def _header(self, race: RaceInfo) -> str:
        return self.translator.format(
            "race.results.header",
            name=race.name,
            track=race.track_name,
            status=self.translator.translate(f"race.status.{race.status.value}"),
        )

    def _best_line(self, best: TimeBest) -> str:
        return self.translator.format(
            "race.results.overall",
            time=format_duration(best.time_ns),
            driver=best.driver_label,
            vehicle=best.vehicle_label,
            lane=best.lane,
        )

    def _show_time_trial(self, race: RaceInfo) -> None:
        bests = self._service.overall_bests(track_id=race.track_id)
        extra = (
            self.translator.translate("race.results.no_measurement")
            if not bests
            else self._best_line(bests[0])
        )
        self.summary.setText(f"{self._header(race)}\n{extra}")
        stored = (
            []
            if race.track_id is None
            else self._service.list_time_measurements(track_id=race.track_id)
        )
        board = build_time_trial_board(
            lane_count=race.lane_count,
            race_id=race.id,
            measurements=stored,
            participants=race.participants,
        )
        fill_lane_table(
            self.records_table,
            [
                (
                    str(line.lane),
                    line.driver_label or "-",
                    line.vehicle_label or "-",
                    format_lap_seconds(line.time_ns),
                )
                for line in board.records
            ],
            time_column=3,
        )
        measurements = self._service.list_time_measurements(race_id=race.id)
        fill_table(
            self.measurements_table,
            [
                (
                    row.driver_label,
                    row.vehicle_label,
                    str(row.lane),
                    format_duration(row.time_ns),
                    format_datetime(row.recorded_at),
                )
                for row in measurements
            ],
            keep_selection=False,
        )
        fill_table(
            self.bests_table,
            [
                (
                    str(row.lane),
                    row.driver_label,
                    row.vehicle_label,
                    format_duration(row.time_ns),
                    format_datetime(row.recorded_at),
                )
                for row in sorted(bests, key=lambda row: (row.lane, row.time_ns))
            ],
            keep_selection=False,
        )
        self._fill_rankings(race.track_id)

    def _fill_rankings(self, track_id: TrackId | None) -> None:
        rankings = self._service.lane_rankings(track_id=track_id)
        fill_table(
            self.ranking_table,
            [
                (
                    str(ranking.lane),
                    str(place),
                    row.driver_label,
                    row.vehicle_label,
                    format_duration(row.time_ns),
                )
                for ranking in rankings
                for place, row in enumerate(ranking.places, start=1)
            ],
            keep_selection=False,
        )

    def _show_lap_race(self, race_id: RaceId, ended: bool) -> None:
        tr = self.translator.translate
        results = self._service.get_results(race_id)
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
