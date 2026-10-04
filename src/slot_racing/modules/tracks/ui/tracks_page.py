"""Track list and editor."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from slot_racing.core.catalog import TrackInfo
from slot_racing.core.domain import TrackId
from slot_racing.core.domain.lanes import DEFAULT_LANE_COUNT, MAX_LANE_COUNT, MIN_LANE_COUNT
from slot_racing.core.i18n import Translator
from slot_racing.modules.tracks.service import TrackInput, TrackService
from slot_racing.uikit import EntityPage, EntityRow, FormDialog, selected_id
from slot_racing.uikit.theme import set_role


class TrackDialog(FormDialog):
    def __init__(
        self,
        translator: Translator,
        service: TrackService,
        track: TrackInfo | None,
        parent: QWidget | None = None,
    ) -> None:
        tr = translator.translate
        super().__init__(
            translator, tr("track.dialog.edit" if track else "track.dialog.new"), parent
        )
        self._service = service
        self._track = track
        self.name_edit = QLineEdit(track.name if track else "")
        self.name_edit.setObjectName("track-name")
        self.description_edit = QPlainTextEdit(track.description or "" if track else "")
        self.description_edit.setObjectName("track-description")
        self.description_edit.setFixedHeight(70)
        self.lane_count_edit = QComboBox()
        self.lane_count_edit.setObjectName("track-lane-count")
        stored = None if track is None else track.lane_count
        for count in _lane_choices(stored):
            self.lane_count_edit.addItem(str(count), count)
        selected = DEFAULT_LANE_COUNT if stored is None else stored
        self.lane_count_edit.setCurrentIndex(self.lane_count_edit.findData(selected))
        self.image_edit = QLineEdit(track.image_path or "" if track else "")
        self.image_edit.setObjectName("track-image")
        browse = QPushButton(tr("track.browse"))
        set_role(browse, "ghost")
        browse.clicked.connect(self._browse)
        image_row = QHBoxLayout()
        image_row.addWidget(self.image_edit, 1)
        image_row.addWidget(browse)
        self.form.addRow(tr("track.field.name"), self.name_edit)
        self.form.addRow(tr("track.field.description"), self.description_edit)
        self.form.addRow(tr("track.field.lane_count"), self.lane_count_edit)
        self.form.addRow(tr("track.field.image"), image_row)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.translator.translate("track.field.image"), "", "Images (*.png *.jpg *.svg)"
        )
        if path:
            self.image_edit.setText(path)

    def submit(self) -> None:
        data = TrackInput(
            name=self.name_edit.text(),
            lane_count=int(self.lane_count_edit.currentData()),
            description=self.description_edit.toPlainText(),
            image_path=self.image_edit.text(),
        )
        if self._track is None:
            self._service.create_track(data)
        else:
            self._service.update_track(self._track.id, data)


def _lane_choices(stored: int | None) -> list[int]:
    """2, 3 and 4, plus a count already stored so opening the dialog does not change it."""
    choices = list(range(MIN_LANE_COUNT, MAX_LANE_COUNT + 1))
    if stored is not None and stored not in choices and stored >= 1:
        choices.append(stored)
        choices.sort()
    return choices


class TracksPage(EntityPage):
    timing_requested = Signal(int)
    """Emitted with the id of the track whose timing configuration should be opened."""

    def __init__(self, translator: Translator, service: TrackService) -> None:
        super().__init__(
            translator,
            title_key="nav.tracks",
            header_keys=[
                "track.field.name",
                "track.column.lanes",
                "track.field.description",
                "common.active",
            ],
            name="tracks",
        )
        self._service = service
        self.timing_button = QPushButton(translator.translate("track.timing"))
        self.timing_button.setObjectName("tracks-timing")
        set_role(self.timing_button, "secondary")
        self.buttons.insertWidget(self.buttons.count() - 1, self.timing_button)
        self.timing_button.clicked.connect(lambda: self.open_timing_selected())
        self.table.itemSelectionChanged.connect(self._update_timing_button)
        self._update_timing_button()

    def open_timing_selected(self) -> None:
        track_id = selected_id(self.table)
        if track_id is not None:
            self.timing_requested.emit(track_id)

    def _update_timing_button(self) -> None:
        self.timing_button.setEnabled(selected_id(self.table) is not None)

    def load_rows(self) -> list[EntityRow]:
        tr = self.translator.translate
        return [
            EntityRow(
                id=track.id,
                cells=(
                    track.name,
                    str(track.lane_count),
                    track.description or "",
                    tr("common.yes" if track.is_active else "common.no"),
                ),
                active=track.is_active,
            )
            for track in self._service.list_tracks()
        ]

    def create_dialog(self, entity_id: int | None) -> QDialog:
        track = None if entity_id is None else self._service.get_track(TrackId(entity_id))
        return TrackDialog(self.translator, self._service, track, self)

    def set_entity_active(self, entity_id: int, active: bool) -> None:
        self._service.set_active(TrackId(entity_id), active)

    def delete_entity(self, entity_id: int) -> None:
        self._service.delete_track(TrackId(entity_id))
