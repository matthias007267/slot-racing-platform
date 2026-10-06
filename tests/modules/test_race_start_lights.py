"""The start lights belong to the race start, for every timing provider."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
from slot_racing.core.timing import TimingSourceFactory
from slot_racing.modules.races.runner import RaceRunner
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from tests.modules.conftest import Env
from tests.modules.test_ui_management import open_page
from tests.support.timing import FakeTimingFactory, FakeTimingSource


def _status(runner: RaceRunner) -> RaceStatus:
    return runner.status


def _page(qtbot: QtBot, env: Env) -> RacesPage:
    window, page = open_page(qtbot, env, "races")
    assert isinstance(page, RacesPage)
    window.resize(1100, 700)
    window.show()
    page.live.cue_interval_ms = 60_000
    return page


def _seat(env: Env, race_id: RaceId, lanes: int = 1) -> None:
    for lane in range(1, lanes + 1):
        driver_id, vehicle_id = env.pair(lane)
        env.races.add_participant(race_id, driver_id, vehicle_id, lane)


def _start(page: RacesPage, race_id: RaceId) -> LiveRaceView:
    assert page.start_race(race_id)
    live = page.live
    assert isinstance(page.current_view(), LiveRaceView)
    return live


def test_simulation_camera_and_a_sensor_all_wait_for_the_same_lights(
    qtbot: QtBot, env: Env
) -> None:
    camera = FakeTimingSource("camera")
    sensor = FakeTimingSource("timing_sensor")
    env.runtime.services.register(
        TimingSourceFactory,
        FakeTimingFactory("camera", source=camera),
        owner="test",
        name="camera",
    )
    env.runtime.services.register(
        TimingSourceFactory,
        FakeTimingFactory("timing_sensor", source=sensor),
        owner="test",
        name="timing_sensor",
    )
    page = _page(qtbot, env)
    track = env.track("Ring", lanes=2)
    for provider, source in (
        ("simulation", None),
        ("camera", camera),
        ("timing_sensor", sensor),
    ):
        race = env.races.create_race(provider, track.id, 3, provider)
        _seat(env, race.id)
        live = _start(page, race.id)
        runner = live.runner
        assert runner is not None
        assert runner.race.timing_provider == provider
        assert live.start_lights.isVisible()
        assert live.start_lights.lit_lights == 1
        assert runner.status is RaceStatus.CREATED
        assert runner.snapshot().elapsed_ns == 0
        if source is not None:
            assert "start" not in source.calls
        for lit in (2, 3, 4, 5):
            live.advance_start_cue()
            assert live.start_lights.lit_lights == lit
            assert runner.status is RaceStatus.CREATED
            assert runner.snapshot().elapsed_ns == 0
        live.advance_start_cue()
        assert live.start_lights.showing_go
        assert live.start_lights.lit_lights == 0
        assert _status(runner) is RaceStatus.RUNNING
        if source is not None:
            assert source.calls.count("start") == 1
        runner.stop()
        runner.close()


def test_hiding_simulation_lights_does_not_start_the_race(qtbot: QtBot, env: Env) -> None:
    page = _page(qtbot, env)
    page.live.cue_interval_ms = 40
    track = env.track("Ring")
    race = env.races.create_race("Simulation", track.id, 2)
    _seat(env, race.id)
    live = _start(page, race.id)
    live.advance_start_cue()
    assert live.start_lights.lit_lights == 2
    live.hide()
    qtbot.wait(400)
    runner = live.runner
    assert runner is not None
    assert runner.status is RaceStatus.CREATED
    assert runner.snapshot().elapsed_ns == 0
    assert not live.start_lights.isVisible()
    live.show()
    live.open_for_start(runner)
    assert live.start_lights.lit_lights == 1
    assert runner.status is RaceStatus.CREATED
    runner.close()


def test_a_restarted_simulation_race_begins_at_the_first_lamp(qtbot: QtBot, env: Env) -> None:
    page = _page(qtbot, env)
    track = env.track("Ring")
    race = env.races.create_race("Abbruch", track.id, 4)
    _seat(env, race.id)
    live = _start(page, race.id)
    for _ in range(5):
        live.advance_start_cue()
    runner = live.runner
    assert runner is not None and _status(runner) is RaceStatus.RUNNING
    runner.stop()
    assert env.races.require_race(race.id).status is RaceStatus.ABORTED
    restarted = env.races.restart_aborted(race.id)
    assert restarted.id != race.id
    live = _start(page, restarted.id)
    again = live.runner
    assert again is not None
    assert again.race.id == restarted.id
    assert live.start_lights.lit_lights == 1
    assert not live.start_lights.showing_go
    assert again.status is RaceStatus.CREATED
    assert env.races.require_race(race.id).status is RaceStatus.ABORTED
    for lit in (2, 3, 4, 5):
        live.advance_start_cue()
        assert live.start_lights.lit_lights == lit
        assert again.status is RaceStatus.CREATED
    live.advance_start_cue()
    assert live.start_lights.showing_go
    assert _status(again) is RaceStatus.RUNNING
    again.close()


def test_lap_timed_and_open_time_trials_wait_for_the_lights(qtbot: QtBot, env: Env) -> None:
    page = _page(qtbot, env)
    track = env.track("Ring", lanes=2)
    lap = env.races.create_race("Runden", track.id, 5)
    open_trial = env.races.create_time_trial("Offen", track.id)
    timed = env.races.create_time_trial("Dauer", track.id, duration_minutes=12)
    _seat(env, lap.id)
    _seat(env, open_trial.id)
    _seat(env, timed.id)
    assert timed.mode is RaceMode.TIME_TRIAL
    assert timed.duration_minutes == 12
    for race in (lap, open_trial, timed):
        live = _start(page, race.id)
        runner = live.runner
        assert runner is not None
        assert live.start_lights.lit_lights == 1
        assert runner.status is RaceStatus.CREATED
        assert runner.snapshot().elapsed_ns == 0
        for lit in (2, 3, 4, 5):
            live.advance_start_cue()
            assert live.start_lights.lit_lights == lit
            assert runner.status is RaceStatus.CREATED
            assert runner.snapshot().elapsed_ns == 0
        live.advance_start_cue()
        assert _status(runner) is RaceStatus.RUNNING
        assert runner.snapshot().elapsed_ns == 0
        if race.id == timed.id:
            env.clock.advance(1_000_000_000)
            runner.tick()
            assert _status(runner) is RaceStatus.RUNNING
        runner.stop()
        runner.close()
        live.advance_start_cue()
        assert not live.start_lights.isVisible()
