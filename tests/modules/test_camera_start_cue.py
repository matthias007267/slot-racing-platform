"""Start lights: the race clock starts when the lamps go out, not while they come on."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from slot_racing.modules.races.ui.start_cue import (
    START_LIGHT_COUNT,
    StartCue,
    StartPhase,
    uses_start_cue,
)


def test_only_a_camera_race_uses_the_start_cue() -> None:
    assert uses_start_cue("camera")
    assert not uses_start_cue("simulation")
    assert not uses_start_cue("sensor")


def test_the_race_starts_when_the_lights_go_out_and_not_before(qtbot: QtBot) -> None:
    del qtbot
    started: list[str] = []
    heard: list[str] = []
    cue = StartCue(lambda: started.append("go"), interval_ms=60_000)
    cue.changed.connect(lambda step: heard.append(step.sound_id))
    cue.begin()
    assert cue.current is not None
    assert cue.current.phase is StartPhase.COUNTING
    assert cue.current.lit_lights == 1
    assert started == []
    for lit in range(2, START_LIGHT_COUNT + 1):
        cue.advance()
        step = cue.current
        assert step is not None
        assert step.lit_lights == lit
        assert step.phase is StartPhase.COUNTING
        assert started == []
    cue.advance()
    finished = cue.current
    assert finished is not None
    assert finished.phase is StartPhase.START_SIGNAL
    assert cue.current.lit_lights == 0
    assert cue.current.sound_id == "go"
    assert started == ["go"]
    assert heard == [f"light-{lit}" for lit in range(1, START_LIGHT_COUNT + 1)] + ["go"]
    cue.advance()
    assert cue.current is None
    assert started == ["go"]


def test_stopping_the_cue_blocks_a_later_start(qtbot: QtBot) -> None:
    started: list[str] = []
    cue = StartCue(lambda: started.append("go"), interval_ms=30)
    cue.begin()
    cue.advance()
    cue.stop()
    qtbot.wait(200)
    assert started == []
    assert cue.current is None
    cue.advance()
    assert started == []
