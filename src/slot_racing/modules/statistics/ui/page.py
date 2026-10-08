"""Statistics page. Lanes follow the selected track. Records follow one layout."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import RaceHistoryCatalog, TimeMeasurementCatalog, TrackCatalog
from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceId, TrackId
from slot_racing.core.i18n import Translator
from slot_racing.core.statistics import TimeScope, track_records
from slot_racing.modules.statistics.lanes import lane_statistic_cells, statistics_for_track
from slot_racing.uikit import fill_table, make_table
from slot_racing.uikit.career import MISSING
from slot_racing.uikit.report_view import RaceReportView
from slot_racing.uikit.theme import configure_page, set_role
from slot_racing.uikit.widgets import fill_sortable, selected_id


class RecordDialog(QDialog):
    """The ended race behind one record. The report is the same view the race result uses."""

    def __init__(
        self,
        translator: Translator,
        catalog: RaceHistoryCatalog,
        race_id: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("statistics-record-dialog")
        self.setWindowTitle(translator.translate("records.detail"))
        self.setMinimumSize(880, 560)
        self.report = RaceReportView(translator)
        layout = QVBoxLayout(self)
        layout.addWidget(self.report)
        self.report.show_race(catalog.get_completed(RaceId(race_id)))


class StatisticsPage(QWidget):
    """Best time of each lane, plus the fastest valid laps of one layout."""

    def __init__(
        self,
        translator: Translator,
        tracks: TrackCatalog,
        measurements: Callable[[], TimeMeasurementCatalog | None],
        history: Callable[[], RaceHistoryCatalog | None] | None = None,
        drivers: Callable[[], Sequence[tuple[int, str]]] | None = None,
        vehicles: Callable[[], Sequence[tuple[int, str]]] | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("statistics-page")
        self._translator = translator
        self._tracks = tracks
        self._measurements = measurements
        self._history = history
        self._drivers = drivers or (lambda: ())
        self._vehicles = vehicles or (lambda: ())
        self.dialog_runner: Callable[[QDialog], int] = lambda dialog: dialog.exec()
        tr = translator.translate
        self.track_combo = QComboBox()
        self.track_combo.setObjectName("statistics-track")
        track_label = QLabel(tr("statistics.track"))
        track_label.setObjectName("statistics-track-label")
        choice = QHBoxLayout()
        choice.setContentsMargins(0, 0, 0, 0)
        choice.addWidget(track_label)
        choice.addWidget(self.track_combo, 1)
        self.section = QLabel(tr("statistics.lanes"))
        self.section.setObjectName("statistics-lanes-heading")
        set_role(self.section, "section")
        self.empty = QLabel(tr("statistics.empty"))
        self.empty.setObjectName("statistics-empty")
        set_role(self.empty, "caption")
        self.table = make_table(
            [
                tr("statistics.column.lane"),
                tr("statistics.column.driver"),
                tr("statistics.column.vehicle"),
                tr("statistics.column.best"),
            ],
            "statistics-lanes",
        )
        self.records_heading = QLabel(tr("records.heading"))
        self.records_heading.setObjectName("statistics-records-heading")
        set_role(self.records_heading, "section")
        self.layout_combo = QComboBox()
        self.layout_combo.setObjectName("statistics-layout")
        self.lane_combo = QComboBox()
        self.lane_combo.setObjectName("statistics-lane")
        self.driver_combo = QComboBox()
        self.driver_combo.setObjectName("statistics-driver")
        self.vehicle_combo = QComboBox()
        self.vehicle_combo.setObjectName("statistics-vehicle")
        self.since_check = QCheckBox(tr("records.since"))
        self.since_check.setObjectName("statistics-since")
        self.since_edit = _date_edit()
        self.until_check = QCheckBox(tr("records.until"))
        self.until_check.setObjectName("statistics-until")
        self.until_edit = _date_edit()
        self.until_edit.setDate(QDate.currentDate())
        self.since_edit.setDate(QDate.currentDate().addMonths(-12))
        self.records_empty = QLabel(tr("records.empty"))
        self.records_empty.setObjectName("statistics-records-empty")
        self.records_empty.setWordWrap(True)
        set_role(self.records_empty, "caption")
        self.records_table = make_table(
            [
                tr("records.column.time"),
                tr("records.column.driver"),
                tr("records.column.vehicle"),
                tr("records.column.lane"),
                tr("records.column.date"),
                tr("records.column.race"),
                tr("records.column.lap"),
            ],
            "statistics-records",
        )
        filters = QHBoxLayout()
        filters.addWidget(self.layout_combo, 2)
        filters.addWidget(self.lane_combo, 1)
        filters.addWidget(self.driver_combo, 2)
        filters.addWidget(self.vehicle_combo, 2)
        period = QHBoxLayout()
        period.addWidget(self.since_check)
        period.addWidget(self.since_edit)
        period.addWidget(self.until_check)
        period.addWidget(self.until_edit)
        period.addStretch(1)
        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addLayout(choice)
        layout.addWidget(self.section)
        layout.addWidget(self.empty)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.records_heading)
        layout.addLayout(filters)
        layout.addLayout(period)
        layout.addWidget(self.records_empty)
        layout.addWidget(self.records_table, 2)
        self.track_combo.currentIndexChanged.connect(lambda _index: self._show_selected())
        for widget in (self.layout_combo, self.lane_combo, self.driver_combo, self.vehicle_combo):
            widget.currentIndexChanged.connect(lambda _index: self._show_records())
        self.since_check.toggled.connect(lambda _checked: self._show_records())
        self.until_check.toggled.connect(lambda _checked: self._show_records())
        self.since_edit.dateChanged.connect(lambda _date: self._show_records())
        self.until_edit.dateChanged.connect(lambda _date: self._show_records())
        self.records_table.cellDoubleClicked.connect(lambda *_args: self.show_selected_record())
        self.refresh()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        """Reload tracks and the lanes of the selected one. Stored data is only read."""
        selected = self.track_combo.currentData()
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        for track in self._tracks.list_tracks():
            self.track_combo.addItem(track.name, track.id)
        if selected is not None:
            index = self.track_combo.findData(selected)
            if index >= 0:
                self.track_combo.setCurrentIndex(index)
        self.track_combo.blockSignals(False)
        self._fill_people()
        self._show_selected()

    def show_selected_record(self) -> None:
        """Open the race that produced the selected lap. A missing race shows the empty report."""
        race_id = selected_id(self.records_table)
        catalog = None if self._history is None else self._history()
        if race_id is None or catalog is None:
            return
        self.dialog_runner(RecordDialog(self._translator, catalog, race_id, self))

    def _show_selected(self) -> None:
        raw = self.track_combo.currentData()
        track = None if raw is None else self._tracks.get_track(TrackId(raw))
        has_track = track is not None
        self.empty.setVisible(not has_track)
        self.section.setVisible(has_track)
        self.table.setVisible(has_track)
        self._set_records_visible(has_track)
        if track is None:
            fill_table(self.table, [])
            fill_sortable(self.records_table, [])
            return
        catalog = self._measurements()
        rows = () if catalog is None else catalog.list_for_track(track.id)
        lines = statistics_for_track(track, rows)
        fill_table(self.table, [lane_statistic_cells(line) for line in lines])
        self._fill_layouts()
        self._fill_lanes(track.lane_count)
        self._show_records()

    def _show_records(self) -> None:
        raw = self.track_combo.currentData()
        catalog = None if self._history is None else self._history()
        if raw is None or catalog is None:
            fill_sortable(self.records_table, [])
            self.records_empty.setVisible(self.track_combo.currentData() is not None)
            self.records_table.setVisible(False)
            return
        lines = track_records(catalog.list_completed(), self._scope(int(raw)))
        tr = self._translator.translate
        self.records_empty.setText(tr("records.empty"))
        self.records_empty.setVisible(not lines)
        self.records_table.setVisible(bool(lines))
        fill_sortable(
            self.records_table,
            [
                (
                    format_duration(line.time_ns),
                    line.driver_label,
                    line.vehicle_label,
                    MISSING if line.lane is None else str(line.lane),
                    MISSING if line.when is None else line.when.strftime("%d.%m.%Y"),
                    line.race_name,
                    str(line.lap_number),
                )
                for line in lines
            ],
            [line.race_id for line in lines],
        )

    def _scope(self, track_id: int) -> TimeScope:
        layout = self.layout_combo.currentData()
        driver = self.driver_combo.currentData()
        vehicle = self.vehicle_combo.currentData()
        lane = self.lane_combo.currentData()
        return TimeScope(
            track_id=track_id,
            layout_id=layout if isinstance(layout, int) else None,
            unassigned=layout == "unassigned",
            driver_id=driver if isinstance(driver, int) else None,
            vehicle_id=vehicle if isinstance(vehicle, int) else None,
            lane=lane if isinstance(lane, int) else None,
            since=_selected_date(self.since_check, self.since_edit),
            until=_selected_date(self.until_check, self.until_edit),
        )

    def _fill_layouts(self) -> None:
        selected = self.layout_combo.currentData()
        raw = self.track_combo.currentData()
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        tr = self._translator.translate
        self.layout_combo.addItem(tr("records.unassigned"), "unassigned")
        current_id: int | None = None
        catalog = None if self._history is None else self._history()
        if catalog is not None and raw is not None:
            for revision in catalog.list_layouts(TrackId(raw)):
                label = tr("records.layout_current" if revision.current else "records.layout_old")
                self.layout_combo.addItem(
                    self._translator.format(
                        "records.layout", name=label, number=revision.layout_id
                    ),
                    revision.layout_id,
                )
                if revision.current:
                    current_id = revision.layout_id
        if selected is not None:
            index = self.layout_combo.findData(selected)
            if index >= 0:
                self.layout_combo.setCurrentIndex(index)
        elif current_id is not None:
            self.layout_combo.setCurrentIndex(max(0, self.layout_combo.findData(current_id)))
        self.layout_combo.blockSignals(False)

    def _fill_lanes(self, lane_count: int) -> None:
        selected = self.lane_combo.currentData()
        self.lane_combo.blockSignals(True)
        self.lane_combo.clear()
        self.lane_combo.addItem(self._translator.translate("records.all_lanes"), None)
        for lane in range(1, lane_count + 1):
            self.lane_combo.addItem(str(lane), lane)
        if isinstance(selected, int):
            index = self.lane_combo.findData(selected)
            if index >= 0:
                self.lane_combo.setCurrentIndex(index)
        self.lane_combo.blockSignals(False)

    def _fill_people(self) -> None:
        self._fill_choice(self.driver_combo, "records.all_drivers", self._drivers())
        self._fill_choice(self.vehicle_combo, "records.all_vehicles", self._vehicles())

    def _fill_choice(
        self, combo: QComboBox, all_key: str, choices: Sequence[tuple[int, str]]
    ) -> None:
        selected = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(self._translator.translate(all_key), None)
        for choice_id, label in choices:
            combo.addItem(label, choice_id)
        if isinstance(selected, int):
            index = combo.findData(selected)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _set_records_visible(self, visible: bool) -> None:
        for widget in (
            self.records_heading,
            self.layout_combo,
            self.lane_combo,
            self.driver_combo,
            self.vehicle_combo,
            self.since_check,
            self.since_edit,
            self.until_check,
            self.until_edit,
            self.records_empty,
            self.records_table,
        ):
            widget.setVisible(visible)


def _date_edit() -> QDateEdit:
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd.MM.yyyy")
    edit.setDate(QDate.currentDate())
    return edit


def _selected_date(check: QCheckBox, edit: QDateEdit) -> date | None:
    if not check.isChecked():
        return None
    chosen = edit.date()
    return date(chosen.year(), chosen.month(), chosen.day())
