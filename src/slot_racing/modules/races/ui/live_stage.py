"""The live HUD surface shared by the race view and the HUD editor.

Clock and status sit on top, lane cards beside the ranking, messages and controls
along the bottom, unless the layout stores a rectangle for every area. The start
gantry is not part of this flow: the host paints it on top.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceMode, RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import (
    BOTTOM_MIN_HEIGHT,
    CLOCK_MIN_HEIGHT,
    CLOCK_MIN_WIDTH,
    LANES_MIN_HEIGHT,
    RANKING_MIN_HEIGHT,
    RANKING_MIN_WIDTH,
    REGION_IDS,
    STAGE_GAP,
    HudLayout,
    HudWidgetConfig,
    factory_layout,
    region_frame,
    regions_ready,
    to_pixels,
)
from slot_racing.modules.races.runner import RaceSnapshot
from slot_racing.modules.races.ui.formatting import ranking_cells, status_presentation
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

_WIDGET_SIZE_MAX = (1 << 24) - 1


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
            widget.setMinimumSize(CLOCK_MIN_WIDTH, CLOCK_MIN_HEIGHT)
        self.lanes.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.lanes.setMinimumHeight(LANES_MIN_HEIGHT)
        self.ranking.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.ranking.setMinimumSize(RANKING_MIN_WIDTH, RANKING_MIN_HEIGHT)
        for panel in (self.messages, self.controls):
            panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
            panel.setMinimumHeight(BOTTOM_MIN_HEIGHT)

        self._top = QHBoxLayout()
        self._top.setSpacing(STAGE_GAP)
        self._top.addWidget(self.clock, 1)
        self._top.addWidget(self.status, 1)
        self._middle = QHBoxLayout()
        self._middle.setSpacing(STAGE_GAP)
        self._middle.addWidget(self.lanes, 3)
        self._middle.addWidget(self.ranking, 1)
        self._bottom = QHBoxLayout()
        self._bottom.setSpacing(STAGE_GAP)
        self._bottom.addWidget(self.messages, 1)
        self._bottom.addWidget(self.controls, 1)
        self.canvas = QWidget()
        self.canvas.setObjectName("live-layout")
        self.canvas.setMinimumSize(0, 0)
        self._body = QVBoxLayout(self.canvas)
        body = self._body
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(STAGE_GAP)
        body.addLayout(self._top, 1)
        body.addLayout(self._middle, 4)
        body.addLayout(self._bottom, 1)
        self._flow = True
        self._placing = False
        self._minimums = {
            "clock": (CLOCK_MIN_WIDTH, CLOCK_MIN_HEIGHT),
            "status": (CLOCK_MIN_WIDTH, CLOCK_MIN_HEIGHT),
            "lanes": (0, LANES_MIN_HEIGHT),
            "ranking": (RANKING_MIN_WIDTH, RANKING_MIN_HEIGHT),
            "messages": (0, BOTTOM_MIN_HEIGHT),
            "controls": (0, BOTTOM_MIN_HEIGHT),
        }

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(self.canvas)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.apply_layout(self._layout_spec)

    def apply_layout(self, layout: HudLayout) -> None:
        """Shares, or stored rectangles. The child widgets stay the ones already on screen."""
        self._layout_spec = layout
        if regions_ready(layout):
            self._place_free(layout)
        else:
            self._place_flow(layout)
        self.lanes.apply_style(layout)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._placing or not regions_ready(self._layout_spec):
            return
        self._place_free(self._layout_spec)

    def panels(self) -> dict[str, QWidget]:
        """The six live areas, in the order a placement is stored."""
        return {
            "clock": self.clock,
            "status": self.status,
            "lanes": self.lanes,
            "ranking": self.ranking,
            "messages": self.messages,
            "controls": self.controls,
        }

    def _place_flow(self, layout: HudLayout) -> None:
        self._restore_flow()
        self._top.setStretch(0, layout.clock_share)
        self._top.setStretch(1, layout.status_share)
        self._middle.setStretch(0, layout.lanes_share)
        self._middle.setStretch(1, layout.ranking_share)

    def _place_free(self, layout: HudLayout) -> None:
        """Put every area on its stored fraction of the canvas."""
        self._release_flow()
        width = self.canvas.width()
        height = self.canvas.height()
        if width <= 1 or height <= 1:
            return
        self._placing = True
        try:
            for region_id in REGION_IDS:
                frame = region_frame(layout, region_id)
                widget = self.panels()[region_id]
                if frame is None:
                    continue
                rect = to_pixels(
                    HudWidgetConfig(frame.id, True, frame.x, frame.y, frame.width, frame.height),
                    width,
                    height,
                )
                widget.setMinimumSize(rect.width, rect.height)
                widget.setMaximumSize(rect.width, rect.height)
                widget.setGeometry(rect.x, rect.y, rect.width, rect.height)
                widget.show()
        finally:
            self._placing = False

    def _release_flow(self) -> None:
        if not self._flow:
            return
        for widget in self.panels().values():
            self._top.removeWidget(widget)
            self._middle.removeWidget(widget)
            self._bottom.removeWidget(widget)
            widget.setParent(self.canvas)
        self._flow = False

    def _restore_flow(self) -> None:
        if self._flow:
            return
        for region_id, widget in self.panels().items():
            widget.setParent(None)
            width, height = self._minimums[region_id]
            widget.setMinimumSize(width, height)
            widget.setMaximumSize(_WIDGET_SIZE_MAX, _WIDGET_SIZE_MAX)
        body = self._body
        while body.count():
            removed = body.takeAt(0)
            if removed is not None:
                del removed
        self._top = QHBoxLayout()
        self._middle = QHBoxLayout()
        self._bottom = QHBoxLayout()
        for row in (self._top, self._middle, self._bottom):
            row.setSpacing(STAGE_GAP)
        self._top.addWidget(self.clock, 1)
        self._top.addWidget(self.status, 1)
        self._middle.addWidget(self.lanes, 3)
        self._middle.addWidget(self.ranking, 1)
        self._bottom.addWidget(self.messages, 1)
        self._bottom.addWidget(self.controls, 1)
        body.addLayout(self._top, 1)
        body.addLayout(self._middle, 4)
        body.addLayout(self._bottom, 1)
        body.activate()
        # Qt applies the new row geometry on the next layout pass.
        QApplication.sendPostedEvents()
        self._flow = True

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
        ranking = snapshot.standings or snapshot.rows
        fill_table(
            self.table,
            [
                ranking_cells(self._translator, snapshot, row, ended=ended, paused=paused)
                for row in ranking
            ],
            [row.participant_id or row.lane for row in ranking],
        )
        self.ranking.present()
        self.lanes.show_snapshot(
            snapshot,
            lane_count=lane_count,
            mode=mode,
            duration_minutes=duration_minutes,
        )
        return status_text, tone
