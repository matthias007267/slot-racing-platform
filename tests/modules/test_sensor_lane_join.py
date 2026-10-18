"""A lane joins a running sensor session at any time, and the start window stays out of its way.

The bytes come from a scripted stream. No test opens a serial port, and none
uploads firmware.
"""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceStatus, TrackId
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.timing_sensor.link import SensorLink
from tests.modules.conftest import Env
from tests.modules.test_free_race import _live_row
from tests.modules.test_sensor_free_race import _shared
from tests.modules.test_sensor_race_restart import (
    _Feed,
    _mapping,
    _open,
    _release,
    _started,
)
from tests.modules.test_sensor_usb import _Stream

_SECOND = 1_000_000_000


def _free(env: Env, track_id: int, lanes: tuple[int, ...]) -> RaceInfo:
    race = env.races.create_free_race("Sensor frei", TrackId(track_id), timing_provider="sensor")
    env.races.set_free_assignments(race.id, [(lane, *env.pair(lane)) for lane in lanes])
    return race


def _run(live: LiveRaceView, feed: _Feed, lane: int, host_ns: int) -> None:
    feed.push(host_ns, f"s{lane}")
    live.refresh()


def _laps(live: LiveRaceView, lane: int) -> tuple[int, tuple[int, ...]]:
    runner = live.runner
    assert runner is not None
    row = _live_row(runner, lane)
    return row.laps_completed, row.lap_times_ns


def _drive_lane_one(live: LiveRaceView, feed: _Feed, first_ns: int) -> tuple[int, ...]:
    """Four crossings, three laps of 3, 4 and 5 seconds."""
    stamps = (first_ns, first_ns + 3 * _SECOND, first_ns + 7 * _SECOND, first_ns + 12 * _SECOND)
    for stamp in stamps:
        _run(live, feed, 1, stamp)
    return stamps


def test_a_lane_that_is_assigned_from_the_start_but_unused_joins_later(
    qtbot: QtBot, env: Env
) -> None:
    _page, link, transport, stream, track = _shared(qtbot, env)
    measured = _mapping(link)
    race = _free(env, int(track.id), (1, 2))
    feed = _Feed(link, stream, transport)
    live = _open(qtbot, env, link, race.id)
    _release(live, transport, stream)
    started = _started(live)
    _drive_lane_one(live, feed, started + 2 * _SECOND)
    assert _laps(live, 1) == (3, (3 * _SECOND, 4 * _SECOND, 5 * _SECOND))
    assert _laps(live, 2) == (0, ())
    before = _laps(live, 1)

    late = started + 90 * _SECOND
    env.clock.set(late)
    _run(live, feed, 2, late)
    assert _laps(live, 2) == (0, ())
    _run(live, feed, 2, late + 6 * _SECOND)
    assert _laps(live, 2) == (1, (6 * _SECOND,))
    assert _laps(live, 1) == before
    assert link.false_starts() == ()
    _assert_link_kept(link, stream, measured)


def test_an_empty_lane_that_is_occupied_later_joins_without_touching_lane_one(
    qtbot: QtBot, env: Env
) -> None:
    _page, link, transport, stream, track = _shared(qtbot, env)
    measured = _mapping(link)
    race = _free(env, int(track.id), (1,))
    feed = _Feed(link, stream, transport)
    live = _open(qtbot, env, link, race.id)
    _release(live, transport, stream)
    started = _started(live)
    _drive_lane_one(live, feed, started + 2 * _SECOND)
    before = _laps(live, 1)
    assert before == (3, (3 * _SECOND, 4 * _SECOND, 5 * _SECOND))
    runner = live.runner
    assert runner is not None

    late = started + 60 * _SECOND
    env.clock.set(late)
    assert env.controller.switch_lane(2, *env.pair(2)) is True
    assert _laps(live, 1) == before
    assert runner.status is RaceStatus.RUNNING
    _run(live, feed, 2, late + _SECOND)
    assert _laps(live, 2) == (0, ())
    _run(live, feed, 2, late + 5 * _SECOND)
    assert _laps(live, 2) == (1, (4 * _SECOND,))
    _run(live, feed, 2, late + 11 * _SECOND)
    assert _laps(live, 2) == (2, (4 * _SECOND, 6 * _SECOND))
    assert _laps(live, 1) == before
    assert link.false_starts() == ()
    _assert_link_kept(link, stream, measured)


def test_a_crossing_captured_before_the_assignment_does_not_start_the_new_lane(
    qtbot: QtBot, env: Env
) -> None:
    _page, link, transport, stream, track = _shared(qtbot, env)
    race = _free(env, int(track.id), (1,))
    feed = _Feed(link, stream, transport)
    live = _open(qtbot, env, link, race.id)
    _release(live, transport, stream)
    started = _started(live)
    _drive_lane_one(live, feed, started + 2 * _SECOND)

    late = started + 60 * _SECOND
    env.clock.set(late)
    feed.push(late - _SECOND, "s2", pump=False)
    assert env.controller.switch_lane(2, *env.pair(2)) is True
    transport.pump()
    live.refresh()
    assert _laps(live, 2) == (0, ())
    _run(live, feed, 2, late + _SECOND)
    _run(live, feed, 2, late + 4 * _SECOND)
    assert _laps(live, 2) == (1, (3 * _SECOND,))


def test_a_lane_that_is_replaced_later_keeps_the_other_lane_running(qtbot: QtBot, env: Env) -> None:
    _page, link, transport, stream, track = _shared(qtbot, env)
    race = _free(env, int(track.id), (1, 2))
    feed = _Feed(link, stream, transport)
    live = _open(qtbot, env, link, race.id)
    _release(live, transport, stream)
    started = _started(live)
    _drive_lane_one(live, feed, started + 2 * _SECOND)
    _run(live, feed, 2, started + 3 * _SECOND)
    _run(live, feed, 2, started + 8 * _SECOND)
    assert _laps(live, 2) == (1, (5 * _SECOND,))
    lane_one = _laps(live, 1)

    late = started + 40 * _SECOND
    env.clock.set(late)
    assert env.controller.switch_lane(2, *env.pair(3)) is True
    assert _laps(live, 1) == lane_one
    assert _laps(live, 2) == (0, ())
    _run(live, feed, 2, late + _SECOND)
    _run(live, feed, 2, late + 4 * _SECOND)
    assert _laps(live, 2) == (1, (3 * _SECOND,))
    assert _laps(live, 1) == lane_one


def _assert_link_kept(link: SensorLink, stream: _Stream, measured: object) -> None:
    assert stream.closed is False
    assert link.connected is True
    assert link.mapping_origin == "measured"
    assert link.sync.value == "synchronized"
    assert link._state.mapping == measured
