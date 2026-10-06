"""Audio block on the settings page. It only stores the level and plays a preview.

The preview uses the same sound ids as the start gantry. It does not create a
race, open the lights, or start a timing provider. A volume change applies on
the next tone. Turning sounds off, hiding the page, or destroying it drops
every tone that has not started yet.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.config import AppConfig, save_config
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.ui.race_audio import RaceAudio, StartSoundPreview
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import set_role, set_tone


class AudioSettings(QWidget):
    """Checkbox, volume slider and a speaker test for the stored race sounds."""

    def __init__(
        self,
        translator: Translator,
        config: AppConfig,
        config_path: Path | None = None,
        *,
        audio: RaceAudio | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("audio-settings")
        self._translator = translator
        self._config = config
        self._path = config_path
        self.audio = audio if audio is not None else RaceAudio(config=config, parent=self)
        if audio is not None and audio.parent() is None:
            audio.setParent(self)
        self._preview = StartSoundPreview(self.audio, parent=self)
        self._preview.finished.connect(self._apply_controls)
        translate = translator.translate

        self.enabled = QCheckBox(translate("audio.enabled"))
        self.enabled.setObjectName("audio-enabled")
        self.enabled.setChecked(config.audio_enabled)
        self.enabled.toggled.connect(self._toggle)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("audio-volume")
        self.slider.setRange(0, 100)
        self.slider.setValue(config.audio_volume)
        self.slider.valueChanged.connect(self._change_volume)
        self.percent = QLabel(_percent_text(config.audio_volume))
        self.percent.setObjectName("audio-volume-percent")
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.percent.setMinimumWidth(self.percent.fontMetrics().horizontalAdvance("100 %"))
        volume_row = QHBoxLayout()
        volume_row.addWidget(self.slider, 1)
        volume_row.addWidget(self.percent)

        self.test = QPushButton(translate("audio.test"))
        self.test.setObjectName("audio-test")
        set_role(self.test, "secondary")
        self.test.clicked.connect(self._test)

        self.message = QLabel()
        self.message.setObjectName("audio-message")
        self.message.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.enabled)
        volume_label = QLabel(translate("audio.volume"))
        volume_label.setObjectName("audio-volume-label")
        layout.addWidget(volume_label)
        layout.addLayout(volume_row)
        layout.addWidget(self.test)
        layout.addWidget(self.message)
        self._apply_controls()

    def event(self, event: QEvent) -> bool:
        if event.type() is QEvent.Type.DeferredDelete:
            self._preview.stop()
        return super().event(event)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._preview.stop()
        super().hideEvent(event)

    def _toggle(self, checked: bool) -> None:
        self._config.audio_enabled = checked
        self._persist()
        if not checked:
            self._preview.stop()
        self._apply_controls()

    def _change_volume(self, value: int) -> None:
        self.percent.setText(_percent_text(value))
        self._config.audio_volume = value
        self._persist()

    def _test(self) -> None:
        if self._preview.start():
            self._apply_controls()

    def _apply_controls(self) -> None:
        sounds_on = self.enabled.isChecked()
        self.slider.setEnabled(sounds_on)
        self.test.setEnabled(sounds_on and not self._preview.running)

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            save_config(self._config, self._path)
        except OSError as error:
            set_tone(self.message, "error")
            self.message.setText(describe_error(self._translator, error))


def _percent_text(value: int) -> str:
    return f"{value} %"
