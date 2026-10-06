"""Start tones follow the cue and never decide when the race starts."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from itertools import pairwise

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceStatus
from slot_racing.modules.races.runner import RaceRunner
from slot_racing.modules.races.ui.race_audio import (
    GO_TONE,
    LAMP_TONE,
    RaceAudio,
    SilentToneOutput,
    ToneSpec,
    open_tone_output,
    render_tone,
)
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.start_cue import START_LIGHT_COUNT, StartCue
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page


class RecordingOutput:
    """Remembers tones. ``error`` makes ``play`` fail the way a missing device can."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.calls: list[tuple[ToneSpec, int]] = []
        self.error = error

    def play(self, tone: ToneSpec, *, volume: int) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append((tone, volume))


def test_go_tone_is_higher_and_longer_than_the_lamp_tone() -> None:
    lamp = render_tone(LAMP_TONE)
    go = render_tone(GO_TONE)
    assert len(go) > len(lamp) * 2
    assert _rate(go) > _rate(lamp) * 1.5


def test_each_lamp_plays_one_tone_and_go_plays_once() -> None:
    output = RecordingOutput()
    audio = RaceAudio(output)
    started: list[str] = []
    cue = StartCue(lambda: started.append("go"), interval_ms=60_000)
    cue.changed.connect(lambda step: audio.play(step.sound_id))
    cue.begin()
    assert [tone for tone, _volume in output.calls] == [LAMP_TONE]
    assert started == []
    for _ in range(START_LIGHT_COUNT - 1):
        cue.advance()
    assert [tone for tone, _volume in output.calls] == [LAMP_TONE] * 5
    assert started == []
    began = time.perf_counter()
    cue.advance()
    elapsed = time.perf_counter() - began
    assert elapsed < 0.05
    assert [tone for tone, _volume in output.calls] == [LAMP_TONE] * 5 + [GO_TONE]
    assert started == ["go"]
    cue.advance()
    assert started == ["go"]
    assert output.calls[-1][0] is GO_TONE


def test_disabled_audio_and_a_missing_library_still_start_the_race() -> None:
    missing = ImportError(
        "libpulse.so.0: cannot open shared object file: No such file or directory"
    )
    cases = (
        (RecordingOutput(), False),
        (RecordingOutput(missing), True),
        (RecordingOutput(OSError(str(missing))), True),
    )
    for output, enabled in cases:
        audio = RaceAudio(output)
        audio.enabled = enabled
        started: list[str] = []
        cue = StartCue(_remember(started), interval_ms=60_000)
        cue.changed.connect(lambda step, player=audio: player.play(step.sound_id))
        cue.begin()
        for _ in range(START_LIGHT_COUNT):
            cue.advance()
        assert started == ["go"]
        assert output.calls == []


def test_a_programming_error_in_the_output_is_not_hidden() -> None:
    audio = RaceAudio(RecordingOutput(RuntimeError("bug")))
    with pytest.raises(RuntimeError, match="bug"):
        audio.play("light-1")


def test_stopping_the_cue_does_not_play_go_later(qtbot: QtBot) -> None:
    output = RecordingOutput()
    audio = RaceAudio(output)
    started: list[str] = []
    cue = StartCue(lambda: started.append("go"), interval_ms=30)
    cue.changed.connect(lambda step: audio.play(step.sound_id))
    cue.begin()
    cue.advance()
    cue.stop()
    qtbot.wait(200)
    cue.advance()
    assert started == []
    assert GO_TONE not in [tone for tone, _volume in output.calls]
    assert output.calls == [(LAMP_TONE, 80), (LAMP_TONE, 80)]


def test_playback_returns_immediately_with_or_without_qt(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    output = open_tone_output(host)
    began = time.perf_counter()
    output.play(LAMP_TONE, volume=80)
    output.play(GO_TONE, volume=80)
    assert time.perf_counter() - began < 0.05
    try:
        from slot_racing.modules.races.ui.qt_tone_output import QtToneOutput
    except (ImportError, OSError):
        assert isinstance(output, SilentToneOutput)
    else:
        assert isinstance(output, QtToneOutput)


def test_the_race_ui_imports_when_qt_multimedia_is_missing() -> None:
    script = """
message = "libpulse.so.0: cannot open shared object file: No such file or directory"
real_import = __import__

def guard(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "PySide6.QtMultimedia" or (
        name == "PySide6" and fromlist and "QtMultimedia" in fromlist
    ):
        raise ImportError(message)
    return real_import(name, globals, locals, fromlist, level)

import builtins
builtins.__import__ = guard

from PySide6.QtWidgets import QApplication
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.race_audio import RaceAudio, SilentToneOutput
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.races.ui.start_cue import START_LIGHT_COUNT, StartCue

app = QApplication([])
assert LiveRaceView is not None and RacesPage is not None
audio = RaceAudio()
assert isinstance(audio.output, SilentToneOutput)
started = []

def remember():
    started.append("go")

cue = StartCue(remember, interval_ms=60_000)
cue.changed.connect(lambda step: audio.play(step.sound_id))
cue.begin()
for _ in range(START_LIGHT_COUNT):
    cue.advance()
assert started == ["go"]
audio.play("light-1")
audio.play("go")
del app
"""
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_volume_is_one_setting_on_the_player() -> None:
    output = RecordingOutput()
    audio = RaceAudio(output)
    audio.set_volume(40)
    audio.play("light-3")
    audio.play("go")
    audio.play("best-lap")
    assert output.calls == [(LAMP_TONE, 40), (GO_TONE, 40)]
    audio.set_volume(0)
    audio.play("light-1")
    assert output.calls == [(LAMP_TONE, 40), (GO_TONE, 40)]


def test_the_live_view_plays_tones_and_still_starts_on_lights_out(qtbot: QtBot, env: Env) -> None:
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    window.show()
    output = RecordingOutput()
    page.live.audio = RaceAudio(output)
    page.live.cue_interval_ms = 60_000
    track = env.track("Ring")
    race = env.races.create_race("Finale", track.id, 3)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    assert page.start_race(race.id)
    live = page.live
    runner = live.runner
    assert runner is not None
    assert _status(runner) is RaceStatus.CREATED
    assert runner.snapshot().elapsed_ns == 0
    assert [tone for tone, _volume in output.calls] == [LAMP_TONE]
    for _ in range(4):
        live.advance_start_cue()
        assert _status(runner) is RaceStatus.CREATED
        assert runner.snapshot().elapsed_ns == 0
    live.advance_start_cue()
    assert _status(runner) is RaceStatus.RUNNING
    assert [tone for tone, _volume in output.calls] == [LAMP_TONE] * 5 + [GO_TONE]


def _status(runner: RaceRunner) -> RaceStatus:
    return runner.status


def _remember(started: list[str]) -> Callable[[], None]:
    def start() -> None:
        started.append("go")

    return start


def _rate(pcm: bytes) -> float:
    samples = memoryview(pcm).cast("h")
    crossings = sum(1 for previous, current in pairwise(samples) if previous < 0 <= current)
    return crossings / (len(samples) / 22_050)
