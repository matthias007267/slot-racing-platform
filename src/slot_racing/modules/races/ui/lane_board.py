"""One column per lane of the track that is being raced."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceMode
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.runner import RaceSnapshot
from slot_racing.modules.races.ui.formatting import (
    format_progress_cell,
    format_signed_seconds,
    lane_gaps,
)
from slot_racing.uikit.theme import SPACE

_FREE = "race.time_trial.free"


class LiveLaneBoard(QWidget):
    """Live overview. The column count is the track's lane count, including lanes that stay free."""

    def __init__(self, translator: Translator) -> None:
        super().__init__()
        self.translator = translator
        self.setObjectName("live-lane-board")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._columns: dict[int, _LaneColumn] = {}
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(SPACE.sm)

    def show_snapshot(self, snapshot: RaceSnapshot, *, lane_count: int, mode: RaceMode) -> None:
        self._ensure_columns(lane_count)
        by_lane = {row.lane: row for row in snapshot.rows}
        gaps = lane_gaps(snapshot.rows, by_best_lap=mode is RaceMode.TIME_TRIAL)
        free = self.translator.translate(_FREE)
        for lane in range(1, lane_count + 1):
            column = self._columns[lane]
            row = by_lane.get(lane)
            if row is None:
                column.show_free(free)
                continue
            gap = gaps.get(lane)
            leader = "" if gap is None else self._gap(gap.leader_ns, "race.live.gap.leader")
            behind = ""
            if gap is not None and gap.next_ns is not None:
                behind = self._gap(gap.next_ns, "race.live.gap.next")
            column.show_driver(
                name=row.driver_label,
                lap=format_progress_cell(row.current_lap, snapshot.laps),
                last=format_duration(row.last_lap_ns),
                best=format_duration(row.best_lap_ns),
                leader_gap=leader,
                next_gap=behind,
            )

    def _gap(self, delta_ns: int, key: str) -> str:
        return self.translator.format(key, gap=format_signed_seconds(delta_ns))

    def _ensure_columns(self, lane_count: int) -> None:
        if list(self._columns) == list(range(1, lane_count + 1)):
            return
        while self._row.count():
            item = self._row.takeAt(0)
            widget = None if item is None else item.widget()
            if widget is not None:
                widget.deleteLater()
        self._columns = {}
        for lane in range(1, lane_count + 1):
            column = _LaneColumn(self.translator, lane)
            self._columns[lane] = column
            self._row.addWidget(column, 1)


class _LaneColumn(QFrame):
    """Lane, driver, lap, last time, best time and gap, one line each."""

    def __init__(self, translator: Translator, lane: int) -> None:
        super().__init__()
        self.setObjectName(f"live-lane-{lane}")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.lane_label = QLabel(f"{translator.translate('race.live.lane')} {lane}")
        self.lane_label.setObjectName(f"live-lane-{lane}-number")
        self.driver_label = QLabel()
        self.driver_label.setObjectName(f"live-lane-{lane}-driver")
        self.lap_label = QLabel()
        self.lap_label.setObjectName(f"live-lane-{lane}-lap")
        self.last_label = QLabel()
        self.last_label.setObjectName(f"live-lane-{lane}-last")
        self.best_label = QLabel()
        self.best_label.setObjectName(f"live-lane-{lane}-best")
        self.gap_label = QLabel()
        self.gap_label.setObjectName(f"live-lane-{lane}-gap")
        font = QFont(self.font())
        font.setPixelSize(11)
        bold = QFont(font)
        bold.setBold(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.xs, 0, SPACE.xs, 0)
        layout.setSpacing(0)
        for label in (
            self.lane_label,
            self.driver_label,
            self.lap_label,
            self.last_label,
            self.best_label,
            self.gap_label,
        ):
            label.setFont(font if label is not self.lane_label else bold)
            label.setWordWrap(False)
            label.setMinimumSize(0, 0)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(label)

    def show_free(self, free: str) -> None:
        self.driver_label.setText(free)
        self.lap_label.setText("—")
        self.last_label.setText("—")
        self.best_label.setText("—")
        self.gap_label.setText("—")

    def show_driver(
        self,
        *,
        name: str,
        lap: str,
        last: str,
        best: str,
        leader_gap: str,
        next_gap: str,
    ) -> None:
        self.driver_label.setText(name)
        self.lap_label.setText(lap)
        self.last_label.setText(last)
        self.best_label.setText(best)
        self.gap_label.setText(leader_gap if not next_gap else f"{leader_gap}\n{next_gap}")
