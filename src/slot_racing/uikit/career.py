"""Career figures for one driver or one vehicle. The numbers come from ``core.statistics``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import RaceHistoryCatalog, TrackInfo
from slot_racing.core.clock import format_duration
from slot_racing.core.domain import TrackId
from slot_racing.core.i18n import Translator
from slot_racing.core.statistics import CareerSummary, TimeScope, career_summary
from slot_racing.uikit.chart import ChartPoint, ChartSeries, LineChart
from slot_racing.uikit.theme import set_role
from slot_racing.uikit.widgets import fill_sortable, make_table

MISSING = "–"  # noqa: RUF001


class CareerPanel(QWidget):
    """Filters and the resulting history. An open race is never part of the figures."""

    def __init__(
        self,
        translator: Translator,
        history: Callable[[], RaceHistoryCatalog | None],
        tracks: Callable[[], Sequence[TrackInfo]],
        choices: Callable[[], Sequence[tuple[int, str]]],
        *,
        subject: str,
    ) -> None:
        super().__init__()
        self._translator = translator
        self._history = history
        self._tracks = tracks
        self._choices = choices
        self._subject = subject
        self._subject_id: int | None = None
        self._refreshing = False
        self._track_lanes: dict[int, int] = {}
        tr = translator.translate
        self.setObjectName(f"career-{subject}")
        self.heading = QLabel(tr("career.heading"))
        self.heading.setObjectName(f"career-{subject}-heading")
        set_role(self.heading, "section")
        self.track_combo = QComboBox()
        self.track_combo.setObjectName(f"career-{subject}-track")
        self.layout_combo = QComboBox()
        self.layout_combo.setObjectName(f"career-{subject}-layout")
        self.choice_combo = QComboBox()
        self.choice_combo.setObjectName(f"career-{subject}-choice")
        self.lane_combo = QComboBox()
        self.lane_combo.setObjectName(f"career-{subject}-lane")
        self.since_check = QCheckBox(tr("career.since"))
        self.since_check.setObjectName(f"career-{subject}-since")
        self.since_edit = QDateEdit()
        self.since_edit.setCalendarPopup(True)
        self.since_edit.setDisplayFormat("dd.MM.yyyy")
        self.since_edit.setDate(QDate.currentDate().addMonths(-12))
        self.until_check = QCheckBox(tr("career.until"))
        self.until_check.setObjectName(f"career-{subject}-until")
        self.until_edit = QDateEdit()
        self.until_edit.setCalendarPopup(True)
        self.until_edit.setDisplayFormat("dd.MM.yyyy")
        self.until_edit.setDate(QDate.currentDate())
        self.empty = QLabel(tr("career.empty"))
        self.empty.setObjectName(f"career-{subject}-empty")
        self.empty.setWordWrap(True)
        set_role(self.empty, "caption")
        self.note = QLabel(tr("career.note"))
        self.note.setObjectName(f"career-{subject}-note")
        self.note.setWordWrap(True)
        set_role(self.note, "caption")
        self.metrics = QWidget()
        self.metrics.setObjectName(f"career-{subject}-metrics")
        metric_grid = QGridLayout(self.metrics)
        metric_grid.setContentsMargins(0, 0, 0, 0)
        self._values: dict[str, QLabel] = {}
        for index, (key, name) in enumerate(_METRICS):
            caption = QLabel(tr(key))
            set_role(caption, "caption")
            value = QLabel(MISSING)
            value.setObjectName(f"career-{subject}-{name}")
            metric_grid.addWidget(caption, index // 4, (index % 4) * 2)
            metric_grid.addWidget(value, index // 4, (index % 4) * 2 + 1)
            self._values[name] = value
        self.history_table = make_table(
            [
                tr("career.column.date"),
                tr("career.column.race"),
                tr("career.column.mode"),
                tr("career.column.place"),
                tr("career.column.laps"),
                tr("career.column.best"),
            ],
            f"career-{subject}-history",
        )
        self.chart = LineChart()
        self.chart.setObjectName(f"career-{subject}-chart")
        self.history_table.setMaximumHeight(180)
        filters = QHBoxLayout()
        filters.addWidget(self.track_combo, 2)
        filters.addWidget(self.layout_combo, 2)
        filters.addWidget(self.choice_combo, 2)
        filters.addWidget(self.lane_combo, 1)
        period = QHBoxLayout()
        period.addWidget(self.since_check)
        period.addWidget(self.since_edit)
        period.addWidget(self.until_check)
        period.addWidget(self.until_edit)
        period.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.heading)
        layout.addLayout(filters)
        layout.addLayout(period)
        layout.addWidget(self.note)
        layout.addWidget(self.empty)
        layout.addWidget(self.metrics)
        layout.addWidget(self.history_table)
        layout.addWidget(self.chart)
        self.track_combo.currentIndexChanged.connect(lambda _index: self._fill_layouts())
        for widget in (
            self.layout_combo,
            self.choice_combo,
            self.lane_combo,
            self.since_check,
            self.until_check,
            self.since_edit,
            self.until_edit,
        ):
            signal = getattr(widget, "currentIndexChanged", None)
            if signal is not None:
                signal.connect(lambda _index: self.refresh())
        self.since_check.toggled.connect(lambda _checked: self.refresh())
        self.until_check.toggled.connect(lambda _checked: self.refresh())
        self.since_edit.dateChanged.connect(lambda _date: self.refresh())
        self.until_edit.dateChanged.connect(lambda _date: self.refresh())

    def show_subject(self, subject_id: int | None) -> None:
        self._subject_id = subject_id
        self.refresh()

    def refresh(self) -> None:
        """Read the ended races again. Deleting a race removes it from the next refresh."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self._fill_tracks()
            self._fill_choices()
            self._show(self._summary(self._history()))
        finally:
            self._refreshing = False

    def _summary(self, catalog: RaceHistoryCatalog | None) -> CareerSummary | None:
        if catalog is None or self._subject_id is None:
            return None
        scope = self._scope()
        races = catalog.list_completed()
        if self._subject == "driver":
            return career_summary(races, scope, driver_id=self._subject_id)
        return career_summary(races, scope, vehicle_id=self._subject_id)

    def _scope(self) -> TimeScope:
        track_id = self.track_combo.currentData()
        layout = self.layout_combo.currentData()
        choice = self.choice_combo.currentData()
        lane = self.lane_combo.currentData()
        unassigned = layout == "unassigned"
        layout_id = layout if isinstance(layout, int) else None
        driver_id = choice if self._subject == "vehicle" and isinstance(choice, int) else None
        vehicle_id = choice if self._subject == "driver" and isinstance(choice, int) else None
        return TimeScope(
            track_id=track_id if isinstance(track_id, int) else None,
            layout_id=layout_id,
            unassigned=unassigned,
            driver_id=driver_id,
            vehicle_id=vehicle_id,
            lane=lane if isinstance(lane, int) else None,
            since=_selected_date(self.since_check, self.since_edit),
            until=_selected_date(self.until_check, self.until_edit),
        )

    def _fill_tracks(self) -> None:
        selected = self.track_combo.currentData()
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        self.track_combo.addItem(self._translator.translate("career.all_tracks"), None)
        self._track_lanes = {}
        for track in self._tracks():
            self.track_combo.addItem(track.name, int(track.id))
            self._track_lanes[int(track.id)] = track.lane_count
        if selected is not None:
            index = self.track_combo.findData(selected)
            if index >= 0:
                self.track_combo.setCurrentIndex(index)
        self.track_combo.blockSignals(False)
        self._fill_layouts(refresh=False)

    def _fill_layouts(self, refresh: bool = True) -> None:
        selected = self.layout_combo.currentData()
        track_id = self.track_combo.currentData()
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        tr = self._translator.translate
        self.layout_combo.addItem(tr("career.all_layouts"), None)
        self.layout_combo.addItem(tr("career.unassigned"), "unassigned")
        catalog = self._history()
        if catalog is not None and isinstance(track_id, int):
            for revision in catalog.list_layouts(TrackId(track_id)):
                label = tr("career.layout_current" if revision.current else "career.layout_old")
                self.layout_combo.addItem(
                    self._translator.format("career.layout", name=label, number=revision.layout_id),
                    revision.layout_id,
                )
        if selected is not None:
            index = self.layout_combo.findData(selected)
            if index >= 0:
                self.layout_combo.setCurrentIndex(index)
        self.layout_combo.blockSignals(False)
        self._fill_lanes()
        if refresh:
            self.refresh()

    def _fill_lanes(self) -> None:
        selected = self.lane_combo.currentData()
        track_id = self.track_combo.currentData()
        count = self._track_lanes.get(track_id, 0) if isinstance(track_id, int) else 0
        self.lane_combo.blockSignals(True)
        self.lane_combo.clear()
        self.lane_combo.addItem(self._translator.translate("career.all_lanes"), None)
        for lane in range(1, count + 1):
            self.lane_combo.addItem(str(lane), lane)
        if isinstance(selected, int):
            index = self.lane_combo.findData(selected)
            if index >= 0:
                self.lane_combo.setCurrentIndex(index)
        self.lane_combo.blockSignals(False)

    def _fill_choices(self) -> None:
        selected = self.choice_combo.currentData()
        self.choice_combo.blockSignals(True)
        self.choice_combo.clear()
        key = "career.all_vehicles" if self._subject == "driver" else "career.all_drivers"
        self.choice_combo.addItem(self._translator.translate(key), None)
        for choice_id, label in self._choices():
            self.choice_combo.addItem(label, choice_id)
        if isinstance(selected, int):
            index = self.choice_combo.findData(selected)
            if index >= 0:
                self.choice_combo.setCurrentIndex(index)
        self.choice_combo.blockSignals(False)

    def _show(self, summary: CareerSummary | None) -> None:
        tr = self._translator.translate
        if self._subject_id is None or summary is None:
            self.empty.setText(tr("career.none_selected"))
            self.empty.setVisible(True)
            self.metrics.setVisible(False)
            self.history_table.setVisible(False)
            self.chart.setVisible(False)
            self.note.setVisible(False)
            return
        comparable = summary.best_lap_ns is not None or summary.laps == 0
        self.note.setVisible(summary.races > 0 and summary.best_lap_ns is None and summary.laps > 0)
        self.empty.setVisible(summary.races == 0)
        self.empty.setText(tr("career.empty"))
        self.metrics.setVisible(summary.races > 0)
        self.history_table.setVisible(summary.races > 0)
        self._values["races"].setText(str(summary.races))
        self._values["wins"].setText(str(summary.wins))
        self._values["podiums"].setText(str(summary.podiums))
        self._values["laps"].setText(str(summary.laps))
        self._values["best"].setText(_time(summary.best_lap_ns))
        self._values["average"].setText(_time(summary.average_lap_ns))
        self._values["stdev"].setText(_time(summary.stdev_ns))
        self._values["distance"].setText(MISSING)
        fill_sortable(
            self.history_table,
            [
                (
                    _day(row.when),
                    row.name,
                    tr(f"career.mode.{row.mode.value}"),
                    MISSING if row.position is None else str(row.position),
                    str(row.laps),
                    _time(row.best_lap_ns),
                )
                for row in summary.history
            ],
        )
        points = tuple(
            ChartPoint(index, time_ns / 1_000_000_000)
            for index, (_moment, time_ns) in enumerate(summary.trend, start=1)
        )
        waiting = summary.races > 0 and summary.laps > 0 and summary.best_lap_ns is None
        self.chart.setVisible(bool(points) or waiting)
        self.chart.set_series(
            (ChartSeries(tr("career.trend"), points),) if points else (),
            empty="" if points or not comparable else tr("career.no_trend"),
        )


_METRICS = (
    ("career.races", "races"),
    ("career.wins", "wins"),
    ("career.podiums", "podiums"),
    ("career.laps", "laps"),
    ("career.best", "best"),
    ("career.average", "average"),
    ("career.stdev", "stdev"),
    ("career.distance", "distance"),
)


def _selected_date(check: QCheckBox, edit: QDateEdit) -> date | None:
    if not check.isChecked():
        return None
    chosen = edit.date()
    return date(chosen.year(), chosen.month(), chosen.day())


def _time(value: int | None) -> str:
    return MISSING if value is None else format_duration(value)


def _day(moment: datetime | None) -> str:
    return MISSING if moment is None else moment.strftime("%d.%m.%Y")
