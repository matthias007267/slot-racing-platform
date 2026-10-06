"""Finish the start lights in tests that score a race after it is already running."""

from __future__ import annotations

from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.start_cue import START_LIGHT_COUNT


def release_start_lights(live: LiveRaceView) -> None:
    """Walk the gantry to lights-out so the engine is running.

    The lights-out step is still drawn. One further step is the existing
    end of the sequence and takes the gantry off the live view.
    """
    for _ in range(START_LIGHT_COUNT):
        runner = live.runner
        if runner is not None and runner.is_active:
            break
        live.advance_start_cue()
    runner = live.runner
    if runner is None or not runner.is_active:
        raise AssertionError("the start lights did not start the race")
    if not live.start_lights.isHidden():
        live.advance_start_cue()
