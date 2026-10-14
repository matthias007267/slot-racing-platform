"""Live ranking on the surface shown between heats.

The rows are the race snapshot's standings. Column choice, order and the rectangle
come from the saved HUD layout. The table scrolls; it does not drop a configured
column or a driver to make the box smaller.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.domain import RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.hud import (
    BETWEEN_RANKING_FONT_BASE,
    RANKING_COLUMN_LABELS,
    BetweenRanking,
    between_ranking_pixels,
    effective_px,
    normalize_between_ranking,
    project_ranking_cells,
    visible_ranking_columns,
)
from slot_racing.modules.races.runner import RaceSnapshot
from slot_racing.modules.races.ui.formatting import ranking_cells
from slot_racing.modules.races.ui.race_briefing import BriefingBoard
from slot_racing.uikit import fill_table
from slot_racing.uikit.theme import set_role


class ConfiguredRanking(QFrame):
    """One ranking table. A new snapshot replaces the rows in the same update."""

    def __init__(self, translator: Translator) -> None:
        super().__init__()
        self.setObjectName("heat-ranking")
        set_role(self, "card")
        self._translator = translator
        self.caption = QLabel(translator.translate("hud.widget.live_ranking"))
        self.caption.setObjectName("heat-ranking-caption")
        self.empty = QLabel(translator.translate("hud.ranking.no_columns"))
        self.empty.setObjectName("heat-ranking-empty")
        self.empty.setWordWrap(True)
        self.empty.hide()
        self.table = QTableWidget(0, 0)
        self.table.setObjectName("heat-ranking-table")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addWidget(self.caption)
        layout.addWidget(self.empty)
        layout.addWidget(self.table, 1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def show_standings(self, snapshot: RaceSnapshot, frame: BetweenRanking) -> None:
        """Paint ``snapshot`` standings. Does not clear the table before the new rows exist."""
        normalized = normalize_between_ranking(frame)
        column_ids = visible_ranking_columns(normalized)
        pixels = effective_px(BETWEEN_RANKING_FONT_BASE, normalized.font_scale, 100)
        font = QFont(self.table.font())
        font.setPixelSize(pixels)
        self.table.setFont(font)
        self.caption.setFont(font)
        self.table.horizontalHeader().setFont(font)
        self.table.verticalHeader().setDefaultSectionSize(pixels + 12)
        translate = self._translator.translate
        self.table.setColumnCount(len(column_ids))
        self.table.setHorizontalHeaderLabels(
            [translate(RANKING_COLUMN_LABELS[column_id]) for column_id in column_ids]
        )
        self.empty.setVisible(not column_ids)
        self.table.setVisible(bool(column_ids))
        if not column_ids:
            self.table.setRowCount(0)
            return
        ended = snapshot.status is RaceStatus.FINISHED
        paused = snapshot.status is RaceStatus.PAUSED
        ranking = snapshot.standings or snapshot.rows
        fill_table(
            self.table,
            [
                project_ranking_cells(
                    ranking_cells(self._translator, snapshot, row, ended=ended, paused=paused),
                    normalized,
                )
                for row in ranking
            ],
            [row.participant_id or row.lane for row in ranking],
        )
        self.table.resizeColumnsToContents()


class BetweenSurface(QWidget):
    """Briefing and the configured ranking. The host keeps the start controls outside."""

    def __init__(self, translator: Translator) -> None:
        super().__init__()
        self.setObjectName("heat-between")
        self.board = BriefingBoard()
        scroll = QScrollArea()
        scroll.setObjectName("heat-briefing-scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.board)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)
        self.ranking = ConfiguredRanking(translator)
        self.ranking.setParent(self)
        self._frame = normalize_between_ranking(BetweenRanking())
        self._snapshot: RaceSnapshot | None = None

    def show_standings(self, snapshot: RaceSnapshot, frame: BetweenRanking) -> None:
        self._snapshot = snapshot
        self._frame = normalize_between_ranking(frame)
        self.ranking.show_standings(snapshot, self._frame)
        self._place()

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._place()

    def showEvent(self, event: object) -> None:  # noqa: N802
        super().showEvent(event)  # type: ignore[arg-type]
        self._place()

    def _place(self) -> None:
        rect = between_ranking_pixels(self._frame, self.width(), self.height())
        self.ranking.setGeometry(rect.x, rect.y, rect.width, rect.height)
        self.ranking.setVisible(self._frame.visible and rect.width > 1 and rect.height > 1)
        if self.ranking.isVisible():
            self.ranking.raise_()
