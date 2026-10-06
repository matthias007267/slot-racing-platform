"""The settings page stores race-sound choices and can preview them."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QSlider, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.app.main_window import MainWindow
from slot_racing.app.runtime import Runtime
from slot_racing.core.config import AppConfig, load_config
from slot_racing.core.i18n import Translator
from slot_racing.core.storage import Database
from slot_racing.modules.races.translations import TRANSLATIONS
from slot_racing.modules.races.ui.audio_settings import AudioSettings
from slot_racing.modules.races.ui.race_audio import (
    GO_TONE,
    LAMP_TONE,
    START_SOUND_GAP_MS,
    START_SOUND_IDS,
    RaceAudio,
    StartSoundPreview,
    ToneSpec,
    resolve_tone,
)
from slot_racing.modules.races.ui.start_cue import DEFAULT_START_STEPS
from tests.app.test_shell import make_window
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page


class RecordingOutput:
    def __init__(self) -> None:
        self.calls: list[tuple[ToneSpec, int]] = []

    def play(self, tone: ToneSpec, *, volume: int) -> None:
        self.calls.append((tone, volume))


def test_settings_show_and_store_the_audio_choice(qtbot: QtBot, env: Env) -> None:
    env.runtime.config.audio_enabled = False
    env.runtime.config.audio_volume = 25
    window, _page = open_page(qtbot, env, "settings")
    checkbox, slider, percent, button, title = _controls(window)
    assert title.text() == "Audio"
    assert checkbox.text() == "Rennsounds aktivieren"
    assert not checkbox.isChecked()
    assert slider.value() == 25
    assert percent.text() == "25 %"
    assert not slider.isEnabled()
    assert button.text() == "Lautsprecher testen"
    assert not button.isEnabled()
    checkbox.setChecked(True)
    assert slider.isEnabled()
    assert button.isEnabled()
    slider.setValue(40)
    assert percent.text() == "40 %"
    assert env.runtime.config.audio_enabled is True
    assert env.runtime.config.audio_volume == 40


def test_audio_choices_are_written_to_the_config_file(qtbot: QtBot, tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    runtime = Runtime.create(AppConfig(), config_path=path, database=Database.in_memory())
    try:
        window = make_window(qtbot, runtime)
        window.select("settings")
        checkbox, slider, percent, button, _title = _controls(window)
        slider.setValue(55)
        assert percent.text() == "55 %"
        checkbox.setChecked(False)
        assert not slider.isEnabled()
        assert not button.isEnabled()
        stored = load_config(path)
        assert stored.audio_volume == 55
        assert stored.audio_enabled is False
    finally:
        runtime.shutdown()


def test_the_preview_uses_the_start_sound_ids_and_does_not_block(qtbot: QtBot) -> None:
    assert START_SOUND_GAP_MS == 1000
    assert tuple(step.sound_id for step in DEFAULT_START_STEPS) == START_SOUND_IDS
    tones = [resolve_tone(sound_id) for sound_id in START_SOUND_IDS]
    assert tones == [LAMP_TONE] * 5 + [GO_TONE]
    host = QWidget()
    qtbot.addWidget(host)
    output = RecordingOutput()
    preview = StartSoundPreview(RaceAudio(output, parent=host), parent=host, interval_ms=20)
    assert preview.start()
    assert not preview.start()
    # The first tone is handed to the output and the rest stay on the timer.
    # A blocking implementation would have finished the sequence before returning.
    assert preview.running
    assert preview._timer.isActive()
    assert preview._index == 1
    assert [tone for tone, _volume in output.calls] == [LAMP_TONE]
    qtbot.waitUntil(lambda: len(output.calls) == 6, timeout=2000)
    assert [tone for tone, _volume in output.calls] == [LAMP_TONE] * 5 + [GO_TONE]
    assert not preview.running


def test_the_speaker_button_plays_one_sequence(qtbot: QtBot) -> None:
    panel, output = _panel(qtbot)
    assert panel._preview._timer.interval() == START_SOUND_GAP_MS
    panel._preview._timer.setInterval(20)
    panel.show()
    panel.test.click()
    assert not panel.test.isEnabled()
    panel.test.click()
    assert [tone for tone, _volume in output.calls] == [LAMP_TONE]
    qtbot.waitUntil(lambda: len(output.calls) == 6, timeout=2000)
    assert [tone for tone, _volume in output.calls] == [LAMP_TONE] * 5 + [GO_TONE]
    assert panel.test.isEnabled()


def test_turning_audio_off_stops_the_preview(qtbot: QtBot) -> None:
    panel, output = _panel(qtbot)
    panel._preview._timer.setInterval(30)
    panel.show()
    panel.test.click()
    qtbot.waitUntil(lambda: len(output.calls) >= 2, timeout=1000)
    heard = len(output.calls)
    panel.enabled.setChecked(False)
    qtbot.wait(200)
    assert len(output.calls) == heard
    assert not panel.slider.isEnabled()
    assert not panel.test.isEnabled()
    assert not panel._preview.running


def test_hiding_or_destroying_the_page_stops_later_tones(qtbot: QtBot) -> None:
    hidden, hidden_output = _panel(qtbot)
    hidden._preview._timer.setInterval(30)
    hidden.show()
    hidden.test.click()
    hidden.hide()
    qtbot.wait(200)
    assert [tone for tone, _volume in hidden_output.calls] == [LAMP_TONE]

    destroyed, destroyed_output = _panel(qtbot)
    destroyed._preview._timer.setInterval(30)
    destroyed.show()
    destroyed.test.click()
    destroyed.deleteLater()
    qtbot.wait(200)
    assert [tone for tone, _volume in destroyed_output.calls] == [LAMP_TONE]


def _panel(qtbot: QtBot, config: AppConfig | None = None) -> tuple[AudioSettings, RecordingOutput]:
    stored = config or AppConfig()
    output = RecordingOutput()
    panel = AudioSettings(_translator(), stored, audio=RaceAudio(output, config=stored))
    qtbot.addWidget(panel)
    return panel, output


def _translator() -> Translator:
    translator = Translator("de")
    translator.add_catalog("de", TRANSLATIONS["de"])
    return translator


def _controls(
    window: MainWindow,
) -> tuple[QCheckBox, QSlider, QLabel, QPushButton, QLabel]:
    checkbox = window.findChild(QCheckBox, "audio-enabled")
    slider = window.findChild(QSlider, "audio-volume")
    percent = window.findChild(QLabel, "audio-volume-percent")
    button = window.findChild(QPushButton, "audio-test")
    title = window.findChild(QLabel, "settings-section-audio")
    assert checkbox is not None
    assert slider is not None
    assert percent is not None
    assert button is not None
    assert title is not None
    return checkbox, slider, percent, button, title
