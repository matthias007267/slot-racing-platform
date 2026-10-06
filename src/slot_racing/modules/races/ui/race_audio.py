"""Presentation sounds for a race. Playback never decides when the race starts.

``StartCue`` publishes a ``sound_id`` with each step. :class:`RaceAudio` maps that
id onto a tone and asks an output to start it. The call returns immediately.
A missing device or a failed output is ignored, so the cue's own timer and
``_commit_start`` stay the only start clock.

Further cues (race end, a new best lap, the last lap) can be added in
:func:`resolve_tone`. They are not played today.
"""

from __future__ import annotations

import array
import logging
import math
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QBuffer, QIODevice, QObject
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QtAudio

logger = logging.getLogger(__name__)

SAMPLE_RATE_HZ = 22_050

# One place for the start tones. Lamp beeps are the same short low tone.
# GO is an octave higher and longer, so the start is distinct.
LAMP_TONE_HZ = 349
LAMP_TONE_MS = 110
GO_TONE_HZ = 698
GO_TONE_MS = 260


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


class QtToneOutput:
    """Plays a tone through ``QAudioSink`` without waiting for the buffer to drain."""

    def __init__(self, parent: QObject) -> None:
        self._parent = parent
        self._format = QAudioFormat()
        self._format.setSampleRate(SAMPLE_RATE_HZ)
        self._format.setChannelCount(1)
        self._format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._full_scale = {LAMP_TONE: render_tone(LAMP_TONE), GO_TONE: render_tone(GO_TONE)}
        self._live: list[tuple[QAudioSink, QBuffer]] = []

    def play(self, tone: ToneSpec, *, volume: int) -> None:
        device = QMediaDevices.defaultAudioOutput()
        if device is None or device.isNull() or not device.isFormatSupported(self._format):
            return
        pcm = self._full_scale.get(tone)
        if pcm is None:
            pcm = render_tone(tone)
        buffer = QBuffer(self._parent)
        buffer.setData(_scale(pcm, volume))
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            return
        sink = QAudioSink(device, self._format, self._parent)
        self._live.append((sink, buffer))
        sink.stateChanged.connect(lambda _state, item=(sink, buffer): self._release(item))
        sink.start(buffer)

    def _release(self, item: tuple[QAudioSink, QBuffer]) -> None:
        state = item[0].state()
        finished = (QtAudio.State.IdleState, QtAudio.State.StoppedState)
        if state in finished and item in self._live:
            self._live.remove(item)


def _scale(pcm: bytes, volume: int) -> bytes:
    gain = max(0.0, min(volume, 100) / 100) * 0.8
    source = array.array("h")
    source.frombytes(pcm)
    scaled = array.array("h", (int(sample * gain) for sample in source))
    return scaled.tobytes()


class RaceAudio(QObject):
    """Maps a start-cue id to a tone. Disabled audio and output errors stay here."""

    def __init__(self, output: ToneOutput | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._output = QtToneOutput(self) if output is None else output
        self._enabled = True
        self._volume = 80
        self._reported = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    @property
    def volume(self) -> int:
        """Playback level from 0 to 100. The widget does not keep its own copy."""
        return self._volume

    def set_volume(self, percent: int) -> None:
        if percent < 0 or percent > 100:
            raise ValueError("volume must be between 0 and 100")
        self._volume = percent

    def play(self, sound_id: str) -> None:
        """Start ``sound_id`` if audio is on. Returns before the tone finishes."""
        if not self._enabled or self._volume <= 0:
            return
        tone = resolve_tone(sound_id)
        if tone is None:
            return
        try:
            self._output.play(tone, volume=self._volume)
        except Exception:
            if not self._reported:
                self._reported = True
                logger.warning("Race sound %s was not played", sound_id, exc_info=True)
