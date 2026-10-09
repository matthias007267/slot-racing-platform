"""Stored results of a race.

Lap races stay in the order the race engine ranked them. A time trial shows every measurement,
with driver, vehicle, lane, time and the moment it was recorded, plus best times that never mix
lanes.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionHeader,
    QStyleOptionViewItem,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceId, RaceMode, TrackId
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import (
    TEXT_DRIVER,
    TEXT_HEADER,
    TEXT_PLACE,
    TEXT_ROW,
    TEXT_TIME,
    TEXT_VEHICLE,
    VIEW_RESULTS,
    HudConfigurationStore,
    ViewStyle,
    effective_px,
)
from slot_racing.modules.races.service import RaceService
from slot_racing.modules.races.time_trial_board import build_time_trial_board, format_lap_seconds
from slot_racing.modules.races.types import RaceInfo, TimeBest
from slot_racing.modules.races.ui.formatting import participant_status_key, start_number_text
from slot_racing.modules.races.ui.time_trial_board_view import fill_lane_table
from slot_racing.uikit import fill_table, heading, make_table
from slot_racing.uikit.report_view import RaceReportView
from slot_racing.uikit.theme import configure_page, set_role
from slot_racing.uikit.widgets import format_datetime

_VISIBLE_DATA_ROWS = 5
"""How many data lines a result table keeps on screen before it scrolls."""


class ResultsView(QWidget):
    back_requested = Signal()

    def __init__(
        self,
        translator: Translator,
        service: RaceService,
        hud_store: HudConfigurationStore | None = None,
    ) -> None:
        super().__init__()
        self.translator = translator
        self._service = service
        self._hud_store = hud_store
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
        self.report = RaceReportView(translator)
        self.back_button = QPushButton(tr("race.results.back"))
        self.back_button.setObjectName("results-back")
        set_role(self.back_button, "ghost")
        self.back_button.clicked.connect(self.back_requested.emit)
        self._sized_tables = (
            self.records_table,
            self.measurements_table,
            self.bests_table,
            self.ranking_table,
        )
        self._fitting = False
        content = QWidget()
        content.setObjectName("results-content")
        # Minimum: a short window scrolls this page instead of squeezing a table
        # down to a single data line.
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(content)
        configure_page(layout)
        layout.addWidget(self.header)
        layout.addWidget(self.summary)
        layout.addWidget(self.records_heading)
        layout.addWidget(self.records_table)
        layout.addWidget(self.table)
        layout.addWidget(self.laps_heading)
        layout.addWidget(self.laps_table)
        layout.addWidget(self.measurements_heading)
        layout.addWidget(self.measurements_table)
        layout.addWidget(self.bests_heading)
        layout.addWidget(self.bests_table)
        layout.addWidget(self.ranking_heading)
        layout.addWidget(self.ranking_table)
        layout.addWidget(self.report)
        layout.addWidget(self.back_button)
        self.results_scroll = QScrollArea()
        self.results_scroll.setObjectName("results-scroll")
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.results_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.results_scroll.setWidget(content)
        self.results_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.results_scroll)

    def show_race(self, race_id: RaceId) -> None:
        race = self._service.require_race(race_id)
        self.report.show_race(self._service.get_completed(race_id))
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
        else:
            self.summary.setText(self._header(race))
            self._show_lap_race(race_id, race.is_over)
        self._apply_typography()
        self._fit_tables()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_tables()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_tables()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.ApplicationFontChange):
            # Children may still carry the previous font when this event arrives.
            self._adopt_font()
            self._fit_tables()

    def _adopt_font(self) -> None:
        if not hasattr(self, "_sized_tables"):
            return
        font = self.font()
        for table in self._sized_tables:
            table.setFont(font)

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
        drivers = {row.participant_id: row.driver_label for row in results}
        laps = self._service.get_laps(race_id)
        fill_table(
            self.laps_table,
            [
                (
                    str(lap.lane),
                    drivers.get(lap.participant_id, ""),
                    str(lap.lap_number),
                    format_duration(lap.lap_time_ns),
                    " | ".join(format_duration(t) for t in lap.sector_times_ns),
                )
                for lap in laps
            ],
            keep_selection=False,
        )

    def _apply_typography(self) -> None:
        style = _results_style(self._hud_store)
        if style.font_scale == 100 and not style.texts:
            return
        sizes = {
            role: effective_px(18, style.font_scale, style.text_scale(role))
            for role in (TEXT_HEADER, TEXT_DRIVER, TEXT_VEHICLE, TEXT_PLACE, TEXT_TIME, TEXT_ROW)
        }
        _label_px(self.header, sizes[TEXT_HEADER], bold=True)
        _label_px(self.summary, sizes[TEXT_ROW], bold=False)
        for title in (
            self.laps_heading,
            self.records_heading,
            self.measurements_heading,
            self.bests_heading,
            self.ranking_heading,
        ):
            _label_px(title, sizes[TEXT_HEADER], bold=True)
        _paint_table(
            self.table,
            sizes,
            (
                TEXT_PLACE,
                TEXT_DRIVER,
                TEXT_VEHICLE,
                TEXT_ROW,
                TEXT_ROW,
                TEXT_ROW,
                TEXT_TIME,
                TEXT_TIME,
                TEXT_TIME,
                TEXT_TIME,
                TEXT_ROW,
            ),
        )
        _paint_table(
            self.laps_table,
            sizes,
            (TEXT_ROW, TEXT_DRIVER, TEXT_ROW, TEXT_TIME, TEXT_TIME),
        )
        _paint_table(self.records_table, sizes, (TEXT_ROW, TEXT_DRIVER, TEXT_VEHICLE, TEXT_TIME))
        _paint_table(
            self.measurements_table,
            sizes,
            (TEXT_DRIVER, TEXT_VEHICLE, TEXT_ROW, TEXT_TIME, TEXT_ROW),
        )
        _paint_table(
            self.bests_table,
            sizes,
            (TEXT_ROW, TEXT_DRIVER, TEXT_VEHICLE, TEXT_TIME, TEXT_ROW),
        )
        _paint_table(
            self.ranking_table,
            sizes,
            (TEXT_ROW, TEXT_PLACE, TEXT_DRIVER, TEXT_VEHICLE, TEXT_TIME),
        )

    def _fit_tables(self) -> None:
        if getattr(self, "_fitting", False) or not hasattr(self, "_sized_tables"):
            return
        self._fitting = True
        try:
            for table in self._sized_tables:
                _fit_data_rows(table, visible_rows=_VISIBLE_DATA_ROWS)
        finally:
            self._fitting = False


def _results_style(store: HudConfigurationStore | None) -> ViewStyle:
    if store is None:
        return ViewStyle()
    return store.load_default_layout().view(VIEW_RESULTS)


def _label_px(label: QLabel, pixels: int, *, bold: bool) -> None:
    font = label.font()
    font.setPixelSize(pixels)
    font.setBold(bold)
    label.setFont(font)


def _fit_data_rows(table: QTableWidget, *, visible_rows: int) -> None:
    """Size the table from its header, rows, frame and a horizontal bar.

    More than ``visible_rows`` lines stay inside the table and scroll. Fewer lines
    use a shorter table. The height is never a fixed pixel constant.
    """
    table.ensurePolished()
    row_height = _commit_row_height(table, _row_height(table))
    shown = 1 if table.rowCount() < 1 else min(table.rowCount(), visible_rows)
    header_height = _header_height(table)
    frame = table.frameWidth() * 2
    bar = _horizontal_bar_height(table)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    table.setFixedHeight(header_height + row_height * shown + frame + bar)
    _match_viewport(table, shown, row_height)


def _commit_row_height(table: QTableWidget, height: int) -> int:
    """Apply ``height``, then keep the size Qt actually uses for the section.

    The style refuses a section below its minimum, and a stylesheet can make the
    painted line taller than the first measurement. The value written back is that
    painted line, so the viewport is not one row short.
    """
    vertical = table.verticalHeader()
    height = max(height, vertical.minimumSectionSize())
    vertical.setDefaultSectionSize(height)
    vertical.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    for row in range(table.rowCount()):
        table.setRowHeight(row, height)
    if table.rowCount() < 1:
        return max(height, vertical.defaultSectionSize())
    actual = table.rowHeight(0)
    if actual > height:
        vertical.setDefaultSectionSize(actual)
        for row in range(table.rowCount()):
            table.setRowHeight(row, actual)
        actual = max(actual, table.rowHeight(0))
    return actual


def _match_viewport(table: QTableWidget, shown: int, row_height: int) -> None:
    """Grow or shrink until the data area holds exactly ``shown`` complete lines."""
    if row_height < 1 or not table.isVisible():
        return
    for _ in range(2):
        viewport = table.viewport().height()
        if viewport <= 0:
            return
        delta = shown * row_height - viewport
        if delta == 0:
            return
        updated = table.height() + delta
        if updated <= 0 or updated == table.height():
            return
        table.setFixedHeight(updated)


def _row_height(table: QTableWidget) -> int:
    """Delegate hint, stylesheet box and the tallest font, so a line is not cut off."""
    hinted = table.sizeHintForRow(0) if table.rowCount() else 0
    font = table.font()
    tallest = table.fontMetrics().height()
    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is None:
                continue
            item_font = item.font()
            item_height = QFontMetrics(item_font).height()
            if item_height > tallest:
                tallest = item_height
                font = item_font
    metrics = QFontMetrics(font)
    option = QStyleOptionViewItem()
    option.initFrom(table)
    option.font = font
    option.text = "Ag"
    option.features = QStyleOptionViewItem.ViewItemFeature.HasDisplay
    measured = table.style().sizeFromContents(
        QStyle.ContentsType.CT_ItemViewItem,
        option,
        QSize(metrics.horizontalAdvance(option.text), metrics.height()),
        table,
    )
    return max(hinted, measured.height(), tallest, table.verticalHeader().minimumSectionSize())


def _header_height(table: QTableWidget) -> int:
    header = table.horizontalHeader()
    header.ensurePolished()
    metrics = header.fontMetrics()
    option = QStyleOptionHeader()
    option.initFrom(header)
    option.fontMetrics = metrics
    option.text = "Ag"
    measured = header.style().sizeFromContents(
        QStyle.ContentsType.CT_HeaderSection,
        option,
        QSize(metrics.horizontalAdvance(option.text), metrics.height()),
        header,
    )
    return max(header.sizeHint().height(), measured.height(), header.height())


def _horizontal_bar_height(table: QTableWidget) -> int:
    policy = table.horizontalScrollBarPolicy()
    if policy == Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
        return 0
    bar = table.horizontalScrollBar()
    if policy == Qt.ScrollBarPolicy.ScrollBarAlwaysOn or bar.isVisible():
        return bar.sizeHint().height()
    available = table.viewport().width()
    if available <= 0:
        available = max(0, table.width() - table.frameWidth() * 2)
    if available > 0 and table.horizontalHeader().length() > available:
        return bar.sizeHint().height()
    return 0


def _paint_table(table: QTableWidget, sizes: dict[str, int], columns: tuple[str, ...]) -> None:
    header_font = QFont(table.horizontalHeader().font())
    header_font.setPixelSize(sizes[TEXT_HEADER])
    table.horizontalHeader().setFont(header_font)
    for row in range(table.rowCount()):
        for column, role in enumerate(columns):
            item = table.item(row, column)
            if item is None:
                continue
            font = QFont(item.font())
            font.setPixelSize(sizes[role])
            item.setFont(font)
