"""Track list and editor."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from carrera.core.catalog import TrackInfo
from carrera.core.domain import TrackId
from carrera.core.i18n import Translator
from carrera.modules.tracks.service import MAX_LANES, MIN_LANES, TrackInput, TrackService
from carrera.uikit import EntityPage, EntityRow, FormDialog


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
        self.lane_count_edit = QSpinBox()
        self.lane_count_edit.setObjectName("track-lane-count")
        self.lane_count_edit.setRange(0, 99)
        self.lane_count_edit.setValue(track.lane_count if track else 2)
        self.image_edit = QLineEdit(track.image_path or "" if track else "")
        self.image_edit.setObjectName("track-image")
        browse = QPushButton(tr("track.browse"))
        browse.clicked.connect(self._browse)
        image_row = QHBoxLayout()
        image_row.addWidget(self.image_edit, 1)
        image_row.addWidget(browse)
        self.form.addRow(tr("track.field.name"), self.name_edit)
        self.form.addRow(tr("track.field.description"), self.description_edit)
        self.form.addRow(
            translator.format("track.field.lane_count", minimum=MIN_LANES, maximum=MAX_LANES),
            self.lane_count_edit,
        )
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
            lane_count=self.lane_count_edit.value(),
            description=self.description_edit.toPlainText(),
            image_path=self.image_edit.text(),
        )
        if self._track is None:
            self._service.create_track(data)
        else:
            self._service.update_track(self._track.id, data)


class TracksPage(EntityPage):
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
