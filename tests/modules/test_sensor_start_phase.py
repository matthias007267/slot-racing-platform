"""False starts follow the start phase and the release, never the moment a line was read.

The bytes come from a scripted stream. No test opens a serial port, and none
uploads firmware.
"""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceId, TrackId
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.timing_sensor.link import SensorLink
from slot_racing.modules.timing_sensor.protocol import SensorFrame, format_sensor_frame
from slot_racing.modules.timing_sensor.ui.page import SensorTimingPage
from tests.modules.conftest import Env
from tests.modules.test_free_race import _cell, _live_row
from tests.modules.test_sensor_free_race import _shared
from tests.modules.test_sensor_race_restart import (
    _Feed,
    _mapping,
    _open,
    _release,
    _started,
    _trial,
)
from tests.modules.test_sensor_usb import _HELLO

_SECOND = 1_000_000_000
_MODES = ("free", "trial")


def _race(env: Env, track_id: int, mode: str) -> int:
    if mode == "trial":
        return int(_trial(env, TrackId(track_id)).id)
    race = env.races.create_free_race("Sensor frei", TrackId(track_id), timing_provider="sensor")
    env.races.set_free_assignments(race.id, [(1, *env.pair(1)), (2, *env.pair(2))])
    return int(race.id)


def _start(
    qtbot: QtBot, env: Env, link: SensorLink, track_id: int, mode: str
) -> tuple[LiveRaceView, int]:
    race_id = _race(env, track_id, mode)
    live = _open(qtbot, env, link, RaceId(race_id))
    return live, race_id


def _events(page: SensorTimingPage) -> list[dict[str, object]]:
    page.refresh()
    found = page.diagnostic_payload()["events"]
    assert isinstance(found, list)
    return [item for item in found if isinstance(item, dict)]


def _laps(live: LiveRaceView, lane: int) -> tuple[int, tuple[int, ...]]:
    runner = live.runner
    assert runner is not None
    row = _live_row(runner, lane)
    return row.laps_completed, row.lap_times_ns


def _run(live: LiveRaceView, feed: _Feed, lane: int, host_ns: int) -> None:
    feed.push(host_ns, f"s{lane}")
    live.refresh()


@pytest.mark.parametrize("mode", _MODES)
def test_events_before_at_and_after_the_release_are_classified_by_capture_time(
    qtbot: QtBot, env: Env, mode: str
) -> None:
    page, link, transport, stream, track = _shared(qtbot, env)
    live, _race_id = _start(qtbot, env, link, int(track.id), mode)
    feed = _Feed(link, stream, transport)
    opened = link._window_opened_ns
    assert opened is not None
    inside = env.clock.now_ns() + 1_000_000
    feed.push(inside, "s1", pump=False)
    _release(live, transport, stream)
    started = _started(live)
    assert inside < started
    feed.push(started - 1, "s2")
    feed.push(started, "s1")
    feed.push(started + 1, "s2")
    live.refresh()
    notices = {(item.lane, item.captured_ns) for item in link.false_starts()}
    assert notices == {(1, inside), (2, started - 1)}
    assert _laps(live, 1) == (0, ())
    _run(live, feed, 1, started + 3 * _SECOND)
    _run(live, feed, 2, started + 4 * _SECOND)
    assert _laps(live, 1) == (1, (3 * _SECOND,))
    assert _laps(live, 2) == (1, (4 * _SECOND - 1,))
    by_lane: dict[tuple[object, object], dict[str, object]] = {}
    for item in _events(page):
        by_lane[(item["lane"], item["converted_host_time_ns"])] = item
    at_release = by_lane[(1, started)]
    assert at_release["classification"] == "race_crossing"
    assert at_release["classification_reason"] == "at_or_after_release"
    assert at_release["delta_to_release_ns"] == 0
    assert at_release["release_ns"] == started
    assert at_release["start_phase_begin_ns"] == opened
    assert at_release["sync_status"] == "synchronized"
    just_before = by_lane[(2, started - 1)]
    assert just_before["classification"] == "false_start"
    assert just_before["classification_reason"] == "before_release"
    assert just_before["delta_to_release_ns"] == -1
    just_after = by_lane[(2, started + 1)]
    assert just_after["classification"] == "race_crossing"
    assert just_after["delta_to_release_ns"] == 1


@pytest.mark.parametrize("mode", _MODES)
def test_the_first_pass_long_after_the_release_is_no_false_start(
    qtbot: QtBot, env: Env, mode: str
) -> None:
    page, link, transport, stream, track = _shared(qtbot, env)
    live, _race_id = _start(qtbot, env, link, int(track.id), mode)
    feed = _Feed(link, stream, transport)
    _release(live, transport, stream)
    started = _started(live)
    env.clock.advance(45 * _SECOND)
    first = started + 45 * _SECOND
    _run(live, feed, 1, first)
    _run(live, feed, 2, first + 2 * _SECOND)
    assert link.false_starts() == ()
    assert live.false_start_banner.isHidden() is True
    _run(live, feed, 1, first + 5 * _SECOND)
    _run(live, feed, 2, first + 8 * _SECOND)
    assert _laps(live, 1) == (1, (5 * _SECOND,))
    assert _laps(live, 2) == (1, (6 * _SECOND,))
    for item in _events(page):
        assert item["classification"] == "race_crossing"
        assert item["classification_reason"] == "at_or_after_release"
        assert item["delta_to_release_ns"] is not None
        assert item["delta_to_release_ns"] >= 45 * _SECOND  # type: ignore[operator]


@pytest.mark.parametrize("mode", _MODES)
def test_a_delayed_read_does_not_change_the_classification(
    qtbot: QtBot, env: Env, mode: str
) -> None:
    page, link, transport, stream, track = _shared(qtbot, env)
    live, _race_id = _start(qtbot, env, link, int(track.id), mode)
    feed = _Feed(link, stream, transport)
    _release(live, transport, stream)
    started = _started(live)
    env.clock.advance(30 * _SECOND)
    feed.push(started + 2 * _SECOND, "s1", pump=False)
    feed.push(started + 5 * _SECOND, "s1", pump=False)
    transport.pump()
    live.refresh()
    assert link.false_starts() == ()
    assert _laps(live, 1) == (1, (3 * _SECOND,))
    feed.push(started - 400_000_000, "s2")
    live.refresh()
    assert [item.lane for item in link.false_starts()] == [2]
    assert _laps(live, 2) == (0, ())
    reasons = {
        (item["lane"], item["classification_reason"])
        for item in _events(page)
        if item["classification"] != "race_crossing" or item["lane"] == 2
    }
    assert reasons == {(2, "before_release")}


@pytest.mark.parametrize("mode", _MODES)
def test_a_real_false_start_does_not_cost_the_first_valid_lap_capture(
    qtbot: QtBot, env: Env, mode: str
) -> None:
    _page, link, transport, stream, track = _shared(qtbot, env)
    live, _race_id = _start(qtbot, env, link, int(track.id), mode)
    feed = _Feed(link, stream, transport)
    opened = link._window_opened_ns
    assert opened is not None
    feed.push(env.clock.now_ns() + 1_000_000, "s1")
    live.refresh()
    assert [item.lane for item in link.false_starts()] == [1]
    assert live.false_start_banner.isHidden() is False
    _release(live, transport, stream)
    started = _started(live)
    _run(live, feed, 1, started + 1_200_000_000)
    assert _laps(live, 1) == (0, ())
    _run(live, feed, 1, started + 4_200_000_000)
    assert _laps(live, 1) == (1, (3 * _SECOND,))
    assert _cell(live, 1, "Runden") == "1"
    assert [item.lane for item in link.false_starts()] == [1]


def test_a_cancelled_start_sequence_leaves_no_start_window(qtbot: QtBot, env: Env) -> None:
    page, link, transport, stream, track = _shared(qtbot, env)
    live, _race_id = _start(qtbot, env, link, int(track.id), "free")
    feed = _Feed(link, stream, transport)
    assert link.start_window_state()["active"] is True
    live._cancel_cue()
    assert link.start_window_state()["active"] is False
    assert link.start_window_state()["begin_ns"] is None
    feed.push(env.clock.now_ns() + 1_000_000, "s1")
    link.poll()
    assert link.false_starts() == ()
    event = _events(page)[-1]
    assert event["classification"] == "race_crossing"
    assert event["classification_reason"] == "no_start_sequence"


def test_a_release_of_another_race_does_not_close_the_window() -> None:
    link = SensorLink()
    link.open_start_window(7, 100)
    link.close_start_window(500, race_id=8)
    assert link.start_window_state()["release_ns"] is None
    assert link.start_window_state()["active"] is True
    link.cancel_start_window(race_id=8)
    assert link.start_window_state()["active"] is True
    link.close_start_window(500, race_id=7)
    assert link.start_window_state()["release_ns"] == 500
    link.close_start_window(900, race_id=7)
    assert link.start_window_state()["release_ns"] == 500
    link.cancel_start_window(race_id=7)
    assert link.start_window_state()["release_ns"] == 500


@pytest.mark.parametrize("mode", _MODES)
def test_a_lost_time_base_is_a_clock_error_and_not_a_false_start(
    qtbot: QtBot, env: Env, mode: str
) -> None:
    page, link, transport, stream, track = _shared(qtbot, env)
    live, _race_id = _start(qtbot, env, link, int(track.id), mode)
    feed = _Feed(link, stream, transport)
    measured = _mapping(link)
    pushed = env.clock.now_ns() + 1_000_000
    link.lose_time_mapping()
    sequence = feed._sequence
    frame = SensorFrame(_HELLO.session_id, sequence, pushed - measured.offset_ns, "s1")
    stream.push((format_sensor_frame(frame) + "\n").encode("ascii"))
    transport.pump()
    link.poll()
    assert link.false_starts() == ()
    latest = _events(page)[-1]
    assert latest["classification"] == "time_base_error"
    assert latest["classification_reason"] == "lost"
    assert latest["sync_status"] == "lost"
    assert latest["converted_host_time_ns"] is None
    assert live.false_start_banner.isHidden() is True
    assert _laps(live, 1) == (0, ())


@pytest.mark.parametrize("mode", _MODES)
def test_a_repeated_start_carries_no_old_start_phase_or_lane_state(
    qtbot: QtBot, env: Env, mode: str
) -> None:
    page, link, transport, stream, track = _shared(qtbot, env)
    live, race_id = _start(qtbot, env, link, int(track.id), mode)
    feed = _Feed(link, stream, transport)
    feed.push(env.clock.now_ns() + 1_000_000, "s2")
    live.refresh()
    assert [item.lane for item in link.false_starts()] == [2]
    _release(live, transport, stream)
    first_start = _started(live)
    _run(live, feed, 1, first_start + _SECOND)
    _run(live, feed, 1, first_start + 3 * _SECOND)
    live.confirm = lambda _text: True
    live.stop_race()
    first_window = dict(link.start_window_state())
    assert first_window["release_ns"] == first_start

    if mode == "trial":
        assert live.heat_gate.isVisible()
        live.heat_gate.start_button.click()
    else:
        env.clock.advance(5 * _SECOND)
        live = _open(qtbot, env, link, RaceId(_race(env, int(track.id), "free")))
    window = link.start_window_state()
    assert window["release_ns"] is None
    assert window["active"] is True
    assert window["false_starts"] == 0
    assert window["begin_ns"] != first_window["begin_ns"]
    assert link.false_starts() == ()
    assert live.false_start_banner.isHidden() is True
    _release(live, transport, stream)
    started = _started(live)
    assert link.start_window_state()["release_ns"] == started
    _run(live, feed, 2, started + 20 * _SECOND)
    assert link.false_starts() == ()
    _run(live, feed, 2, started + 25 * _SECOND)
    assert _laps(live, 2) == (1, (5 * _SECOND,))
    assert _laps(live, 1) == (0, ())
    assert race_id > 0
    assert [item["classification"] for item in _events(page)[-2:]] == ["race_crossing"] * 2


@pytest.mark.parametrize("mode", _MODES)
def test_a_crossing_already_in_the_reader_at_the_release_is_a_race_crossing(
    qtbot: QtBot, env: Env, mode: str
) -> None:
    page, link, transport, stream, track = _shared(qtbot, env)
    live, _race_id = _start(qtbot, env, link, int(track.id), mode)
    feed = _Feed(link, stream, transport)
    later = env.clock.now_ns() + 3_600 * _SECOND
    feed.push(later, "s1", pump=False)
    feed.push(later + 3 * _SECOND, "s1", pump=False)
    _release(live, transport, stream)
    assert link.false_starts() == ()
    live.refresh()
    assert _laps(live, 1) == (1, (3 * _SECOND,))
    assert [item["classification"] for item in _events(page)] == ["race_crossing"] * 2
