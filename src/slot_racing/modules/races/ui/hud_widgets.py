"""Independent HUD panels. They display values the live view already has; they compute no race."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QVBoxLayout,
)

from slot_racing.core.i18n import Translator
from slot_racing.modules.races.ui.formatting import EMPTY_DISPLAY, format_lap_progress
from slot_racing.uikit.theme import COLORS, SPACE, set_role, set_tone
from slot_racing.uikit.widgets import StatusLabel, make_table

_MIN_FONT = 12
_MAX_FONT = 64
LEADER_ROLE = Qt.ItemDataRole.UserRole + 1

COLUMN_POSITION = 0
COLUMN_DRIVER = 1
COLUMN_VEHICLE = 2
COLUMN_LAPS = 5
COLUMN_BEST = 8
_OPTIONAL_COLUMNS = frozenset({3, 4, 6, 7, 9, 10, 11})
_VEHICLE_MIN_WIDTH = 460
_FULL_TABLE_WIDTH = 980


class HudWidget(QFrame):
    """One movable panel. The stage positions it; the panel only lays out its own content."""

    def __init__(self, widget_id: str) -> None:
        super().__init__()
        self.widget_id = widget_id
        self.setObjectName(f"hud-panel-{widget_id}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(0, 0)
        set_role(self, "hud-panel")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(SPACE.xs, SPACE.xs, SPACE.xs, SPACE.xs)
        self.body.setSpacing(SPACE.xs)

    def add_caption(self, text: str) -> QLabel:
        label = QLabel(text)
        set_role(label, "card-title")
        label.setWordWrap(False)
        label.setMinimumSize(0, 0)
        self.body.addWidget(label)
        return label


class FitLabel(QLabel):
    """A figure whose type size follows the space it is actually given."""

    def __init__(self, object_name: str, text: str = EMPTY_DISPLAY) -> None:
        super().__init__(text)
        self.setObjectName(object_name)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(0, 0)
        self.setWordWrap(False)
        self._fitting = False
        set_role(self, "telemetry-fit")

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self._fit()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if self._fitting:
            return
        width = self.width()
        height = self.height()
        if width < 8 or height < 8:
            return
        lines = self.text().split("\n") or [EMPTY_DISPLAY]
        font = self.font()
        chosen = _MIN_FONT
        placeholder = all(line.strip() in {EMPTY_DISPLAY, "-", ""} for line in lines)
        maximum = 22 if placeholder else _MAX_FONT
        low, high = _MIN_FONT, maximum
        while low <= high:
            mid = (low + high) // 2
            font.setPixelSize(mid)
            metrics = QFontMetrics(font)
            widest = max((metrics.horizontalAdvance(line) for line in lines), default=0)
            block = metrics.height() * len(lines)
            if widest <= width - 4 and block <= height - 2:
                chosen = mid
                low = mid + 1
            else:
                high = mid - 1
        if self.font().pixelSize() == chosen:
            return
        font.setPixelSize(chosen)
        self._fitting = True
        try:
            self.setFont(font)
        finally:
            self._fitting = False


class RaceHeaderWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("race_header")
        self.name_label = ElidingLabel(
            "live-name", translator.translate("race.live.title"), color=COLORS.text, bold=True
        )
        self.track_label = _meta("live-track")
        self.provider_label = _meta("live-provider")
        self.header_status = _meta("hud-header-status")
        self.participants_label = _meta("hud-participants")
        self._meta_labels = (
            self.header_status,
            self.track_label,
            self.participants_label,
            self.provider_label,
        )
        self._meta = QGridLayout()
        self._meta.setSpacing(SPACE.xs)
        self._title = QHBoxLayout()
        self._title.setSpacing(SPACE.xs)
        self._title.addWidget(self.name_label, 1)
        self._meta_columns = 0
        self._provider_on_title = False
        self._meta_signature: tuple[int, bool] | None = None
        self.body.addLayout(self._title)
        self.body.addLayout(self._meta)
        self._arrange_meta(4)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Two rows need enough height. A short panel stays on one line so the text is not crushed.
        columns = 2 if self.width() < 560 and self.height() >= 72 else 4
        self._place_provider(columns)
        self._arrange_meta(columns)

    def _place_provider(self, columns: int) -> None:
        """Keep every fact whole. A short one-line row puts the timing source beside the name."""
        metrics = QFontMetrics(self.header_status.font())
        gap = 4

        def needed(label: QLabel) -> int:
            return metrics.horizontalAdvance(label.text()) + gap

        margins = self.body.contentsMargins()
        inner = max(self.width() - margins.left() - margins.right(), 0)
        facts = (self.header_status, self.track_label, self.participants_label)
        spacing = self._meta.spacing()
        facts_need = sum(needed(label) for label in facts) + spacing * max(len(facts) - 1, 0)
        provider_need = needed(self.provider_label)
        meta_need = facts_need + provider_need + spacing
        on_title = columns == 4 and meta_need > inner
        if on_title != self._provider_on_title:
            self._provider_on_title = on_title
            self.provider_label.setParent(None)
            if on_title:
                self._title.addWidget(self.provider_label, 0)
        self.provider_label.setMinimumWidth(provider_need if on_title else 0)
        facts_fit = (not on_title and meta_need <= inner) or (on_title and facts_need <= inner)
        for label in facts:
            label.setMinimumWidth(needed(label) if facts_fit else 0)

    def _arrange_meta(self, columns: int) -> None:
        """Two rows when the panel is narrow and tall enough for them."""
        signature = (columns, self._provider_on_title)
        if signature == self._meta_signature:
            return
        self._meta_signature = signature
        self._meta_columns = columns
        labels = [
            label
            for label in self._meta_labels
            if label is not self.provider_label or not self._provider_on_title
        ]
        for label in self._meta_labels:
            self._meta.removeWidget(label)
        for index, label in enumerate(labels):
            if columns == 2:
                row, column = divmod(index, 2)
            else:
                row, column = 0, index
            self._meta.addWidget(label, row, column)


class RaceClockWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("race_clock")
        self.add_caption(translator.translate("race.live.time"))
        self.time_label = FitLabel("live-time", "0:00.000")
        self.body.addWidget(self.time_label, 1)


class LapProgressWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("lap_progress")
        self.add_caption(translator.translate("hud.lap.caption"))
        self.laps_label = FitLabel("live-laps", format_lap_progress(0, 0))
        self.progress = QProgressBar()
        self.progress.setObjectName("live-progress")
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(8)
        self.progress.setMinimumHeight(0)
        self.body.addWidget(self.laps_label, 1)
        self.body.addWidget(self.progress)

    def show_counts(self, current: int, target: int, completed: int) -> None:
        """Show the leader's lap and how much of the race is done. No ranking of its own."""
        self.laps_label.setText(format_lap_progress(current, target))
        if target < 1:
            self.progress.setVisible(False)
            return
        self.progress.setVisible(True)
        self.progress.setRange(0, target)
        self.progress.setValue(min(max(completed, 0), target))


class LiveRankingWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("live_ranking")
        tr = translator.translate
        self.add_caption(tr("hud.widget.live_ranking"))
        self.table = make_table(
            [
                tr("race.column.position"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
                tr("race.column.start_number"),
                tr("race.column.lane"),
                tr("race.column.laps_done"),
                tr("race.column.current_lap"),
                tr("race.column.last_lap"),
                tr("race.column.best_lap"),
                tr("race.column.total_time"),
                tr("race.column.progress"),
                tr("race.column.status"),
            ],
            "live-table",
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COLUMN_DRIVER, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_VEHICLE, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(28)
        header.setTextElideMode(Qt.TextElideMode.ElideRight)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStyleSheet("QHeaderView::section { padding: 4px 6px; }")
        self.table.setMinimumSize(0, 0)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollMode(self.table.ScrollMode.ScrollPerPixel)
        self.table.setAlternatingRowColors(False)
        self.table.setItemDelegate(RankingDelegate(self.table))
        self.table.verticalHeader().setDefaultSectionSize(32)
        self._fitting = False
        self.body.addWidget(self.table, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.body.activate()
        self.present()

    def present(self) -> None:
        """Fit columns and row height to this panel. The row order is left untouched."""
        if self._fitting:
            return
        self._fitting = True
        try:
            self._apply_columns()
            mark_leader(self.table)
            self._fit_rows()
        finally:
            self._fitting = False

    def _apply_columns(self) -> None:
        width = self.table.viewport().width() or self.width()
        if width < 40:
            return
        show_vehicle = width >= _VEHICLE_MIN_WIDTH
        show_rest = width >= _FULL_TABLE_WIDTH
        header = self.table.horizontalHeader()
        for column in range(self.table.columnCount()):
            if column == COLUMN_VEHICLE:
                hidden = not show_vehicle
            elif column in _OPTIONAL_COLUMNS:
                hidden = not show_rest
            else:
                hidden = False
            self.table.setColumnHidden(column, hidden)
        self._assign_widths(width, header, allow_drop=True)

    def _assign_widths(self, viewport: int, header: QHeaderView, *, allow_drop: bool) -> None:
        """Give driver names the room, then lap columns. Headers ellipsize before names do."""
        visible = [
            column
            for column in range(self.table.columnCount())
            if not self.table.isColumnHidden(column)
        ]
        metrics = self.table.fontMetrics()
        header_width: dict[int, int] = {}
        value_width: dict[int, int] = {}
        for column in visible:
            header_item = self.table.horizontalHeaderItem(column)
            label = "" if header_item is None else header_item.text()
            header_width[column] = max(28, metrics.horizontalAdvance(label) + 16)
            widest = 28
            for row in range(self.table.rowCount()):
                item = self.table.item(row, column)
                if item is not None and item.text():
                    widest = max(widest, metrics.horizontalAdvance(item.text()) + 16)
            value_width[column] = widest
        flex = {COLUMN_DRIVER, COLUMN_VEHICLE}
        fixed = [column for column in visible if column not in flex]
        flex_need = value_width.get(COLUMN_DRIVER, 28)
        if COLUMN_VEHICLE in visible:
            flex_need += value_width.get(COLUMN_VEHICLE, 28)
        limit = max(viewport - 2, 40)
        if sum(header_width[column] for column in fixed) + flex_need <= limit:
            chosen = {column: header_width[column] for column in fixed}
        else:
            chosen = {column: min(header_width[column], value_width[column]) for column in fixed}
            spare = limit - flex_need - sum(chosen.values())
            if spare > 0:
                for column in fixed:
                    room = max(0, header_width[column] - chosen[column])
                    take = min(room, spare)
                    chosen[column] += take
                    spare -= take
            elif spare < 0:
                overflow = -spare
                for column in reversed(fixed):
                    cut = min(max(0, chosen[column] - 28), overflow)
                    chosen[column] -= cut
                    overflow -= cut
                    if overflow <= 0:
                        break
        # A best-lap column that cannot show its header is dropped. The lap count and the
        # driver names stay. Wider tables keep the column.
        if allow_drop and chosen.get(COLUMN_BEST, 64) < 64:
            self.table.setColumnHidden(COLUMN_BEST, True)
            self._assign_widths(viewport, header, allow_drop=False)
            return
        for column in visible:
            if column in chosen:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
                self.table.setColumnWidth(column, chosen[column])
            else:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)

    def _fit_rows(self) -> None:
        count = self.table.rowCount()
        available = self.table.viewport().height()
        if count <= 0 or available <= 0:
            return
        per = available // min(count, 8)
        height = max(28, min(48, per))
        self.table.verticalHeader().setDefaultSectionSize(height)
        for row in range(count):
            self.table.setRowHeight(row, height)


class RankingDelegate(QStyledItemDelegate):
    """Paints the leader quietly and keeps the driver readable. It does not sort."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        painter.save()
        rect = option.rect
        leader = bool(index.data(LEADER_ROLE))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(rect, QColor(COLORS.row_hover))
        elif leader or index.row() % 2 == 0:
            painter.fillRect(rect, QColor(COLORS.elevated))
        else:
            painter.fillRect(rect, QColor(COLORS.surface))
        if leader and index.column() == COLUMN_POSITION:
            bar = rect.adjusted(0, 3, 0, -3)
            bar.setWidth(3)
            painter.fillRect(bar, QColor(COLORS.accent))
        painter.setPen(QColor(COLORS.border))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        text = index.data(Qt.ItemDataRole.DisplayRole)
        font = QFont(option.font)
        column = index.column()
        if column == COLUMN_POSITION or (leader and column == COLUMN_DRIVER):
            font.setBold(True)
        if column == COLUMN_POSITION:
            font.setPixelSize(max(13, min(22, rect.height() - 10)))
        color = COLORS.text_secondary if column == COLUMN_VEHICLE else COLORS.text
        painter.setFont(font)
        painter.setPen(QColor(color))
        shown = "" if text is None else str(text)
        inner = rect.adjusted(8, 0, -6, 0)
        shown = QFontMetrics(font).elidedText(
            shown, Qt.TextElideMode.ElideRight, max(inner.width(), 0)
        )
        painter.drawText(inner, option.displayAlignment, shown)
        painter.restore()


class DriverHighlightWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("driver_highlight")
        tr = translator.translate
        self._lap_caption = tr("hud.lap.caption")
        self._last_caption = tr("race.column.last_lap")
        self._best_caption = tr("race.column.best_lap")
        self._last_value = EMPTY_DISPLAY
        self._best_value = EMPTY_DISPLAY
        self.detail_title = QLabel(tr("race.live.detail"))
        self.detail_title.setObjectName("live-detail-title")
        set_role(self.detail_title, "card-title")
        self.detail_title.setVisible(False)
        self.position_label = FitLabel("hud-highlight-position")
        self.name_label = FitLabel("hud-highlight-name")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.vehicle_label = _meta("hud-highlight-vehicle")
        self.vehicle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lap_label = _meta("hud-highlight-lap")
        self.lap_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.last_label = _meta("hud-highlight-last")
        self.best_label = _meta("hud-highlight-best")
        self.detail = QLabel(tr("race.live.detail_empty"))
        self.detail.setObjectName("live-detail")
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.detail.setMinimumSize(0, 0)
        set_role(self.detail, "caption")
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self.detail)
        self._scroll.setMinimumHeight(0)
        self.body.addWidget(self.detail_title)
        self.body.addWidget(self.position_label)
        self.body.addWidget(self.name_label, 1)
        self.body.addWidget(self.vehicle_label)
        self.body.addWidget(self.lap_label)
        times = QHBoxLayout()
        times.setSpacing(SPACE.sm)
        times.addWidget(self.last_label, 1)
        times.addWidget(self.best_label, 1)
        self.body.addLayout(times)
        self.body.addWidget(self._scroll, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        # A short panel keeps the name readable. The detail text stays available and scrolls
        # back into view once the panel is tall enough.
        height = self.height()
        self.lap_label.setVisible(height >= 120)
        self.last_label.setVisible(height >= 150)
        self.best_label.setVisible(height >= 150)
        self._scroll.setVisible(height >= 180)
        self._show_times()

    def show_driver(
        self,
        *,
        position: str,
        name: str,
        vehicle: str,
        lap: str,
        last: str,
        best: str,
    ) -> None:
        self.position_label.setText(position)
        self.name_label.setText(name.upper() if name and name != EMPTY_DISPLAY else EMPTY_DISPLAY)
        self.vehicle_label.setText(vehicle.upper() if vehicle else "")
        self.lap_label.setText(f"{self._lap_caption} {lap}".strip())
        self._last_value = last
        self._best_value = best
        self._show_times()

    def _show_times(self) -> None:
        narrow = self.width() < 220
        last_caption = "Letzte" if narrow else self._last_caption
        best_caption = "Beste" if narrow else self._best_caption
        self.last_label.setText(f"{last_caption}\n{self._last_value}")
        self.best_label.setText(f"{best_caption}\n{self._best_value}")

    def clear_driver(self) -> None:
        self.show_driver(
            position=EMPTY_DISPLAY,
            name=EMPTY_DISPLAY,
            vehicle="",
            lap="",
            last=EMPTY_DISPLAY,
            best=EMPTY_DISPLAY,
        )


class LastLapWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("last_lap")
        self.add_caption(translator.translate("race.column.last_lap"))
        self.value_label = FitLabel("hud-last-lap")
        self.body.addWidget(self.value_label, 1)


class BestLapWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("best_lap")
        self.add_caption(translator.translate("race.column.best_lap"))
        self.value_label = FitLabel("hud-best-lap")
        self.body.addWidget(self.value_label, 1)


class RaceStatusWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("race_status")
        self._caption = self.add_caption(translator.translate("hud.widget.race_status"))
        self.status_label = FitLabel("live-status")
        self.body.addWidget(self.status_label, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        # The status word stays readable when the panel cannot hold the caption as well.
        self._caption.setVisible(self.height() >= 56)

    def emphasize(self, tone: str) -> None:
        """Running and paused stand out by a thin border. The fill stays dark."""
        set_tone(self, tone if tone in {"ok", "warn"} else "")


class RaceMessageWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("race_message")
        self._caption = self.add_caption(translator.translate("hud.widget.race_message"))
        self.message_label = QLabel(EMPTY_DISPLAY)
        self.message_label.setObjectName("hud-message")
        self.message_label.setWordWrap(True)
        self.message_label.setMinimumSize(0, 0)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.warning = StatusLabel("live-warning")
        self.warning.setVisible(False)
        self.body.addWidget(self.message_label, 1)
        self.body.addWidget(self.warning)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._caption.setVisible(self.height() >= 64)

    def show_warning(self, text: str) -> None:
        self.warning.show_error(text)
        self.warning.setVisible(bool(text))

    def clear_warning(self) -> None:
        self.warning.clear_message()
        self.warning.setVisible(False)


class RaceControlsWidget(HudWidget):
    def __init__(self, translator: Translator) -> None:
        super().__init__("race_controls")
        tr = translator.translate
        self.pause_button = _button(tr("hud.control.pause"), "live-pause", "")
        self.resume_button = _button(tr("hud.control.resume"), "live-resume", "")
        self.stop_button = _button(tr("hud.control.abort"), "live-stop", "danger")
        self.results_button = _button(tr("hud.control.results"), "live-results", "primary")
        self.back_button = _button(tr("hud.control.back"), "live-back", "ghost")
        self.body.addLayout(_button_row(self.pause_button, self.resume_button))
        self.body.addLayout(_button_row(self.stop_button, self.results_button, self.back_button))
        self._fitting = False

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fitting:
            return
        self._fitting = True
        try:
            self.body.activate()
            self._fit_labels()
        finally:
            self._fitting = False

    def _fit_labels(self) -> None:
        """Shrink the type only when the German labels would otherwise be clipped."""
        buttons = (
            self.pause_button,
            self.resume_button,
            self.stop_button,
            self.results_button,
            self.back_button,
        )
        tight = False
        for button in buttons:
            if button.width() <= 0:
                continue
            font = button.font()
            font.setPixelSize(12)
            needed = QFontMetrics(font).horizontalAdvance(button.text()) + 12
            if needed > button.width():
                tight = True
                break
        value = "small" if tight else ""
        for button in buttons:
            if button.property("fit") == value:
                continue
            button.setProperty("fit", value)
            button.style().unpolish(button)
            button.style().polish(button)


def mark_leader(table: QTableWidget) -> None:
    """Mark position 1. The table order is the ranking the race already produced."""
    for row in range(table.rowCount()):
        position = table.item(row, COLUMN_POSITION)
        leader = position is not None and position.text() == "1"
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is None:
                continue
            item.setData(LEADER_ROLE, leader)
            font = item.font()
            font.setBold(leader and column in (COLUMN_POSITION, COLUMN_DRIVER))
            item.setFont(font)


class ElidingLabel(QLabel):
    """One line that shrinks with an ellipsis instead of painting past the panel."""

    def __init__(self, object_name: str, text: str = "", *, color: str, bold: bool = False) -> None:
        super().__init__(text)
        self.setObjectName(object_name)
        self.setMinimumSize(0, 0)
        self.setWordWrap(False)
        self._color = color
        if bold:
            font = self.font()
            font.setBold(True)
            self.setFont(font)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """The line can become narrower than its text; the paint then adds an ellipsis."""
        return QSize(0, max(super().minimumSizeHint().height(), self.fontMetrics().height()))

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        metrics = QFontMetrics(self.font())
        width = max(self.width() - 2, 0)
        text = metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight, width)
        painter.setPen(QColor(self._color))
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )


def _meta(object_name: str) -> QLabel:
    return ElidingLabel(object_name, color=COLORS.text_secondary)


def _button(text: str, object_name: str, role: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    button.setProperty("compact", True)
    button.setMinimumWidth(0)
    button.setMinimumHeight(0)
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    if role:
        set_role(button, role)
    else:
        set_role(button, "")
    return button


def _button_row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(SPACE.xs)
    for button in buttons:
        row.addWidget(button, 1)
    return row
