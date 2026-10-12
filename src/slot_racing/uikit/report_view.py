"""One ended race: table, gaps and a lap chart. The figures come from ``core.statistics``."""

from __future__ import annotations

import html

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from slot_racing.core.clock import format_duration
from slot_racing.core.i18n import Translator
from slot_racing.core.lap_chart import LapChart, LapChartPoint, LapChartSeries, build_lap_chart
from slot_racing.core.statistics import HistoryRace, RaceReport, ReportLine, race_report
from slot_racing.uikit.career import MISSING
from slot_racing.uikit.chart import ChartPoint, ChartRun, ChartSeries, LineChart, driver_color
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
        self.chart_note = QLabel()
        self.chart_note.setObjectName("race-report-chart-note")
        self.chart_note.setWordWrap(True)
        self.chart_note.setVisible(False)
        set_role(self.chart_note, "caption")
        self.reset_zoom = QPushButton(tr("report.zoom_reset"))
        self.reset_zoom.setObjectName("race-report-zoom-reset")
        self.reset_zoom.clicked.connect(self.chart.reset_view)
        self.reset_zoom.setVisible(False)
        self._checks = QHBoxLayout()
        self._boxes: list[QCheckBox] = []
        self._report: RaceReport | None = None
        self._chart_model: LapChart | None = None
        checks = QHBoxLayout()
        checks.addLayout(self._checks, 1)
        checks.addWidget(self.reset_zoom)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.heading)
        layout.addWidget(self.status)
        layout.addWidget(self.empty)
        layout.addWidget(self.table)
        layout.addLayout(checks)
        layout.addWidget(self.chart_note)
        layout.addWidget(self.chart)

    def show_race(self, race: HistoryRace | None) -> None:
        tr = self._translator.translate
        if race is None:
            self._report = None
            self._chart_model = None
            self.empty.setVisible(True)
            self.table.setVisible(False)
            self.chart.setVisible(False)
            self.chart_note.setVisible(False)
            self.reset_zoom.setVisible(False)
            self.status.setText("")
            self._clear_checks()
            return
        report = race_report(race)
        self._report = report
        self._chart_model = build_lap_chart(race)
        self.empty.setVisible(not report.lines)
        self.table.setVisible(bool(report.lines))
        self.status.setText(tr("report.official" if report.official else "report.aborted"))
        fill_sortable(self.table, [_cells(self._translator, line) for line in report.lines])
        self._build_checks(report)
        unaligned = bool(self._chart_model.series) and not self._chart_model.aligned
        self.chart_note.setVisible(unaligned)
        self.chart_note.setText(tr("report.runs_unaligned") if unaligned else "")
        self.reset_zoom.setVisible(bool(self._chart_model.series))
        self.chart.reset_view()
        self._draw()

    def _build_checks(self, report: RaceReport) -> None:
        self._clear_checks()
        for series in report.series:
            box = QCheckBox(series.label)
            box.setObjectName(f"race-report-series-{series.participant_id}")
            box.setChecked(True)
            box.toggled.connect(lambda _checked: self._draw())
            self._boxes.append(box)
            self._checks.addWidget(box)
            box.setStyleSheet(f"color: {driver_color(series.participant_id)};")
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
        model = self._chart_model
        if report is None or model is None:
            self.chart.set_series(())
            return
        by_id = {item.participant_id: item for item in model.series}
        shown = []
        for box, series in zip(self._boxes, report.series, strict=False):
            if not box.isChecked():
                continue
            source = by_id.get(series.participant_id)
            if source is None:
                continue
            shown.append(
                ChartSeries(
                    source.driver_label,
                    tuple(_chart_point(self._translator, source, point) for point in source.points),
                    driver_color(source.participant_id),
                )
            )
        runs = tuple(
            ChartRun(
                label=self._translator.format("report.run", number=run.run_number),
                x_start=run.x_start,
                x_end=run.x_end,
                run_number=run.run_number,
            )
            for run in model.runs
        )
        self.chart.set_series(
            tuple(shown),
            empty=self._translator.translate("report.no_laps"),
            runs=runs,
            axes=True,
            keep_view=True,
        )


def _chart_point(
    translator: Translator, series: LapChartSeries, point: LapChartPoint
) -> ChartPoint:
    return ChartPoint(
        point.x,
        point.lap_time_ns / 1_000_000_000,
        point.outlier,
        "" if point.lap_in_run is None else str(point.lap_in_run),
        _tip(translator, series, point),
        point.run_number,
    )


def _tip(translator: Translator, series: LapChartSeries, point: LapChartPoint) -> str:
    unknown = translator.translate("report.run_unknown")
    run = unknown if point.run_number is None else str(point.run_number)
    lane = unknown if point.lane is None else str(point.lane)
    local = unknown if point.lap_in_run is None else str(point.lap_in_run)
    lines = [f"<b>{html.escape(series.driver_label)}</b>"]
    vehicle = series.vehicle_label.strip()
    if vehicle and vehicle != "-":
        lines.append(html.escape(translator.format("report.tip.vehicle", vehicle=vehicle)))
    lines.append(html.escape(translator.format("report.tip.run", run=run)))
    lines.append(html.escape(translator.format("report.tip.lane", lane=lane)))
    lines.append(html.escape(translator.format("report.tip.lap", lap=local)))
    lines.append(html.escape(translator.format("report.tip.absolute", lap=str(point.absolute_lap))))
    lines.append(
        html.escape(translator.format("report.tip.time", time=format_duration(point.lap_time_ns)))
    )
    return "<br>".join(lines)


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
