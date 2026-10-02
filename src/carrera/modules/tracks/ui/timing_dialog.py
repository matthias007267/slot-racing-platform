"""Dialog to edit one timing position and the sensor assigned to it."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QWidget

from carrera.core.i18n import Translator
from carrera.modules.tracks.timing_editor import TimingDraft
from carrera.modules.tracks.ui.common import entry_text, type_text
from carrera.uikit import FormDialog


class PositionDialog(FormDialog):
    def __init__(
        self,
        translator: Translator,
        draft: TimingDraft,
        position_id: str,
        parent: QWidget | None = None,
    ) -> None:
        tr = translator.translate
        super().__init__(translator, tr("timing.dialog.position"), parent)
        self._draft = draft
        self._position_id = position_id
        entry = draft.entry(position_id)
        self.type_label = QLabel(type_text(translator, entry.type, draft.entries.index(entry)))
        self.type_label.setObjectName("timing-position-type")
        self.name_edit = QLineEdit(entry.name or "")
        self.name_edit.setObjectName("timing-position-name")
        self.name_edit.setPlaceholderText(entry_text(translator, draft.entries, entry))
        self.sensor_id_edit = QLineEdit(entry.sensor_id)
        self.sensor_id_edit.setObjectName("timing-sensor-id")
        self.sensor_name_edit = QLineEdit(entry.sensor_name or "")
        self.sensor_name_edit.setObjectName("timing-sensor-name")
        self.hardware_id_edit = QLineEdit(entry.hardware_id or "")
        self.hardware_id_edit.setObjectName("timing-sensor-hardware")
        self.hardware_id_edit.setPlaceholderText(tr("timing.hardware_hint"))
        self.active_check = QCheckBox(tr("timing.sensor.active"))
        self.active_check.setObjectName("timing-sensor-active")
        self.active_check.setChecked(entry.active)
        self.form.addRow(tr("timing.field.type"), self.type_label)
        self.form.addRow(tr("timing.field.position_name"), self.name_edit)
        self.form.addRow(tr("timing.field.sensor_id"), self.sensor_id_edit)
        self.form.addRow(tr("timing.field.sensor_name"), self.sensor_name_edit)
        self.form.addRow(tr("timing.field.hardware_id"), self.hardware_id_edit)
        self.form.addRow("", self.active_check)

    def submit(self) -> None:
        self._draft.update(
            self._position_id,
            name=self.name_edit.text(),
            sensor_id=self.sensor_id_edit.text(),
            sensor_name=self.sensor_name_edit.text(),
            hardware_id=self.hardware_id_edit.text(),
            active=self.active_check.isChecked(),
        )
