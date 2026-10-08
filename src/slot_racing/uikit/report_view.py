"""One ended race: table, gaps and a lap chart. The figures come from ``core.statistics``."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from slot_racing.core.clock import format_duration
from slot_racing.core.i18n import Translator
from slot_racing.core.statistics import HistoryRace, RaceReport, ReportLine, race_report
from slot_racing.uikit.career import MISSING
from slot_racing.uikit.chart import ChartPoint, ChartSeries, LineChart, series_color
from slot_racing.uikit.theme import set_role
from slot_racing.uikit.widgets import fill_sortable, make_table


class RaceReportView(QWidget):
    """Report for a race that has already ended. An open race shows the empty state."""

    def __init__(self, translator: Translator) -> None:
        super().__init__()
        self._translator = translator
        tr = translator.translate
        self.setObjectName("race-report")
        self.heading = QLabel(tr("report.heading"))
        self.heading.setObjectName("race-report-heading")
        set_role(self.heading, "section")
        self.status = QLabel()
        self.status.setObjectName("race-report-status")
        self.status.setWordWrap(True)
        self.empty = QLabel(tr("report.empty"))
        self.empty.setObjectName("race-report-empty")
        self.empty.setWordWrap(True)
        set_role(self.empty, "caption")
        self.table = make_table(
            [
                tr("report.column.place"),
                tr("report.column.driver"),
                tr("report.column.vehicle"),
                tr("report.column.lane"),
                tr("report.column.laps"),
                tr("report.column.total"),
                tr("report.column.best"),
                tr("report.column.average"),
                tr("report.column.gap"),
                tr("report.column.stdev"),
                tr("report.column.note"),
            ],
            "race-report-table",
        )
        self.chart = LineChart()
        self.chart.setObjectName("race-report-chart")
        self._checks = QHBoxLayout()
        self._boxes: list[QCheckBox] = []
        self._report: RaceReport | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.heading)
        layout.addWidget(self.status)
        layout.addWidget(self.empty)
        layout.addWidget(self.table)
        layout.addLayout(self._checks)
        layout.addWidget(self.chart)

    def show_race(self, race: HistoryRace | None) -> None:
        tr = self._translator.translate
        if race is None:
            self._report = None
            self.empty.setVisible(True)
            self.table.setVisible(False)
            self.chart.setVisible(False)
            self.status.setText("")
            self._clear_checks()
            return
        report = race_report(race)
        self._report = report
        self.empty.setVisible(not report.lines)
        self.table.setVisible(bool(report.lines))
        self.status.setText(tr("report.official" if report.official else "report.aborted"))
        fill_sortable(self.table, [_cells(self._translator, line) for line in report.lines])
        self._build_checks(report)
        self._draw()

    def _build_checks(self, report: RaceReport) -> None:
        self._clear_checks()
        for index, series in enumerate(report.series):
            box = QCheckBox(series.label)
            box.setObjectName(f"race-report-series-{series.participant_id}")
            box.setChecked(True)
            box.toggled.connect(lambda _checked: self._draw())
            self._boxes.append(box)
            self._checks.addWidget(box)
            box.setStyleSheet(f"color: {series_color(index)};")
        self.chart.setVisible(bool(report.series))

    def _clear_checks(self) -> None:
        while self._checks.count():
            item = self._checks.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._boxes = []

    def _draw(self) -> None:
        report = self._report
        if report is None:
            self.chart.set_series(())
            return
        shown = []
        for box, series in zip(self._boxes, report.series, strict=False):
            if not box.isChecked():
                continue
            shown.append(
                ChartSeries(
                    series.label,
                    tuple(
                        ChartPoint(point.lap_number, point.time_ns / 1_000_000_000, point.outlier)
                        for point in series.points
                    ),
                )
            )
        self.chart.set_series(tuple(shown), empty=self._translator.translate("report.no_laps"))


def _cells(translator: Translator, line: ReportLine) -> tuple[str, ...]:
    note = ""
    if line.disqualified:
        note = translator.translate("report.disqualified")
    elif line.outlier_laps:
        note = translator.format(
            "report.outliers", laps=", ".join(str(number) for number in line.outlier_laps)
        )
    lane = MISSING if line.lane is None else str(line.lane)
    place = MISSING if line.position is None else str(line.position)
    return (
        place,
        line.driver_label,
        line.vehicle_label,
        lane,
        str(line.lap_count),
        _time(line.total_time_ns),
        _time(line.best_lap_ns),
        _time(line.average_lap_ns),
        _gap(line.gap_ns),
        _time(line.stdev_ns),
        note,
    )


def _time(value: int | None) -> str:
    return MISSING if value is None else format_duration(value)


def _gap(value: int | None) -> str:
    if value is None:
        return MISSING
    sign = "+" if value >= 0 else "-"
    millis_total = abs(value) // 1_000_000
    seconds, millis = divmod(millis_total, 1000)
    return f"{sign}{seconds},{millis:03d} s"
