"""Start cue: the race clock starts at GO, not while 3, 2 and 1 are showing."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from slot_racing.modules.races.ui.start_cue import (
    StartCue,
    StartPhase,
    uses_start_cue,
)


def test_only_a_camera_race_uses_the_start_cue() -> None:
    assert uses_start_cue("camera")
    assert not uses_start_cue("simulation")
    assert not uses_start_cue("sensor")


def test_the_race_starts_on_go_and_not_before(qtbot: QtBot) -> None:
    started: list[str] = []
    cue = StartCue(lambda: started.append("go"), interval_ms=60_000)
    cue.begin()
    assert cue.current is not None
    assert (cue.current.phase, cue.current.label) == (StartPhase.COUNTING, "3")
    assert started == []
    cue.advance()
    assert cue.current is not None and cue.current.label == "2"
    assert started == []
    cue.advance()
    assert cue.current is not None and cue.current.label == "1"
    assert cue.current.phase is StartPhase.COUNTING
    cue.advance()
    assert cue.current is not None
    assert (cue.current.phase, cue.current.label) == (StartPhase.START_SIGNAL, "GO")
    assert started == ["go"]
    cue.advance()
    assert cue.current is None
    assert started == ["go"]
