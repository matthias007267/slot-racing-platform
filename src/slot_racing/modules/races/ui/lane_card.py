"""One stable block per lane. The block stays on its lane; only the numbers change."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QFont, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceMode, RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import (
    FIELD_BASE_PX,
    FIELD_BEST,
    FIELD_BEST_TIME,
    FIELD_DRIVER,
    FIELD_IDS,
    FIELD_LANE,
    FIELD_LAP,
    FIELD_LAST,
    FIELD_POSITION,
    FIELD_REMAINING_LAPS,
    FIELD_REMAINING_TIME,
    FIELD_START,
    FIELD_STATUS,
    FIELD_TOTAL,
    FIELD_VEHICLE,
    FONT_FLOOR,
    HudLayout,
    caption_px,
    effective_px,
    factory_layout,
    field_shown,
)
from slot_racing.modules.races.runner import LiveRow, RaceSnapshot
from slot_racing.modules.races.ui.formatting import (
    EMPTY_DISPLAY,
    best_lap_display,
    format_lap_progress,
    participant_status_key,
    start_number_text,
)
from slot_racing.uikit.theme import SPACE, set_role

_FREE = "race.time_trial.free"
_ALIGN = {
    "left": Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    "right": Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
    "center": Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
}


class LaneCard(QFrame):
    """Driver, place and lap times for one lane. The card is not recreated while the race runs."""

    def __init__(self, translator: Translator, lane: int) -> None:
        super().__init__()
        self._translator = translator
        self.lane = lane
        self._style = factory_layout()
        self._mode = RaceMode.LAPS
        self._remaining_laps: str | None = None
        self._remaining_time: str | None = None
        self._fit_factor = 1.0
        self._fitting = False
        self.setObjectName(f"live-lane-{lane}")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(0, 160)
        set_role(self, "hud-panel")

        self.lane_label = _value(f"live-lane-{lane}-number")
        self.position_label = _value(f"live-lane-{lane}-position")
        self.driver_label = _value(f"live-lane-{lane}-driver", wrap=True)
        self.vehicle_label = _value(f"live-lane-{lane}-vehicle", wrap=True)
        self.start_label = _value(f"live-lane-{lane}-start")
        self.lap_label = _value(f"live-lane-{lane}-lap", wrap=True)
        self.remaining_laps_label = _value(f"live-lane-{lane}-remaining-laps")
        self.remaining_time_label = _value(f"live-lane-{lane}-remaining-time")
        self.last_label = _value(f"live-lane-{lane}-last")
        self.best_label = _value(f"live-lane-{lane}-best")
        self.best_time_label = _value(f"live-lane-{lane}-best-time")
        self.total_label = _value(f"live-lane-{lane}-total")
        self.status_label = _value(f"live-lane-{lane}-status", wrap=True)
        self.lane_label.setText(f"{translator.translate('race.live.lane')} {lane}")

        self._lane_box = _block(self.lane_label)
        self._position_box = _block(self.position_label)
        self._driver_box = _block(self.driver_label)
        self._vehicle_box = _block(self.vehicle_label)
        tr = translator.translate
        self._start_caption = _caption(
            f"live-lane-{lane}-start-caption", tr("race.column.start_number")
        )
        self._start_box = _block(self._start_caption, self.start_label)
        self._lap_box = _block(self.lap_label)
        self._remaining_laps_caption = _caption(
            f"live-lane-{lane}-remaining-laps-caption", tr("race.column.remaining_laps")
        )
        self._remaining_time_caption = _caption(
            f"live-lane-{lane}-remaining-time-caption", tr("race.column.remaining_time")
        )
        self._remaining_laps_box = _block(self._remaining_laps_caption, self.remaining_laps_label)
        self._remaining_time_box = _block(self._remaining_time_caption, self.remaining_time_label)
        self._last_caption = _caption(f"live-lane-{lane}-last-caption", tr("race.column.last_lap"))
        self._best_caption = _caption(f"live-lane-{lane}-best-caption", tr("hud.field.best"))
        self._best_time_caption = _caption(
            f"live-lane-{lane}-best-time-caption", tr("hud.field.best_time")
        )
        self._total_caption = _caption(
            f"live-lane-{lane}-total-caption", tr("race.column.total_time")
        )
        self._last_box = _block(self._last_caption, self.last_label)
        self._best_box = _block(self._best_caption, self.best_label)
        self._best_time_box = _block(self._best_time_caption, self.best_time_label)
        self._total_box = _block(self._total_caption, self.total_label)
        self._status_box = _block(self.status_label)
        self._boxes = {
            FIELD_LANE: self._lane_box,
            FIELD_POSITION: self._position_box,
            FIELD_DRIVER: self._driver_box,
            FIELD_VEHICLE: self._vehicle_box,
            FIELD_START: self._start_box,
            FIELD_LAP: self._lap_box,
            FIELD_REMAINING_LAPS: self._remaining_laps_box,
            FIELD_REMAINING_TIME: self._remaining_time_box,
            FIELD_LAST: self._last_box,
            FIELD_BEST: self._best_box,
            FIELD_BEST_TIME: self._best_time_box,
            FIELD_TOTAL: self._total_box,
            FIELD_STATUS: self._status_box,
        }
        self._values = {
            FIELD_LANE: self.lane_label,
            FIELD_POSITION: self.position_label,
            FIELD_DRIVER: self.driver_label,
            FIELD_VEHICLE: self.vehicle_label,
            FIELD_START: self.start_label,
            FIELD_LAP: self.lap_label,
            FIELD_REMAINING_LAPS: self.remaining_laps_label,
            FIELD_REMAINING_TIME: self.remaining_time_label,
            FIELD_LAST: self.last_label,
            FIELD_BEST: self.best_label,
            FIELD_BEST_TIME: self.best_time_label,
            FIELD_TOTAL: self.total_label,
            FIELD_STATUS: self.status_label,
        }
        self._captions = {
            FIELD_START: self._start_caption,
            FIELD_REMAINING_LAPS: self._remaining_laps_caption,
            FIELD_REMAINING_TIME: self._remaining_time_caption,
            FIELD_LAST: self._last_caption,
            FIELD_BEST: self._best_caption,
            FIELD_BEST_TIME: self._best_time_caption,
            FIELD_TOTAL: self._total_caption,
        }

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        # Tight gap so "Beste Rundenzeit" fits in the lane row. A looser gap
        # made the canvas a few pixels too tall and the scrollbar moved the clock.
        column.setSpacing(1)
        for field_id in FIELD_IDS:
            column.addWidget(self._boxes[field_id])
        self._column = QWidget()
        self._column.setObjectName(f"live-lane-{lane}-stack")
        self._column.setMinimumWidth(0)
        self._column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._column.setLayout(column)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE.sm, SPACE.sm, SPACE.sm, SPACE.sm)
        outer.addStretch(1)
        outer.addWidget(self._column)
        outer.addStretch(1)
        self.apply_style(self._style)

    def apply_style(self, style: HudLayout) -> None:
        """Fonts, alignment and visibility. The widgets themselves stay in place."""
        self._style = style
        self._fit_factor = 1.0
        self._paint_style()
        self._fit()

    def show_driver(
        self,
        row: LiveRow,
        *,
        laps: int,
        mode: RaceMode,
        paused: bool,
        ended: bool,
        elapsed_ns: int = 0,
        duration_minutes: int | None = None,
    ) -> None:
        self._mode = mode
        self._remaining_laps = _remaining_laps(row.laps_completed, laps)
        self._remaining_time = _remaining_time(elapsed_ns, duration_minutes)
        self.position_label.setText(f"P{row.position}")
        self.driver_label.setText(row.driver_label)
        self.vehicle_label.setText(row.vehicle_label)
        self.start_label.setText(start_number_text(row.start_number))
        self.lap_label.setText(format_lap_progress(row.current_lap, laps))
        self.remaining_laps_label.setText(self._remaining_laps or EMPTY_DISPLAY)
        self.remaining_time_label.setText(self._remaining_time or EMPTY_DISPLAY)
        self.last_label.setText(format_duration(row.last_lap_ns))
        number, best_time = best_lap_display(row.lap_times_ns)
        self.best_label.setText(number)
        self.best_time_label.setText(best_time)
        self.total_label.setText(format_duration(row.total_time_ns))
        self.status_label.setText(
            self._translator.translate(
                participant_status_key(finished=row.finished, paused=paused, ended=ended)
            )
        )
        self._paint_style()

    def show_free(self, free: str, *, show_total: bool = True) -> None:
        self._mode = RaceMode.LAPS if show_total else RaceMode.TIME_TRIAL
        self._remaining_laps = None
        self._remaining_time = None
        self.position_label.setText(EMPTY_DISPLAY)
        self.driver_label.setText(free)
        self.vehicle_label.setText(EMPTY_DISPLAY)
        self.start_label.setText(EMPTY_DISPLAY)
        self.lap_label.setText(EMPTY_DISPLAY)
        self.remaining_laps_label.setText(EMPTY_DISPLAY)
        self.remaining_time_label.setText(EMPTY_DISPLAY)
        self.last_label.setText("-")
        self.best_label.setText("-")
        self.best_time_label.setText("-")
        self.total_label.setText("-")
        self.status_label.setText(EMPTY_DISPLAY)
        self._paint_style()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 160)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        available = event.size().width() - SPACE.sm * 2
        if available > 0:
            self._column.setFixedWidth(available)
        super().resizeEvent(event)
        self._fit()
        self._apply_wrap_height()

    def _paint_style(self) -> None:
        if self._fitting:
            return
        alignment = _ALIGN.get(self._style.alignment, _ALIGN["center"])
        for field_id, box in self._boxes.items():
            box.setVisible(
                field_shown(
                    self._style.field(field_id),
                    field_id,
                    self._mode,
                    available=self._field_available(field_id),
                )
            )
        for label in (*self._values.values(), *self._captions.values()):
            label.setAlignment(alignment)
        self._apply_fonts(self._fit_factor)

    def _field_available(self, field_id: str) -> bool:
        if field_id == FIELD_REMAINING_LAPS:
            return self._remaining_laps is not None
        if field_id == FIELD_REMAINING_TIME:
            return self._remaining_time is not None
        return True

    def _apply_fonts(self, factor: float) -> None:
        sizes: dict[str, int] = {}
        for field_id, label in self._values.items():
            base = FIELD_BASE_PX[field_id]
            style = self._style.field(field_id)
            chosen = effective_px(base, self._style.font_scale, style.scale)
            pixels = max(FONT_FLOOR, round(chosen * factor))
            sizes[field_id] = pixels
            _set_px(label, pixels, bold=True)
        for field_id, caption in self._captions.items():
            _set_px(caption, caption_px(sizes[field_id]), bold=False)

    def _fit(self) -> None:
        """Shrink every line together when the card is shorter than the chosen type."""
        if self._fitting or self.height() < 40:
            return
        self._fitting = True
        try:
            self._fit_factor = 1.0
            self._apply_fonts(1.0)
            needed = self._stack_height() + SPACE.sm * 2
            available = self.height()
            if needed > available > 0:
                self._fit_factor = max(0.45, available / needed)
                self._apply_fonts(self._fit_factor)
        finally:
            self._fitting = False

    def _apply_wrap_height(self) -> None:
        """Give wrapped lines the height their current width and font actually need."""
        if self._fitting:
            return
        self._fitting = True
        changed = False
        try:
            for label in (*self._values.values(), *self._captions.values()):
                if not label.wordWrap():
                    continue
                needed = label.heightForWidth(max(label.width(), 1))
                if needed > 0 and label.minimumHeight() != needed:
                    label.setMinimumHeight(needed)
                    changed = True
            layout = self.layout()
            if changed and layout is not None:
                layout.activate()
        finally:
            self._fitting = False

    def _stack_height(self) -> int:
        width = self._column.width()
        if width > 0 and self._column.hasHeightForWidth():
            return self._column.heightForWidth(width)
        return self._column.sizeHint().height()


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
        self._style = factory_layout()
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(SPACE.sm)

    @property
    def cards(self) -> dict[int, LaneCard]:
        return self._cards

    def apply_style(self, style: HudLayout) -> None:
        self._style = style
        for card in self._cards.values():
            card.apply_style(style)

    def show_snapshot(
        self,
        snapshot: RaceSnapshot,
        *,
        lane_count: int,
        mode: RaceMode,
        duration_minutes: int | None = None,
    ) -> None:
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
                card.show_driver(
                    row,
                    laps=snapshot.laps,
                    mode=mode,
                    paused=paused,
                    ended=ended,
                    elapsed_ns=snapshot.elapsed_ns,
                    duration_minutes=duration_minutes,
                )

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
                card.hide()
                card.setParent(None)
                card.deleteLater()
        for lane in range(1, lane_count + 1):
            if lane not in self._cards:
                card = LaneCard(self._translator, lane)
                card.apply_style(self._style)
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
        # QGridLayout keeps stretch on columns that no longer have a card.
        # A drop from four lanes to two would otherwise leave empty columns
        # and pin the remaining cards to the left.
        for column in range(self._grid.columnCount()):
            self._grid.setColumnStretch(column, 0)
            self._grid.setColumnMinimumWidth(column, 0)
        for row in range(self._grid.rowCount()):
            self._grid.setRowStretch(row, 0)
            self._grid.setRowMinimumHeight(row, 0)
        for index, lane in enumerate(range(1, self._lane_count + 1)):
            row, column = divmod(index, columns)
            self._grid.addWidget(self._cards[lane], row, column)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)
        self._columns = columns
        self._grid.activate()


def _remaining_laps(completed: int, target: int) -> str | None:
    if target < 1:
        return None
    return str(max(0, target - completed))


def _remaining_time(elapsed_ns: int, duration_minutes: int | None) -> str | None:
    if duration_minutes is None or duration_minutes < 1:
        return None
    left = duration_minutes * 60 * 1_000_000_000 - max(0, elapsed_ns)
    return format_duration(max(0, left))


def _columns_for(lanes: int, width: int) -> int:
    """Two lanes share one row. Further lanes wrap once a column would be narrower than 240 px."""
    if lanes <= 1:
        return 1
    if lanes == 2:
        return 2
    if width < 240:
        return 1
    return max(1, min(lanes, width // 240))


def _value(object_name: str, *, wrap: bool = False) -> QLabel:
    label = QLabel()
    label.setObjectName(object_name)
    label.setWordWrap(wrap)
    label.setMinimumSize(0, 0)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return label


def _caption(object_name: str, text: str) -> QLabel:
    label = _value(object_name, wrap=True)
    label.setText(text)
    return label


def _block(*labels: QLabel) -> QWidget:
    box = QWidget()
    box.setMinimumWidth(0)
    box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    for label in labels:
        layout.addWidget(label)
    return box


def _set_px(label: QLabel, pixels: int, *, bold: bool) -> None:
    current = label.font()
    if current.pixelSize() == pixels and current.bold() == bold:
        return
    font = QFont(current)
    font.setPixelSize(pixels)
    font.setBold(bold)
    label.setFont(font)
    weight = 700 if bold else 500
    label.setStyleSheet(f"font-size: {pixels}px; font-weight: {weight};")


def card_top(card: QWidget, label: QWidget) -> int:
    """Label top inside ``card``, so nested captions still compare in one coordinate system."""
    return label.mapTo(card, QPoint(0, 0)).y()
