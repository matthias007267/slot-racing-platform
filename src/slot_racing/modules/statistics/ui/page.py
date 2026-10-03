"""Statistics page. Lanes follow the selected track."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from slot_racing.core.catalog import TimeMeasurementCatalog, TrackCatalog
from slot_racing.core.domain import TrackId
from slot_racing.core.i18n import Translator
from slot_racing.modules.statistics.lanes import lane_statistic_cells, statistics_for_track
from slot_racing.uikit import fill_table, make_table
from slot_racing.uikit.theme import configure_page, set_role


class StatisticsPage(QWidget):
    """Best time of each lane on one track, read from the measurements already stored."""

    def __init__(
        self,
        translator: Translator,
        tracks: TrackCatalog,
        measurements: Callable[[], TimeMeasurementCatalog | None],
    ) -> None:
        super().__init__()
        self.setObjectName("statistics-page")
        self._translator = translator
        self._tracks = tracks
        self._measurements = measurements
        tr = translator.translate
        self.track_combo = QComboBox()
        self.track_combo.setObjectName("statistics-track")
        track_label = QLabel(tr("statistics.track"))
        track_label.setObjectName("statistics-track-label")
        choice = QHBoxLayout()
        choice.setContentsMargins(0, 0, 0, 0)
        choice.addWidget(track_label)
        choice.addWidget(self.track_combo, 1)
        self.section = QLabel(tr("statistics.lanes"))
        self.section.setObjectName("statistics-lanes-heading")
        set_role(self.section, "section")
        self.empty = QLabel(tr("statistics.empty"))
        self.empty.setObjectName("statistics-empty")
        set_role(self.empty, "caption")
        self.table = make_table(
            [
                tr("statistics.column.lane"),
                tr("statistics.column.driver"),
                tr("statistics.column.vehicle"),
                tr("statistics.column.best"),
            ],
            "statistics-lanes",
        )
        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addLayout(choice)
        layout.addWidget(self.section)
        layout.addWidget(self.empty)
        layout.addWidget(self.table, 1)
        self.track_combo.currentIndexChanged.connect(lambda _index: self._show_selected())
        self.refresh()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        """Reload tracks and the lanes of the selected one. Stored data is only read."""
        selected = self.track_combo.currentData()
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        for track in self._tracks.list_tracks():
            self.track_combo.addItem(track.name, track.id)
        if selected is not None:
            index = self.track_combo.findData(selected)
            if index >= 0:
                self.track_combo.setCurrentIndex(index)
        self.track_combo.blockSignals(False)
        self._show_selected()

    def _show_selected(self) -> None:
        raw = self.track_combo.currentData()
        track = None if raw is None else self._tracks.get_track(TrackId(raw))
        self.empty.setVisible(track is None)
        self.section.setVisible(track is not None)
        self.table.setVisible(track is not None)
        if track is None:
            fill_table(self.table, [])
            return
        catalog = self._measurements()
        rows = () if catalog is None else catalog.list_for_track(track.id)
        lines = statistics_for_track(track, rows)
        fill_table(self.table, [lane_statistic_cells(line) for line in lines])
