"""One stable block per lane. The block stays on its lane; only the numbers change."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceMode, RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.runner import LiveRow, RaceSnapshot
from slot_racing.modules.races.ui.formatting import (
    EMPTY_DISPLAY,
    format_lap_progress,
    participant_status_key,
    start_number_text,
)
from slot_racing.uikit.theme import SPACE, set_role

_FREE = "race.time_trial.free"


class LaneCard(QFrame):
    """Driver, place and lap times for one lane. The card is not recreated while the race runs."""

    def __init__(self, translator: Translator, lane: int) -> None:
        super().__init__()
        self._translator = translator
        self.lane = lane
        self.setObjectName(f"live-lane-{lane}")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(0, 180)
        set_role(self, "hud-panel")

        self.lane_label = _label(f"live-lane-{lane}-number", 13, bold=True)
        self.position_label = _label(f"live-lane-{lane}-position", 28, bold=True)
        self.driver_label = _label(f"live-lane-{lane}-driver", 22, bold=True, wrap=True)
        self.vehicle_label = _label(f"live-lane-{lane}-vehicle", 13, wrap=True)
        self.start_label = _label(f"live-lane-{lane}-start", 13)
        self.lap_label = _label(f"live-lane-{lane}-lap", 20, bold=True, wrap=True)
        self.last_label = _label(f"live-lane-{lane}-last", 14, bold=True)
        self.best_label = _label(f"live-lane-{lane}-best", 14, bold=True)
        self.total_label = _label(f"live-lane-{lane}-total", 14, bold=True)
        self.status_label = _label(f"live-lane-{lane}-status", 13, wrap=True)
        set_role(self.driver_label, "page-title")
        set_role(self.position_label, "metric")
        set_role(self.lap_label, "telemetry")

        self._last_caption = _caption(translator.translate("race.column.last_lap"))
        self._best_caption = _caption(translator.translate("race.column.best_lap"))
        self._total_caption = _caption(translator.translate("race.column.total_time"))
        self.lane_label.setText(f"{translator.translate('race.live.lane')} {lane}")

        header = QHBoxLayout()
        header.addWidget(self.lane_label)
        header.addStretch(1)
        header.addWidget(self.position_label)
        identity = QHBoxLayout()
        identity.addWidget(self.vehicle_label, 1)
        identity.addWidget(self.start_label)
        times = QHBoxLayout()
        times.setSpacing(SPACE.sm)
        times.addWidget(_stack(self._last_caption, self.last_label), 1)
        times.addWidget(_stack(self._best_caption, self.best_label), 1)
        self._total_box = _stack(self._total_caption, self.total_label)
        times.addWidget(self._total_box, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.sm, SPACE.sm, SPACE.sm, SPACE.sm)
        layout.setSpacing(SPACE.xs)
        layout.addLayout(header)
        layout.addWidget(self.driver_label)
        layout.addLayout(identity)
        layout.addWidget(self.lap_label)
        layout.addLayout(times)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

    def show_driver(
        self,
        row: LiveRow,
        *,
        laps: int,
        mode: RaceMode,
        paused: bool,
        ended: bool,
    ) -> None:
        tr = self._translator.translate
        show_total = mode is RaceMode.LAPS
        self._total_box.setVisible(show_total)
        self.position_label.setText(f"P{row.position}")
        self.driver_label.setText(row.driver_label)
        self.vehicle_label.setText(row.vehicle_label)
        self.start_label.setText(start_number_text(row.start_number))
        self.lap_label.setText(format_lap_progress(row.current_lap, laps))
        self.last_label.setText(format_duration(row.last_lap_ns))
        self.best_label.setText(format_duration(row.best_lap_ns))
        self.total_label.setText(format_duration(row.total_time_ns))
        self.status_label.setText(
            tr(participant_status_key(finished=row.finished, paused=paused, ended=ended))
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 180)

    def show_free(self, free: str, *, show_total: bool = True) -> None:
        self._total_box.setVisible(show_total)
        self.position_label.setText(EMPTY_DISPLAY)
        self.driver_label.setText(free)
        self.vehicle_label.setText(EMPTY_DISPLAY)
        self.start_label.setText(EMPTY_DISPLAY)
        self.lap_label.setText(EMPTY_DISPLAY)
        self.last_label.setText("-")
        self.best_label.setText("-")
        self.total_label.setText("-")
        self.status_label.setText(EMPTY_DISPLAY)


class LaneCardBoard(QWidget):
    """Lane cards in a grid. Two lanes stay side by side. More lanes wrap onto further rows."""

    def __init__(self, translator: Translator) -> None:
        super().__init__()
        self._translator = translator
        self.setObjectName("live-lane-cards")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._cards: dict[int, LaneCard] = {}
        self._lane_count = 0
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(SPACE.sm)

    @property
    def cards(self) -> dict[int, LaneCard]:
        return self._cards

    def show_snapshot(self, snapshot: RaceSnapshot, *, lane_count: int, mode: RaceMode) -> None:
        self._ensure(lane_count)
        by_lane = {row.lane: row for row in snapshot.rows}
        paused = snapshot.status is RaceStatus.PAUSED
        ended = snapshot.status is RaceStatus.FINISHED
        free = self._translator.translate(_FREE)
        for lane, card in self._cards.items():
            row = by_lane.get(lane)
            if row is None:
                card.show_free(free, show_total=mode is RaceMode.LAPS)
            else:
                card.show_driver(row, laps=snapshot.laps, mode=mode, paused=paused, ended=ended)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow()

    def _ensure(self, lane_count: int) -> None:
        if lane_count == self._lane_count and set(self._cards) == set(range(1, lane_count + 1)):
            return
        for lane in list(self._cards):
            if lane > lane_count:
                card = self._cards.pop(lane)
                self._grid.removeWidget(card)
                card.deleteLater()
        for lane in range(1, lane_count + 1):
            if lane not in self._cards:
                card = LaneCard(self._translator, lane)
                self._cards[lane] = card
        self._lane_count = lane_count
        self._columns = 0
        self._reflow()

    def _reflow(self) -> None:
        if self._lane_count < 1:
            return
        columns = _columns_for(self._lane_count, self.width())
        if columns == self._columns:
            return
        while self._grid.count():
            self._grid.takeAt(0)
        for index, lane in enumerate(range(1, self._lane_count + 1)):
            row, column = divmod(index, columns)
            self._grid.addWidget(self._cards[lane], row, column)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)
        self._columns = columns


def _columns_for(lanes: int, width: int) -> int:
    """Two lanes share one row. Further lanes wrap once a column would be narrower than 240 px."""
    if lanes <= 1:
        return 1
    if lanes == 2:
        return 2
    if width < 240:
        return 1
    return max(1, min(lanes, width // 240))


class _ShrinkLabel(QLabel):
    """A line that may become narrower than its text. Wrapped lines grow in height instead."""

    def __init__(self, object_name: str, *, wrap: bool) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setWordWrap(wrap)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self.fontMetrics().lineSpacing())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self.fontMetrics().lineSpacing())

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return self.wordWrap()

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        if not self.wordWrap() or width < 1:
            return self.fontMetrics().lineSpacing()
        bounds = self.fontMetrics().boundingRect(
            0, 0, width, 10_000, int(Qt.TextFlag.TextWordWrap), self.text()
        )
        return max(bounds.height(), self.fontMetrics().lineSpacing())


def _label(object_name: str, pixels: int, *, bold: bool = False, wrap: bool = False) -> QLabel:
    label = _ShrinkLabel(object_name, wrap=wrap)
    font = QFont(label.font())
    font.setPixelSize(pixels)
    font.setBold(bold)
    label.setFont(font)
    return label


def _caption(text: str) -> QLabel:
    label = _ShrinkLabel("live-lane-caption", wrap=True)
    label.setText(text)
    set_role(label, "caption")
    return label


def _stack(caption: QLabel, value: QLabel) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(caption)
    layout.addWidget(value)
    return box
