"""The live HUD surface shared by the race view and the HUD editor.

Clock and status sit on top, lane cards beside the ranking, messages and controls
along the bottom. The start gantry is not part of this flow: the host paints it
on top and showing it does not move these widgets.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceMode, RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import HudLayout, factory_layout
from slot_racing.modules.races.runner import LiveRow, RaceSnapshot
from slot_racing.modules.races.ui.formatting import (
    format_progress_cell,
    participant_status_key,
    start_number_text,
)
from slot_racing.modules.races.ui.hud_widgets import (
    LiveRankingWidget,
    RaceClockWidget,
    RaceControlsWidget,
    RaceMessageWidget,
    RaceStatusWidget,
)
from slot_racing.modules.races.ui.lane_card import LaneCardBoard
from slot_racing.uikit import fill_table
from slot_racing.uikit.theme import set_tone

_SPACE_GAP = 8


class LiveHudStage(QScrollArea):
    """One scrollable HUD. Timing events update the labels; they do not rebuild it."""

    def __init__(self, translator: Translator, *, object_name: str = "live-scroll") -> None:
        super().__init__()
        self._translator = translator
        self._layout_spec = factory_layout()
        self.setObjectName(object_name)
        self.clock = RaceClockWidget(translator)
        self.status = RaceStatusWidget(translator)
        self.lanes = LaneCardBoard(translator)
        self.ranking = LiveRankingWidget(translator)
        self.messages = RaceMessageWidget(translator)
        self.controls = RaceControlsWidget(translator)
        self.status_label = self.status.status_label
        self.time_label = self.clock.time_label
        self.table = self.ranking.table

        for widget in (self.clock, self.status):
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            widget.setMinimumSize(80, 96)
        self.lanes.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.lanes.setMinimumHeight(180)
        self.ranking.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.ranking.setMinimumSize(200, 120)
        for panel in (self.messages, self.controls):
            panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
            panel.setMinimumHeight(120)

        self._top = QHBoxLayout()
        self._top.setSpacing(_SPACE_GAP)
        self._top.addWidget(self.clock, 1)
        self._top.addWidget(self.status, 1)
        self._middle = QHBoxLayout()
        self._middle.setSpacing(_SPACE_GAP)
        self._middle.addWidget(self.lanes, 3)
        self._middle.addWidget(self.ranking, 1)
        bottom = QHBoxLayout()
        bottom.setSpacing(_SPACE_GAP)
        bottom.addWidget(self.messages, 1)
        bottom.addWidget(self.controls, 1)
        self.canvas = QWidget()
        self.canvas.setObjectName("live-layout")
        self.canvas.setMinimumSize(0, 0)
        body = QVBoxLayout(self.canvas)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(_SPACE_GAP)
        body.addLayout(self._top, 1)
        body.addLayout(self._middle, 4)
        body.addLayout(bottom, 1)

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(self.canvas)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.apply_layout(self._layout_spec)

    def apply_layout(self, layout: HudLayout) -> None:
        """Shares, type and visibility. The child widgets are the ones already on screen."""
        self._layout_spec = layout
        self._top.setStretch(0, layout.clock_share)
        self._top.setStretch(1, layout.status_share)
        self._middle.setStretch(0, layout.lanes_share)
        self._middle.setStretch(1, layout.ranking_share)
        self.lanes.apply_style(layout)

    def layout_spec(self) -> HudLayout:
        return self._layout_spec

    def show_standings(
        self,
        snapshot: RaceSnapshot,
        *,
        mode: RaceMode,
        lane_count: int,
        duration_minutes: int | None = None,
    ) -> tuple[str, str]:
        """Paint one snapshot. Returns the status text and tone for a sibling board."""
        status_text, tone = status_presentation(self._translator, snapshot)
        self.status_label.setText(f"● {status_text}")
        set_tone(self.status_label, tone)
        self.status.emphasize(tone)
        self.time_label.setText(format_duration(snapshot.elapsed_ns))
        ended = snapshot.status is RaceStatus.FINISHED
        paused = snapshot.status is RaceStatus.PAUSED
        fill_table(
            self.table,
            [
                _cells(self._translator, snapshot, row, ended=ended, paused=paused)
                for row in snapshot.rows
            ],
            [row.lane for row in snapshot.rows],
        )
        self.ranking.present()
        self.lanes.show_snapshot(
            snapshot,
            lane_count=lane_count,
            mode=mode,
            duration_minutes=duration_minutes,
        )
        return status_text, tone


def status_presentation(translator: Translator, snapshot: RaceSnapshot) -> tuple[str, str]:
    status_text = translator.translate(f"race.status.{snapshot.status.value}")
    if snapshot.status is RaceStatus.FINISHED and snapshot.aborted:
        status_text = translator.translate("race.status.aborted")
    return status_text, status_tone(snapshot)


def status_tone(snapshot: RaceSnapshot) -> str:
    if snapshot.status is RaceStatus.FINISHED and snapshot.aborted:
        return "error"
    if snapshot.status is RaceStatus.RUNNING:
        return "ok"
    if snapshot.status is RaceStatus.PAUSED:
        return "warn"
    if snapshot.status is RaceStatus.FINISHED:
        return "info"
    return "muted"


def _cells(
    translator: Translator, snapshot: RaceSnapshot, row: LiveRow, *, ended: bool, paused: bool
) -> tuple[str, ...]:
    tr = translator.translate
    return (
        str(row.position),
        row.driver_label,
        row.vehicle_label,
        start_number_text(row.start_number),
        str(row.lane),
        str(row.laps_completed),
        format_progress_cell(row.current_lap, snapshot.laps),
        format_duration(row.last_lap_ns),
        format_duration(row.best_lap_ns),
        format_duration(row.total_time_ns),
        format_progress_cell(row.laps_completed, snapshot.laps),
        tr(participant_status_key(finished=row.finished, paused=paused, ended=ended)),
    )
