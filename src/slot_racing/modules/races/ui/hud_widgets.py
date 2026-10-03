"""Independent HUD panels. They display values the live view already has; they compute no race."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontInfo,
    QFontMetrics,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
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
_MIN_TEXT_FONT = 6
_CELL_PAD = 16
_TEXT_INSET = 2
LEADER_ROLE = Qt.ItemDataRole.UserRole + 1

COLUMN_POSITION = 0
COLUMN_DRIVER = 1
COLUMN_VEHICLE = 2
COLUMN_LAPS = 5
COLUMN_BEST = 8
_PRIMARY_COLUMNS = frozenset({COLUMN_POSITION, COLUMN_DRIVER, COLUMN_LAPS})
_OPTIONAL_COLUMNS = frozenset({3, 4, 6, 7, 9, 10, 11})
_VEHICLE_MIN_WIDTH = 460
_FULL_TABLE_WIDTH = 980


def _wrap_rows(
    items: list[tuple[QLabel, int]], avail_w: int, gap: int
) -> list[list[tuple[QLabel, int]]]:
    rows: list[list[tuple[QLabel, int]]] = []
    current: list[tuple[QLabel, int]] = []
    used = 0
    for label, width in items:
        extra = width if not current else width + gap
        if current and used + extra > avail_w:
            rows.append(current)
            current = [(label, width)]
            used = width
        else:
            current.append((label, width))
            used += extra
    if current:
        rows.append(current)
    return rows


def _row_space(row: list[tuple[QLabel, int]], avail_w: int, gap: int) -> int:
    used = sum(width for _label, width in row) + gap * max(len(row) - 1, 0)
    return avail_w - used


def _pixel_size(font: QFont) -> int:
    if font.pixelSize() > 0:
        return font.pixelSize()
    resolved = QFontInfo(font).pixelSize()
    return resolved if resolved > 0 else 13


def _metrics_at(font: QFont, size: int | None) -> QFontMetrics:
    if size is None:
        return QFontMetrics(font)
    sized = QFont(font)
    sized.setPixelSize(size)
    return QFontMetrics(sized)


class HudWidget(QFrame):
    """One movable panel. The stage positions it; the panel only lays out its own content."""

    def __init__(self, widget_id: str) -> None:
        super().__init__()
        self.widget_id = widget_id
        self.setObjectName(f"hud-panel-{widget_id}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # QSize(0, 0) is null and Qt treats it as "no minimum set", so the layout
        # minimum (the full text width) becomes the panel minimum. Windows then
        # refuses the rectangle the stage assigns. 1x1 is a real minimum.
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        set_role(self, "hud-panel")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(SPACE.xs, SPACE.xs, SPACE.xs, SPACE.xs)
        self.body.setSpacing(SPACE.xs)
        self.body.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(1, 1)

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
        self._fitting = False
        self.name_label = _TrackingLabel("live-name", color=COLORS.text, bold=True)
        self.name_label.setText(translator.translate("race.live.title"))
        self.track_label = _TrackingLabel("live-track", color=COLORS.text_secondary)
        self.provider_label = _TrackingLabel("live-provider", color=COLORS.text_secondary)
        self.header_status = _TrackingLabel("hud-header-status", color=COLORS.text_secondary)
        self.participants_label = _TrackingLabel("hud-participants", color=COLORS.text_secondary)
        self._meta_labels = (
            self.header_status,
            self.track_label,
            self.participants_label,
            self.provider_label,
        )
        # Facts are placed by hand. A grid would squeeze them below the width reported
        # by the label's own font metrics, which is what Windows resolves after DPI.
        for label in (self.name_label, *self._meta_labels):
            label.setParent(self)
            label._after_text = self._fit_header

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_header()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_header()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._fit_header()

    def _fit_header(self) -> None:
        """Fit status, track, field and timing source inside the rectangle the stage assigned."""
        if self._fitting or self.width() < 8 or self.height() < 8:
            return
        self._fitting = True
        try:
            self._apply_meta_font(None)
            if self._place_facts():
                return
            for size in range(_pixel_size(self.font()) - 1, _MIN_TEXT_FONT - 1, -1):
                self._apply_meta_font(size)
                if self._place_facts():
                    return
            self._apply_meta_font(_MIN_TEXT_FONT)
            self._place_facts()
        finally:
            self._fitting = False

    def _apply_meta_font(self, size: int | None) -> None:
        for label in self._meta_labels:
            font = QFont(self.font())
            if size is not None:
                font.setPixelSize(size)
            label.setFont(font)
        name = QFont(self.font())
        name.setBold(True)
        if size is not None:
            name.setPixelSize(size)
        self.name_label.setFont(name)

    def _place_facts(self) -> bool:
        """Give every fact its own text width. The name uses whatever space is left."""
        margin = SPACE.xs
        gap = SPACE.xs
        avail_w = self.width() - 2 * margin
        avail_h = self.height() - 2 * margin
        if avail_w <= 0 or avail_h <= 0:
            return False
        facts = list(self._meta_labels)
        # The label paints with a 2px inset. The width has to cover that, or the
        # last letters are ellipsized even though the advance itself would fit.
        widths = [
            label.fontMetrics().horizontalAdvance(label.text()) + _TEXT_INSET for label in facts
        ]
        if any(width > avail_w for width in widths):
            return False
        line = max(label.fontMetrics().height() for label in (self.name_label, *facts))
        rows = _wrap_rows(list(zip(facts, widths, strict=True)), avail_w, gap)
        gaps = max(len(rows) - 1, 0)
        if len(rows) * line + gaps * gap > avail_h:
            return False
        own_name_row = (len(rows) + 1) * line + len(rows) * gap <= avail_h
        y = margin
        if own_name_row:
            self.name_label.setGeometry(margin, y, avail_w, line)
            y += line + gap
            name_row = -1
        else:
            name_row = max(
                range(len(rows)),
                key=lambda index: _row_space(rows[index], avail_w, gap),
            )
        for index, row in enumerate(rows):
            x = margin
            for label, width in row:
                label.setGeometry(x, y, width, line)
                x += width + gap
            if index == name_row:
                leftover = margin + avail_w - x
                self.name_label.setGeometry(x, y, max(leftover, 0), line)
            y += line + gap
        return True


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
        # The style's default minimum would force a section wider than the lap text.
        header.setMinimumSectionSize(1)
        self._base_font: QFont | None = None
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
            self._fit_rows()
            self._reserve_vertical_bar()
            self._fit_table_font()
            self._apply_columns()
            mark_leader(self.table)
        finally:
            self._fitting = False

    def _fit_table_font(self) -> None:
        """Shrink the table font until place, name and lap count share the viewport.

        The check uses the table's own font metrics after ``setFont``. A detached
        ``QFontMetrics`` is narrower than that on Windows, so it used to keep a font
        whose lap column could no longer hold the digits. The first font seen stays
        the baseline, so a wider panel can grow the type back up.
        """
        if self._base_font is None:
            self._base_font = QFont(self.table.font())
        if self._column_budget() < 40:
            return
        # Prefer a size that also keeps the best-lap column. Only when that cannot
        # fit does the column disappear, and the names and lap counts stay whole.
        if not self._apply_fitting_font(keep_best=True):
            self._apply_fitting_font(keep_best=False)

    def _apply_fitting_font(self, *, keep_best: bool) -> bool:
        base = self._base_font
        if base is None:
            return False
        sizes: list[int | None] = [None]
        sizes.extend(range(_pixel_size(base) - 1, _MIN_TEXT_FONT - 1, -1))
        for size in sizes:
            font = QFont(base)
            if size is not None:
                font.setPixelSize(size)
            self.table.setFont(font)
            self._reserve_vertical_bar()
            extra = 64 if keep_best else 0
            if self._primary_width() + extra <= self._column_budget():
                return True
        return False

    def _primary_width(self) -> int:
        return sum(self._cell_need(column) for column in _PRIMARY_COLUMNS)

    def _cell_need(self, column: int) -> int:
        """Text advance plus the padding the narrow-panel checks require."""
        if column in (COLUMN_POSITION, COLUMN_DRIVER):
            font = QFont(self.table.font())
            font.setBold(True)
            metrics = QFontMetrics(font)
        else:
            metrics = self.table.fontMetrics()
        widest = 0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, column)
            if item is not None and item.text():
                widest = max(widest, metrics.horizontalAdvance(item.text()))
        if widest <= 0:
            return _CELL_PAD
        return widest + _CELL_PAD

    def _column_budget(self) -> int:
        self.table.updateGeometries()
        width = self.table.viewport().width()
        if width <= 0:
            width = self.table.width() or self.width()
        return max(width - 2, 1)

    def _reserve_vertical_bar(self) -> None:
        """Keep the vertical bar's width out of the column budget before columns are set."""
        rows = self.table.rowCount()
        row_height = self.table.verticalHeader().defaultSectionSize()
        if rows > 0 and self.table.rowHeight(0) > 0:
            row_height = self.table.rowHeight(0)
        overflows = rows > 0 and row_height * rows > self.table.viewport().height()
        policy = (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
            if overflows
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        if self.table.verticalScrollBarPolicy() != policy:
            self.table.setVerticalScrollBarPolicy(policy)
        self.table.updateGeometries()

    def _apply_columns(self) -> None:
        width = self._column_budget()
        if width < 40:
            return
        show_vehicle = width >= _VEHICLE_MIN_WIDTH
        show_rest = width >= _FULL_TABLE_WIDTH
        for column in range(self.table.columnCount()):
            if column == COLUMN_VEHICLE:
                hidden = not show_vehicle
            elif column in _OPTIONAL_COLUMNS:
                hidden = not show_rest
            else:
                hidden = False
            self.table.setColumnHidden(column, hidden)
        self._assign_widths(width)

    def _assign_widths(self, viewport: int) -> None:
        """Keep place, driver and lap count whole. Drop the best lap when it no longer fits."""
        visible = [
            column
            for column in range(self.table.columnCount())
            if not self.table.isColumnHidden(column)
        ]
        floors = {
            column: self._cell_need(column) for column in visible if column in _PRIMARY_COLUMNS
        }
        remaining = viewport - sum(floors.values())
        secondary = [column for column in visible if column not in _PRIMARY_COLUMNS]
        if COLUMN_BEST in secondary and remaining < 64:
            self.table.setColumnHidden(COLUMN_BEST, True)
            secondary = [column for column in secondary if column != COLUMN_BEST]
            visible = [column for column in visible if column != COLUMN_BEST]
        chosen: dict[int, int] = dict(floors)
        if COLUMN_BEST in secondary:
            chosen[COLUMN_BEST] = 64
            remaining -= 64
        for column in secondary:
            if column == COLUMN_BEST:
                continue
            need = self._cell_need(column)
            take = need if need <= max(remaining, 0) else max(remaining, 1)
            chosen[column] = take
            remaining -= take
        if remaining > 0 and COLUMN_DRIVER in chosen:
            if COLUMN_VEHICLE in chosen:
                half = remaining // 2
                chosen[COLUMN_VEHICLE] += half
                chosen[COLUMN_DRIVER] += remaining - half
            else:
                chosen[COLUMN_DRIVER] += remaining
        overflow = sum(chosen.values()) - viewport
        if overflow > 0:
            for column in reversed(secondary):
                if column == COLUMN_BEST or column not in chosen:
                    continue
                cut = min(max(0, chosen[column] - 1), overflow)
                chosen[column] -= cut
                overflow -= cut
            if overflow > 0 and COLUMN_BEST in chosen:
                self.table.setColumnHidden(COLUMN_BEST, True)
                del chosen[COLUMN_BEST]
        self._lock_columns(chosen, floors)

    def _lock_columns(self, chosen: dict[int, int], floors: dict[int, int]) -> None:
        """Fixed widths so a stretch section cannot steal pixels from the lap count."""
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(1)
        for column, width in chosen.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, max(width, 1))
        self.table.updateGeometries()
        overflow = self.table.horizontalScrollBar().maximum()
        guard = 0
        while overflow > 0 and guard < 8:
            guard += 1
            donor = None
            for column in (COLUMN_DRIVER, COLUMN_VEHICLE, *chosen):
                if column not in chosen:
                    continue
                floor = floors.get(column, 1)
                if chosen[column] > floor:
                    donor = column
                    break
            if donor is None:
                break
            cut = min(chosen[donor] - floors.get(donor, 1), overflow)
            chosen[donor] -= max(cut, 1)
            self.table.setColumnWidth(donor, chosen[donor])
            self.table.updateGeometries()
            overflow = self.table.horizontalScrollBar().maximum()

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
            fitted = option.font.pixelSize()
            if fitted <= 0:
                fitted = QFontInfo(option.font).pixelSize()
            # A narrow panel already shrank the table font. Do not paint the place
            # larger than that font, or the digit is clipped by the column.
            if fitted >= 13:
                font.setPixelSize(max(fitted, min(22, rect.height() - 10)))
            elif fitted > 0:
                font.setPixelSize(fitted)
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
        self._fit_px: int | None = None
        self._advance_cache: dict[tuple[str, str, int, bool], int] = {}

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refit()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._refit()

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest:
            self._refit()
        return handled

    def _buttons(self) -> tuple[QPushButton, ...]:
        return (
            self.pause_button,
            self.resume_button,
            self.stop_button,
            self.results_button,
            self.back_button,
        )

    def _refit(self) -> None:
        if self._fitting:
            return
        self._fitting = True
        try:
            self.body.activate()
            self._fit_labels()
        finally:
            self._fitting = False

    def _fit_labels(self) -> None:
        """Shrink every label until it fits the button the layout actually assigned.

        The advance is read from ``button.fontMetrics()`` after the font is applied.
        A detached metrics object underestimates Segoe UI, which left "Abbrechen"
        wider than its button. The theme's style sheet ignores ``setFont``, so a
        rule on the button itself is used when a style sheet is active.
        """
        buttons = self._buttons()
        if any(button.width() <= 0 for button in buttons):
            return
        start = max(_pixel_size(self.font()), _MIN_TEXT_FONT)
        winner = _MIN_TEXT_FONT
        for size in range(start, _MIN_TEXT_FONT - 1, -1):
            if all(
                self._advance_for(button, size) <= self._label_limit(button) for button in buttons
            ):
                winner = size
                break
        if self._fit_px == winner:
            return
        for button in buttons:
            self._set_pixel(button, winner)
        self._fit_px = winner

    def _label_limit(self, button: QPushButton) -> int:
        if button.width() <= 0:
            return 0
        option = QStyleOptionButton()
        button.initStyleOption(option)
        inner = button.style().subElementRect(
            QStyle.SubElement.SE_PushButtonContents, option, button
        )
        if inner.width() <= 0:
            return button.width()
        return min(button.width(), inner.width())

    def _advance_for(self, button: QPushButton, size: int) -> int:
        locked = self._stylesheet_fonts() or bool(button.property("fontLocked"))
        key = (button.objectName(), button.text(), size, locked)
        cached = self._advance_cache.get(key)
        if cached is not None:
            return cached
        before = button.fontMetrics().horizontalAdvance(button.text())
        before_size = button.font().pixelSize()
        self._set_pixel(button, size)
        advance = button.fontMetrics().horizontalAdvance(button.text())
        if not locked and before_size > size and before > 0 and advance >= before:
            button.setProperty("fontLocked", True)
            self._set_pixel(button, size)
            advance = button.fontMetrics().horizontalAdvance(button.text())
            key = (button.objectName(), button.text(), size, True)
        self._advance_cache[key] = advance
        return advance

    def _set_pixel(self, button: QPushButton, size: int) -> None:
        use_rule = self._stylesheet_fonts() or bool(button.property("fontLocked"))
        if use_rule:
            rule = f"QPushButton#{button.objectName()} {{ font-size: {size}px; padding: 0px 1px; }}"
            if button.styleSheet() != rule:
                button.setStyleSheet(rule)
        font = QFont(self.font())
        font.setPixelSize(size)
        if button.font().pixelSize() != size:
            button.setFont(font)

    def _stylesheet_fonts(self) -> bool:
        app = QApplication.instance()
        return isinstance(app, QApplication) and bool(app.styleSheet())


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
            # Start from the table font. An item font taken from the default would ignore
            # the size chosen for a narrow panel and paint the name past the column.
            font = QFont(table.font())
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


class _TrackingLabel(ElidingLabel):
    """A header line that refits the panel when its text changes."""

    def __init__(self, object_name: str, *, color: str, bold: bool = False) -> None:
        super().__init__(object_name, color=color, bold=bold)
        self._after_text: Callable[[], None] | None = None

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        callback = getattr(self, "_after_text", None)
        if callback is not None:
            callback()


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
