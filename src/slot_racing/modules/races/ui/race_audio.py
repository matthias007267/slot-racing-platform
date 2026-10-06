"""Presentation sounds for a race. Playback never decides when the race starts.

``StartCue`` publishes a ``sound_id`` with each step. :class:`RaceAudio` maps that
id onto a tone and asks an output to start it. The call returns immediately.
A missing device, or a machine without the Qt Multimedia libraries, stays silent.
The cue's own timer and ``_commit_start`` stay the only start clock.

Importing this module does not load Qt Multimedia. The concrete backend is
opened later, so the race UI can still be imported when ``libpulse`` is absent.

Further cues (race end, a new best lap, the last lap) can be added in
:func:`resolve_tone`. They are not played today.
"""

from __future__ import annotations

import array
import logging
import math
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from slot_racing.core.config.models import (
    AUDIO_ENABLED_DEFAULT,
    AUDIO_VOLUME_DEFAULT,
    AppConfig,
)
from slot_racing.modules.races.ui.start_cue import DEFAULT_START_STEPS

logger = logging.getLogger(__name__)

# The gantry publishes these ids, one second apart. The preview plays the same ids.
START_SOUND_IDS: tuple[str, ...] = tuple(step.sound_id for step in DEFAULT_START_STEPS)
START_SOUND_GAP_MS = 1000

SAMPLE_RATE_HZ = 22_050

# One place for the start tones. Lamp beeps are the same short low tone.
# GO is an octave higher and longer, so the start is distinct.
LAMP_TONE_HZ = 349
LAMP_TONE_MS = 110
GO_TONE_HZ = 698
GO_TONE_MS = 260

_backend_reported = False


@dataclass(frozen=True, slots=True)
class ToneSpec:
    """A synthesized tone. Duration is how long the buffer is, not a wait."""

    frequency_hz: int
    duration_ms: int


LAMP_TONE = ToneSpec(LAMP_TONE_HZ, LAMP_TONE_MS)
GO_TONE = ToneSpec(GO_TONE_HZ, GO_TONE_MS)


def resolve_tone(sound_id: str) -> ToneSpec | None:
    """The tone for a cue id. Unknown ids are silent until a cue is added here."""
    if sound_id.startswith("light-"):
        return LAMP_TONE
    if sound_id == "go":
        return GO_TONE
    return None


def volume_gain(percent: int) -> float:
    """Share of the full playback level. 0 is silent and 100 is the maximum."""
    bounded = min(100, max(0, percent))
    return bounded / 100


def render_tone(tone: ToneSpec) -> bytes:
    """16-bit mono PCM at full scale, with a short fade so the tone does not click."""
    count = max(1, int(SAMPLE_RATE_HZ * tone.duration_ms / 1000))
    fade = min(int(SAMPLE_RATE_HZ * 0.008), count // 4)
    samples = array.array("h")
    for index in range(count):
        envelope = 1.0
        if fade > 0 and index < fade:
            envelope = index / fade
        elif fade > 0 and index > count - fade:
            envelope = (count - index) / fade
        wave = math.sin(2 * math.pi * tone.frequency_hz * index / SAMPLE_RATE_HZ)
        samples.append(int(wave * envelope * 32767))
    return samples.tobytes()


class ToneOutput(Protocol):
    """Starts one tone and returns. It must not wait for the tone to finish."""

    def play(self, tone: ToneSpec, *, volume: int) -> None: ...


class SilentToneOutput:
    """A player that returns at once. Used when Qt Multimedia cannot be loaded."""

    def play(self, tone: ToneSpec, *, volume: int) -> None:
        del tone, volume


def open_tone_output(parent: QObject) -> ToneOutput:
    """The Qt player, or a silent player when the native backend cannot be loaded.

    ``ImportError`` and ``OSError`` are what a missing ``libpulse.so.0`` raises
    while Qt Multimedia is imported. Other exceptions stay visible.
    """
    try:
        from slot_racing.modules.races.ui.qt_tone_output import QtToneOutput
    except (ImportError, OSError) as error:
        _report_missing_backend(error)
        return SilentToneOutput()
    return QtToneOutput(parent)


def _report_missing_backend(error: BaseException) -> None:
    global _backend_reported
    if _backend_reported:
        return
    _backend_reported = True
    logger.warning("Qt multimedia is unavailable (%s); start tones stay silent", error)


class RaceAudio(QObject):
    """Maps a start-cue id to a tone. A missing backend stays silent.

    When ``config`` is set, ``enabled`` and ``volume`` are that configuration.
    The settings page writes the same object, so the next tone uses the new level.
    """

    def __init__(
        self,
        output: ToneOutput | None = None,
        parent: QObject | None = None,
        *,
        config: AppConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self._output = open_tone_output(self) if output is None else output
        self._config = config
        self._enabled = AUDIO_ENABLED_DEFAULT
        self._volume = AUDIO_VOLUME_DEFAULT
        self._reported = False

    @property
    def output(self) -> ToneOutput:
        return self._output

    @property
    def enabled(self) -> bool:
        if self._config is not None:
            return self._config.audio_enabled
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        if self._config is not None:
            self._config.audio_enabled = self._enabled

    @property
    def volume(self) -> int:
        """Playback level from 0 to 100. The widget does not keep its own copy."""
        if self._config is not None:
            return self._config.audio_volume
        return self._volume

    def set_volume(self, percent: int) -> None:
        if percent < 0 or percent > 100:
            raise ValueError("volume must be between 0 and 100")
        self._volume = percent
        if self._config is not None:
            self._config.audio_volume = percent

    def play(self, sound_id: str) -> None:
        """Start ``sound_id`` if audio is on. Returns before the tone finishes.

        A missing native library is treated like a missing device. Programming
        errors from the output are not caught.
        """
        if not self.enabled or self.volume <= 0:
            return
        tone = resolve_tone(sound_id)
        if tone is None:
            return
        try:
            self._output.play(tone, volume=self.volume)
        except (ImportError, OSError) as error:
            if self._reported:
                return
            self._reported = True
            logger.warning("Race sound %s was not played: %s", sound_id, error)


class StartSoundPreview(QObject):
    """Plays the start-sound ids one second apart. It does not start a race.

    A volume change applies on the next tone. ``stop`` drops every tone that
    has not been started yet.
    """

    finished = Signal()

    def __init__(
        self,
        audio: RaceAudio,
        parent: QObject | None = None,
        *,
        interval_ms: int = START_SOUND_GAP_MS,
    ) -> None:
        super().__init__(parent)
        self._audio = audio
        self._index = 0
        self._running = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._play_next)

    @property
    def sound_ids(self) -> tuple[str, ...]:
        return START_SOUND_IDS

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> bool:
        """Start the sequence. A second call while it is running does nothing."""
        if self._running:
            return False
        self._running = True
        self._index = 0
        self._play_next()
        return True

    def stop(self) -> None:
        """Drop the remaining tones. A tone that already started is left to finish."""
        if not self._running:
            return
        self._timer.stop()
        self._running = False
        self.finished.emit()

    def _play_next(self) -> None:
        if not self._running:
            return
        if self._index >= len(START_SOUND_IDS):
            self._running = False
            self.finished.emit()
            return
        sound_id = START_SOUND_IDS[self._index]
        self._index += 1
        self._audio.play(sound_id)
        if self._index < len(START_SOUND_IDS):
            self._timer.start()
            return
        self._running = False
        self.finished.emit()
