"""Qt playback for a start tone. Importing this module loads Qt Multimedia.

The race UI must not import this module at load time. :func:`open_tone_output`
does, and turns a missing ``libpulse`` (or any other native import failure)
into a silent player. Playback itself still returns before the buffer ends.
"""

from __future__ import annotations

import array

from PySide6.QtCore import QBuffer, QIODevice, QObject
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QtAudio

from slot_racing.modules.races.ui.race_audio import (
    GO_TONE,
    LAMP_TONE,
    SAMPLE_RATE_HZ,
    ToneSpec,
    render_tone,
)


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
