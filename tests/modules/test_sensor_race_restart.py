"""Restart laps, false starts and repeating one heat on the shared USB reader.

The bytes come from a scripted stream. No test opens a serial port, and none
uploads firmware.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import RaceId, RaceStatus, TrackId
from slot_racing.core.storage import Database
from slot_racing.modules.races.types import HeatInfo, RaceInfo
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.timing_sensor.clock_map import DeviceClockMapping, to_host_ns
from slot_racing.modules.timing_sensor.link import SensorLink
from slot_racing.modules.timing_sensor.protocol import (
    ArmCommand,
    format_arm_command,
    parse_sensor_line,
)
from slot_racing.modules.timing_sensor.transport import SerialSensorTransport
from tests.modules.conftest import Env
from tests.modules.test_free_race import _cell, _live_row
from tests.modules.test_sensor_free_race import _result_texts, _shared
from tests.modules.test_sensor_timing import _frame
from tests.modules.test_sensor_usb import _HELLO, _Stream
from tests.support.start_sequence import release_start_lights

_WAIT_NS = 30_000_000_000


def test_arm_command_is_not_an_event() -> None:
    assert format_arm_command() == "PSF/1 ARM"
    assert parse_sensor_line("PSF/1 ARM") == ArmCommand()


def test_existing_laps_gain_an_empty_heat_link() -> None:
    database = Database.in_memory()
    database.migrate("0025")
    with database.engine.connect() as connection:
        names = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(laps)")}
    assert "heat_id" not in names
    database.migrate()
    with database.engine.connect() as connection:
        info = list(connection.exec_driver_sql("PRAGMA table_info(laps)"))
    column = next(row for row in info if row[1] == "heat_id")
    assert column[3] == 0
    assert database.schema_revision() == "0026"
    database.dispose()


def test_two_crossings_count_one_lap_on_the_first_start_and_the_next(
    qtbot: QtBot, env: Env
) -> None:
    """A long wait before the first crossing does not add a start crossing."""
    page, link, transport, stream, track = _shared(qtbot, env)
    measured = _mapping(link)
    page.hide()
    QApplication.processEvents()
    race = _trial(env, TrackId(track.id))
    feed = _Feed(link, stream, transport)
    live = _open(qtbot, env, link, race.id)
    _release(live, transport, stream)
    started = _started(live)
    env.clock.advance(_WAIT_NS)
    _cross(live, feed, started + _WAIT_NS)
    assert _cell(live, 1, "Runden") == "0"
    assert _cell(live, 2, "Runden") == "0"
    _cross(live, feed, started + _WAIT_NS + 3_000_000_000)
    assert _cell(live, 1, "Runden") == "1"
    assert _cell(live, 2, "Runden") == "1"
    runner = live.runner
    assert runner is not None
    assert _live_row(runner, 1).laps_completed == 1
    assert _live_row(runner, 2).laps_completed == 1
    page.refresh()
    assert "Initialisierung: Runde begonnen, nicht gezählt" in _result_texts(page)
    _protocol_error(stream, transport, link, measured)
    live.confirm = lambda _text: True
    live.stop_race()
    assert live.heat_gate.isVisible()
    live.heat_gate.start_button.click()
    _release(live, transport, stream)
    restarted = _started(live)
    env.clock.advance(_WAIT_NS)
    _cross(live, feed, restarted + _WAIT_NS)
    assert _cell(live, 1, "Runden") == "0"
    assert _cell(live, 2, "Runden") == "0"
    _cross(live, feed, restarted + _WAIT_NS + 3_000_000_000)
    assert _cell(live, 1, "Runden") == "1"
    assert _cell(live, 2, "Runden") == "1"
    assert stream.closed is False
    assert link.mapping_origin == "measured"
    assert link._state.mapping == measured


def test_false_starts_use_the_capture_time_and_do_not_begin_a_lap(qtbot: QtBot, env: Env) -> None:
    page, link, transport, stream, track = _shared(qtbot, env)
    page.hide()
    race = _trial(env, TrackId(track.id))
    feed = _Feed(link, stream, transport)
    early = env.clock.now_ns()
    feed.push(early, "s1")
    link.poll()
    assert link.false_starts() == ()
    live = _open(qtbot, env, link, race.id)
    opened = link._window_opened_ns
    assert opened is not None
    link.bind_view((), {})
    feed.push(opened + 1_000_000, "s1")
    link.poll()
    assert link._assignments
    live.refresh()
    assert live.false_start_banner.text() == "Frühstart: Bahn 1"
    assert live.false_start_banner.isHidden() is False
    status = live.findChild(QLabel, "live-lane-1-status")
    assert status is not None
    assert status.text() == "Frühstart"
    assert status.property("falseStart") is True
    assert _cell(live, 1, "Runden") == "0"
    feed.push(opened + 2_000_000, "s2", pump=False)
    _release(live, transport, stream)
    transport.pump()
    live.refresh()
    assert live.false_start_banner.text() == "Frühstart: Bahn 1, 2"
    assert _cell(live, 1, "Runden") == "0"
    assert _cell(live, 2, "Runden") == "0"
    started = _started(live)
    _cross(live, feed, started + 1_000_000_000)
    assert _cell(live, 1, "Runden") == "0"
    _cross(live, feed, started + 4_000_000_000)
    assert _cell(live, 1, "Runden") == "1"
    assert _cell(live, 2, "Runden") == "1"
    page.refresh()
    events = page.diagnostic_payload()["events"]
    assert isinstance(events, list)
    assert any(
        isinstance(item, dict)
        and item["stage"] == "false_start"
        and item["reason"] == "before_release"
        for item in events
    )
    assert "Frühstart vor der Rennfreigabe, keine Runde" in _result_texts(page)


def test_a_crossing_at_the_release_starts_the_first_lap(qtbot: QtBot, env: Env) -> None:
    _page, link, transport, stream, track = _shared(qtbot, env)
    race = _trial(env, TrackId(track.id))
    feed = _Feed(link, stream, transport)
    live = _open(qtbot, env, link, race.id)
    _release(live, transport, stream)
    started = _started(live)
    feed.push(started, "s1")
    feed.push(started + 1_000_000_000, "s2")
    live.refresh()
    assert link.false_starts() == ()
    assert live.false_start_banner.isHidden() is True
    assert _cell(live, 1, "Runden") == "0"
    assert _cell(live, 2, "Runden") == "0"
    _cross(live, feed, started + 4_000_000_000)
    assert _cell(live, 1, "Runden") == "1"
    assert _cell(live, 2, "Runden") == "1"


def test_repeating_a_running_heat_drops_its_results_and_keeps_the_plan(
    qtbot: QtBot, env: Env
) -> None:
    _page, link, transport, stream, track = _shared(qtbot, env)
    measured = _mapping(link)
    race = _trial(env, TrackId(track.id))
    feed = _Feed(link, stream, transport)
    live = _open(qtbot, env, link, race.id)
    _release(live, transport, stream)
    _cross(live, feed, _started(live) + 1_000_000_000)
    _cross(live, feed, _started(live) + 4_000_000_000)
    assert len(env.races.list_time_measurements(race_id=race.id)) == 2
    plan = env.races.heat_plan(race.id)
    repetitions = env.races.require_race(race.id).repetitions
    asked: list[str] = []

    def confirm(text: str) -> bool:
        asked.append(text)
        return False

    live.confirm_repeat = confirm
    live.repeat_button.click()
    assert asked and asked[0].startswith("Diesen Durchlauf verwerfen")
    assert len(env.races.get_laps(race.id)) == 2
    live.confirm_repeat = lambda text: text.startswith("Diesen Durchlauf")
    assert live.repeat_button.text() == "Durchlauf wiederholen"
    assert live.repeat_button.isHidden() is False
    live.repeat_button.click()
    _assert_same_plan(env.races.heat_plan(race.id), plan, restarted=True)
    assert env.races.require_race(race.id).repetitions == repetitions
    assert env.races.get_laps(race.id) == []
    assert env.races.list_time_measurements(race_id=race.id) == []
    assert env.races.overall_bests(track_id=track.id) == []
    assert stream.closed is False
    assert link.mapping_origin == "measured"
    assert link._state.mapping == measured
    assert live.runner is not None
    assert live.runner.status is RaceStatus.CREATED
    assert live._cue is not None and live._cue.started is False
    assert live.false_start_banner.isHidden() is True
    _release(live, transport, stream)
    _cross(live, feed, _started(live) + 1_000_000_000)
    _cross(live, feed, _started(live) + 4_000_000_000)
    assert _cell(live, 1, "Runden") == "1"
    assert _cell(live, 2, "Runden") == "1"
    assert len(env.races.get_laps(race.id)) == 2
    assert len(env.races.list_time_measurements(race_id=race.id)) == 2


def test_repeating_between_heats_redoes_the_finished_heat(qtbot: QtBot, env: Env) -> None:
    _page, link, transport, stream, track = _shared(qtbot, env)
    race = _trial(env, TrackId(track.id))
    feed = _Feed(link, stream, transport)
    live = _open(qtbot, env, link, race.id)
    _release(live, transport, stream)
    _cross(live, feed, _started(live) + 1_000_000_000)
    _cross(live, feed, _started(live) + 4_000_000_000)
    live.confirm = lambda _text: True
    live.stop_race()
    plan = env.races.heat_plan(race.id)
    assert plan[0].status == "completed"
    assert live.heat_gate.isVisible()
    assert live.heat_gate.repeat_button.text() == "Durchlauf wiederholen"
    live.confirm_repeat = lambda _text: True
    live.heat_gate.repeat_button.click()
    _assert_same_plan(env.races.heat_plan(race.id), plan, restarted=True)
    assert env.races.get_laps(race.id) == []
    assert env.races.list_time_measurements(race_id=race.id) == []
    assert live.runner is not None and live.runner.status is RaceStatus.CREATED
    _release(live, transport, stream)
    _cross(live, feed, _started(live) + 1_000_000_000)
    _cross(live, feed, _started(live) + 4_000_000_000)
    assert len(env.races.get_laps(race.id)) == 2
    assert _cell(live, 1, "Runden") == "1"
    assert _cell(live, 2, "Runden") == "1"


def test_a_free_session_has_no_repeat_button(qtbot: QtBot, env: Env) -> None:
    _page, link, _transport, _stream, track = _shared(qtbot, env)
    race = env.races.create_free_race("Frei", track.id, timing_provider="sensor")
    env.races.set_free_assignments(race.id, [(1, *env.pair(1)), (2, *env.pair(2))])
    live = _open(qtbot, env, link, race.id)
    assert live.repeat_button.isVisible() is False
    assert live.heat_gate.isVisible() is False


class _Feed:
    def __init__(self, link: SensorLink, stream: _Stream, transport: SerialSensorTransport) -> None:
        self._link = link
        self._stream = stream
        self._transport = transport
        self._sequence = 1

    def push(self, host_ns: int, physical: str, *, pump: bool = True) -> None:
        mapping = _mapping(self._link)
        device = host_ns - mapping.offset_ns
        assert to_host_ns(device, mapping) == host_ns
        text = _frame(_HELLO.session_id, self._sequence, device, physical) + "\n"
        self._sequence += 1
        self._stream.push(text.encode("ascii"))
        if pump:
            self._transport.pump()


def _mapping(link: SensorLink) -> DeviceClockMapping:
    mapping = link._state.mapping
    assert mapping is not None
    assert mapping.numerator == mapping.denominator
    return mapping


def _trial(env: Env, track_id: TrackId) -> RaceInfo:
    race = env.races.create_time_trial("Sensor Durchlauf", track_id, timing_provider="sensor")
    for index in range(1, 5):
        driver, vehicle = env.pair(index)
        env.races.enroll_driver(race.id, driver, vehicle)
    env.races.plan_heats(race.id)
    assert len(env.races.heat_plan(race.id)) == 4
    return race


def _open(qtbot: QtBot, env: Env, link: SensorLink, race_id: RaceId) -> LiveRaceView:
    prepared = env.controller.prepare_race(race_id)
    live = LiveRaceView(
        env.runtime.translator,
        env.controller,
        service=env.races,
        drivers=env.drivers,
        vehicles=env.vehicles,
        start_window=lambda: link,
    )
    qtbot.addWidget(live)
    live.show()
    live.open_for_start(prepared)
    live._timer.stop()
    return live


def _release(live: LiveRaceView, transport: SerialSensorTransport, stream: _Stream) -> None:
    before = bytes(stream.outgoing).count(b"PSF/1 ARM\n")
    release_start_lights(live)
    transport.pump()
    assert bytes(stream.outgoing).count(b"PSF/1 ARM\n") == before + 1


def _started(live: LiveRaceView) -> int:
    runner = live.runner
    assert runner is not None and runner.started_at_ns is not None
    return runner.started_at_ns


def _cross(live: LiveRaceView, feed: _Feed, host_ns: int) -> None:
    feed.push(host_ns, "s1")
    feed.push(host_ns + 1_000_000_000, "s2")
    live.refresh()


def _protocol_error(
    stream: _Stream,
    transport: SerialSensorTransport,
    link: SensorLink,
    measured: DeviceClockMapping,
) -> None:
    stream.push(f"PSF/1 ERROR {_HELLO.session_id} PROTOCOL 0\n".encode())
    transport.pump()
    link.poll()
    assert link.mapping_origin == "measured"
    assert link.sync.value == "synchronized"
    assert link._state.mapping == measured


def _assert_same_plan(after: list[HeatInfo], before: list[HeatInfo], *, restarted: bool) -> None:
    assert [(item.sequence, item.seats) for item in after] == [
        (item.sequence, item.seats) for item in before
    ]
    assert after[0].status == "running"
    if restarted:
        assert [item.status for item in after[1:]] == [item.status for item in before[1:]]
