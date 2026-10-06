"""Start tones follow the cue and never decide when the race starts."""

from __future__ import annotations

import time
from collections.abc import Callable
from itertools import pairwise

from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceStatus
from slot_racing.modules.races.runner import RaceRunner
from slot_racing.modules.races.ui.race_audio import (
    GO_TONE,
    LAMP_TONE,
    QtToneOutput,
    RaceAudio,
    ToneSpec,
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


def test_disabled_audio_and_a_broken_output_still_start_the_race() -> None:
    cases = (
        (RecordingOutput(), False),
        (RecordingOutput(RuntimeError("no device")), True),
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
        if not enabled:
            assert output.calls == []


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


def test_the_qt_output_returns_without_waiting_for_playback(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    output = QtToneOutput(host)
    began = time.perf_counter()
    output.play(LAMP_TONE, volume=80)
    output.play(GO_TONE, volume=80)
    assert time.perf_counter() - began < 0.05


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
