"""Time-trial screen: lane records, who is driving, and the last laps of this attempt.

The lap-race HUD stays untouched. This view only presents a :class:`TimeTrialBoard`.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.clock import format_duration
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.time_trial_board import (
    TimeTrialBoard,
    format_lap_seconds,
)
from slot_racing.uikit.theme import COLORS, SPACE, set_role, set_tone
from slot_racing.uikit.widgets import fill_table, make_table

_BADGE_PX = 36
_LANE_PX = 26
_BODY_PX = 15
_ROW_PX = 30
_HEADER_PX = 28
_MISSING = "-"


class TimeTrialBoardView(QWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__()
        self.translator = translator
        self.setObjectName("time-trial-board")
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        tr = translator.translate

        self.name_label = QLabel()
        self.name_label.setObjectName("time-trial-name")
        set_role(self.name_label, "page-title")
        self.status_label = QLabel()
        self.status_label.setObjectName("time-trial-status")
        set_role(self.status_label, "status")
        self.elapsed_label = QLabel()
        self.elapsed_label.setObjectName("time-trial-elapsed")
        set_role(self.elapsed_label, "telemetry")
        self.warning = QLabel()
        self.warning.setObjectName("time-trial-warning")
        self.warning.setWordWrap(True)
        set_role(self.warning, "caption")
        set_tone(self.warning, "warn")
        self.warning.hide()

        self.records_table = _lane_table(
            [
                tr("race.time_trial.column.lane"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
                tr("race.time_trial.column.best"),
            ],
            "time-trial-records",
        )
        self.active_table = _lane_table(
            [
                tr("race.time_trial.column.lane"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
            ],
            "time-trial-active",
        )
        records_section = _section(tr("race.time_trial.records"), "time-trial-records-section")
        records_section.body.addWidget(self.records_table)
        active_section = _section(tr("race.time_trial.active"), "time-trial-active-section")
        active_section.body.addWidget(self.active_table)
        laps_section = _section(tr("race.time_trial.laps"), "time-trial-laps-section")
        self.attempts = QWidget()
        self.attempts.setObjectName("time-trial-attempts")
        self.attempt_row = QHBoxLayout(self.attempts)
        self.attempt_row.setContentsMargins(0, 0, 0, 0)
        self.attempt_row.setSpacing(SPACE.md)
        laps_section.body.addWidget(self.attempts)

        content = QWidget()
        content.setObjectName("time-trial-content")
        content.setMinimumWidth(0)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(SPACE.md)
        summaries = QHBoxLayout()
        summaries.setSpacing(SPACE.md)
        summaries.addWidget(records_section, 1)
        summaries.addWidget(active_section, 1)
        content_layout.addLayout(summaries)
        content_layout.addWidget(laps_section)
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("time-trial-scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.pause_button = _control(tr("hud.control.pause"), "time-trial-pause", "")
        self.resume_button = _control(tr("hud.control.resume"), "time-trial-resume", "")
        self.stop_button = _control(tr("hud.control.abort"), "time-trial-stop", "danger")
        self.results_button = _control(tr("hud.control.results"), "time-trial-results", "primary")
        self.back_button = _control(tr("hud.control.back"), "time-trial-back", "ghost")
        controls = QHBoxLayout()
        controls.setSpacing(SPACE.sm)
        for button in (
            self.pause_button,
            self.resume_button,
            self.stop_button,
            self.results_button,
            self.back_button,
        ):
            controls.addWidget(button)

        header = QHBoxLayout()
        header.setSpacing(SPACE.md)
        header.addWidget(self.name_label, 1)
        header.addWidget(self.status_label)
        header.addWidget(self.elapsed_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE.md)
        layout.addLayout(header)
        layout.addWidget(self.warning)
        layout.addWidget(scroll, 1)
        layout.addLayout(controls)

    def show_board(self, board: TimeTrialBoard) -> None:
        missing = _MISSING
        fill_lane_table(
            self.records_table,
            [
                (
                    str(line.lane),
                    line.driver_label or missing,
                    line.vehicle_label or missing,
                    format_lap_seconds(line.time_ns),
                )
                for line in board.records
            ],
            time_column=3,
        )
        free = self.translator.translate("race.time_trial.free")
        active_rows: list[tuple[str, str, str]] = []
        muted: list[int] = []
        for index, line in enumerate(board.active):
            if line.occupied:
                active_rows.append(
                    (
                        str(line.lane),
                        line.driver_label or missing,
                        line.vehicle_label or missing,
                    )
                )
            else:
                active_rows.append((str(line.lane), free, missing))
                muted.append(index)
        fill_lane_table(self.active_table, active_rows, muted_rows=muted)
        self._show_attempts(board)

    def show_status(
        self, *, name: str, status: str, tone: str, elapsed_ns: int, warning: str
    ) -> None:
        self.name_label.setText(name)
        self.status_label.setText(f"● {status}")
        set_tone(self.status_label, tone)
        self.elapsed_label.setText(format_duration(elapsed_ns))
        self.warning.setText(warning)
        self.warning.setVisible(bool(warning))

    def set_controls(self, *, pause: bool, resume: bool, stop: bool, results: bool) -> None:
        self.pause_button.setEnabled(pause)
        self.resume_button.setEnabled(resume)
        self.stop_button.setEnabled(stop)
        self.results_button.setEnabled(results)
        self.back_button.setEnabled(True)

    def _show_attempts(self, board: TimeTrialBoard) -> None:
        _clear_layout(self.attempt_row)
        translate = self.translator.format
        for attempt in board.attempts:
            card = QFrame()
            card.setObjectName(f"time-trial-attempt-{attempt.lane}")
            card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            card.setMinimumWidth(0)
            set_role(card, "card")
            body = QVBoxLayout(card)
            body.setContentsMargins(SPACE.md, SPACE.md, SPACE.md, SPACE.md)
            body.setSpacing(SPACE.sm)

            badge = QLabel(str(attempt.lane))
            badge.setObjectName(f"time-trial-lane-badge-{attempt.lane}")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setMinimumWidth(56)
            _pixel_font(badge, _BADGE_PX, bold=True)
            set_role(badge, "metric")
            title = QLabel(
                translate(
                    "race.time_trial.attempt",
                    lane=attempt.lane,
                    driver=attempt.driver_label,
                    vehicle=attempt.vehicle_label,
                )
            )
            title.setObjectName(f"time-trial-attempt-title-{attempt.lane}")
            title.setWordWrap(True)
            set_role(title, "telemetry")
            heading = QHBoxLayout()
            heading.setSpacing(SPACE.sm)
            heading.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            heading.addWidget(title, 1)
            body.addLayout(heading)

            laps = _lane_table(
                [
                    self.translator.translate("race.column.lap"),
                    self.translator.translate("race.column.measured_time"),
                ],
                f"time-trial-attempt-laps-{attempt.lane}",
            )
            fill_lane_table(
                laps,
                [(str(line.offset), format_lap_seconds(line.time_ns)) for line in attempt.recent],
                time_column=1,
            )
            body.addWidget(laps)

            session = QLabel(
                translate(
                    "race.time_trial.session_best",
                    time=format_lap_seconds(attempt.session_best_ns),
                )
            )
            session.setObjectName(f"time-trial-session-best-{attempt.lane}")
            session.setWordWrap(True)
            set_role(session, "telemetry")
            set_tone(session, "ok")
            record = QLabel(
                translate(
                    "race.time_trial.lane_record",
                    time=format_lap_seconds(attempt.lane_record_ns),
                )
            )
            record.setObjectName(f"time-trial-lane-record-{attempt.lane}")
            record.setWordWrap(True)
            set_role(record, "caption")
            body.addWidget(session)
            body.addWidget(record)
            self.attempt_row.addWidget(card, 1)


def fill_lane_table(
    table: QTableWidget,
    rows: Sequence[Sequence[str]],
    *,
    lane_column: int = 0,
    time_column: int | None = None,
    muted_rows: Sequence[int] = (),
) -> None:
    """Fill a board table and keep the lane number the strongest cell."""
    fill_table(table, rows, keep_selection=False)
    _fit_table_height(table, len(rows))
    lane_font = QFont(table.font())
    lane_font.setPixelSize(_LANE_PX)
    lane_font.setBold(True)
    accent = QColor(COLORS.accent)
    muted = QColor(COLORS.text_muted)
    quiet = set(muted_rows)
    for row_index in range(table.rowCount()):
        for column in range(table.columnCount()):
            item = table.item(row_index, column)
            if item is None:
                continue
            if column == lane_column:
                item.setFont(lane_font)
                item.setForeground(accent)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            elif column == time_column:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if row_index in quiet and column != lane_column:
                item.setForeground(muted)


class _Section(QFrame):
    def __init__(self, title: str, object_name: str) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_role(self, "card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(SPACE.md, SPACE.md, SPACE.md, SPACE.md)
        self.body.setSpacing(SPACE.sm)
        label = QLabel(title)
        label.setObjectName(f"{object_name}-title")
        set_role(label, "section")
        self.body.addWidget(label)


def _section(title: str, object_name: str) -> _Section:
    return _Section(title, object_name)


def _lane_table(headers: list[str], object_name: str) -> QTableWidget:
    table = make_table(headers, object_name)
    table.setMinimumWidth(0)
    table.verticalHeader().setDefaultSectionSize(_ROW_PX)
    table.horizontalHeader().setMinimumSectionSize(48)
    _pixel_font(table, _BODY_PX, bold=False)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return table


def _fit_table_height(table: QTableWidget, rows: int) -> None:
    shown = min(max(rows, 1), 8)
    table.setFixedHeight(_HEADER_PX + _ROW_PX * shown + table.frameWidth() * 2 + 2)


def _pixel_font(widget: QWidget, pixels: int, *, bold: bool) -> None:
    font = QFont(widget.font())
    font.setPixelSize(pixels)
    font.setBold(bold)
    widget.setFont(font)


def _control(text: str, object_name: str, role: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    if role:
        set_role(button, role)
    return button


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
