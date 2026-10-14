"""Qt playback for a start tone. Importing this module loads Qt Multimedia.

The race UI must not import this module at load time. :func:`open_tone_output`
does, and turns a missing ``libpulse`` (or any other native import failure)
into a silent player. Playback itself still returns before the buffer ends.

One tone owns the device. The next tone, and the end of a start sequence, stop
that sink and drop its buffer. Leaving every past sink attached to the output
mixes them on the next race, which is what makes the start sound harsher after
each finished race.
"""

from __future__ import annotations

import array
from contextlib import suppress

from PySide6.QtCore import QBuffer, QIODevice, QObject, Slot
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QtAudio

from slot_racing.modules.races.ui.race_audio import (
    GO_TONE,
    LAMP_TONE,
    SAMPLE_RATE_HZ,
    ToneSpec,
    render_tone,
    volume_gain,
)

# 100 % of the setting is this share of full PCM, so a tone does not clip.
_FULL_VOLUME = 0.8


class QtToneOutput(QObject):
    """Plays a tone through ``QAudioSink`` without waiting for the buffer to drain.

    ``stateChanged`` is still declared as ``QAudio::State`` while the Python enum
    lives on ``QtAudio.State``. A slot typed as that signal argument raises
    ``TypeError`` on PySide 6.11 when the device emits the state. The slot
    therefore takes no argument and reads ``sink.state()`` itself.
    """

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._format = QAudioFormat()
        self._format.setSampleRate(SAMPLE_RATE_HZ)
        self._format.setChannelCount(1)
        self._format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._full_scale = {LAMP_TONE: render_tone(LAMP_TONE), GO_TONE: render_tone(GO_TONE)}
        self._live: list[tuple[QAudioSink, QBuffer]] = []
        self._dropping: set[QAudioSink] = set()

    @property
    def retained_count(self) -> int:
        """Sinks still held. A new tone replaces the previous one, so this stays at most one."""
        return len(self._live)

    def play(self, tone: ToneSpec, *, volume: int) -> None:
        device = QMediaDevices.defaultAudioOutput()
        if device is None or device.isNull() or not device.isFormatSupported(self._format):
            return
        self.release()
        pcm = self._full_scale.get(tone)
        if pcm is None:
            pcm = render_tone(tone)
        buffer = QBuffer(self)
        buffer.setData(_scale(pcm, volume))
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            buffer.deleteLater()
            return
        sink = QAudioSink(device, self._format, self)
        self._live.append((sink, buffer))
        sink.stateChanged.connect(self._on_sink_state)
        sink.start(buffer)

    def release(self) -> None:
        """Stop the current tone and drop its sink and buffer. A second call does nothing."""
        for item in tuple(self._live):
            self._drop(item)

    @Slot()
    def _on_sink_state(self) -> None:
        sender = self.sender()
        if not isinstance(sender, QAudioSink):
            return
        finished = (QtAudio.State.IdleState, QtAudio.State.StoppedState)
        if sender.state() not in finished:
            return
        for item in tuple(self._live):
            if item[0] is sender:
                self._drop(item)
                return

    def _drop(self, item: tuple[QAudioSink, QBuffer]) -> None:
        sink, buffer = item
        if sink in self._dropping:
            return
        if item in self._live:
            self._live.remove(item)
        self._dropping.add(sink)
        with suppress(RuntimeError):
            sink.stateChanged.disconnect(self._on_sink_state)
        sink.stop()
        if buffer.isOpen():
            buffer.close()
        sink.deleteLater()
        buffer.deleteLater()
        self._dropping.discard(sink)


def _scale(pcm: bytes, volume: int) -> bytes:
    gain = volume_gain(volume) * _FULL_VOLUME
    source = array.array("h")
    source.frombytes(pcm)
    scaled = array.array("h", (int(sample * gain) for sample in source))
    return scaled.tobytes()
