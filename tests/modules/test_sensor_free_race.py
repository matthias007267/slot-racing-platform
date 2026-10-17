"""Free-race laps from the shared USB sensor reader, including a closed diagnostic page.

The bytes come from the scripted transport. No test opens a serial port.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from slot_racing.core.catalog import TrackInfo
from slot_racing.core.domain import TimingSensor, TimingSetup
from slot_racing.core.events import SensorTriggered
from slot_racing.core.timing import TimingSetupService, TimingSourceFactory
from slot_racing.modules.races.ui.formatting import format_lap_time
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.timing_sensor.clock_map import SyncStatus
from slot_racing.modules.timing_sensor.configuration import SensorAssignment
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.protocol import HelloFrame, format_hello
from slot_racing.modules.timing_sensor.provider import SensorTimingFactory
from slot_racing.modules.timing_sensor.store import SensorConfigurationStore
from slot_racing.modules.timing_sensor.transport import SerialSensorTransport
from slot_racing.modules.timing_sensor.ui.page import SensorTimingPage
from tests.database import migrated_database
from tests.modules.conftest import Env
from tests.modules.test_free_race import _cell, _live_row
from tests.modules.test_sensor_timing import _LOGICAL, _assignment, _frame, _translator
from tests.modules.test_sensor_timing import (
    _cell as log_cell,
)
from tests.modules.test_sensor_usb import _HELLO, _Clock, _identify, _Stream, _sync

_LANE_1 = 3_000_000_000
_LANE_1_AGAIN = 4_000_000_000
_LANE_2 = 4_900_000_000
_LANE_2_AGAIN = 6_000_000_000
_START = (
    (1, 20_000_000_000, "s1"),
    (2, 20_100_000_000, "s2"),
)
_FINISH = (
    (3, 23_000_000_000, "s1"),
    (4, 25_000_000_000, "s2"),
    (5, 27_000_000_000, "s1"),
    (6, 31_000_000_000, "s2"),
)


def test_a_free_race_counts_sensor_laps_after_the_diagnostic_page_closes(
    qtbot: QtBot, env: Env
) -> None:
    page, link, transport, stream, track = _prepared(qtbot, env)
    mapping = link._state.mapping
    session = None if link.identity is None else link.identity.session_id
    assert mapping is not None
    assert session == _HELLO.session_id
    page.hide()
    QApplication.processEvents()
    assert page.isVisible() is False
    assert page._timer.isActive() is False
    assert stream.closed is False
    assert link.connected is True
    assert link.mode is TransportMode.SERIAL
    assert link.sync is SyncStatus.SYNCHRONIZED
    assert link._state.mapping == mapping
    assert link.identity is not None and link.identity.session_id == session

    race = env.races.create_free_race("Sensor frei", track.id, timing_provider="sensor")
    env.races.set_free_assignments(race.id, [(1, *env.pair(1)), (2, *env.pair(2))])
    runner = env.controller.start_race(race.id)
    assert runner.race.timing_provider == "sensor"
    assert link.race_bound is True
    assert link.sync is SyncStatus.SYNCHRONIZED
    assert link._state.mapping == mapping
    assert link.identity is not None and link.identity.session_id == session
    assert stream.closed is False
    started = runner.started_at_ns
    assert started is not None

    seen: list[SensorTriggered] = []
    env.controller.events.subscribe(SensorTriggered, seen.append)
    live = LiveRaceView(
        env.runtime.translator,
        env.controller,
        service=env.races,
        drivers=env.drivers,
        vehicles=env.vehicles,
    )
    qtbot.addWidget(live)
    live.show_runner(runner)
    live._timer.stop()
    assert page.isVisible() is False
    assert runner.snapshot().source_errors == ()

    _push(stream, transport, _START)
    live.refresh()
    assert [event.lane for event in seen] == [1, 2]
    assert {event.sensor_id for event in seen} == {"gate"}
    assert {event.position_id for event in seen} == {"start_finish"}
    assert {event.source_id for event in seen} == {"sensor"}
    assert all(event.timestamp_ns >= started for event in seen)
    assert seen[0].timestamp_ns < seen[1].timestamp_ns
    assert seen[0].timestamp_ns != _START[0][1]
    for lane in (1, 2):
        row = _live_row(runner, lane)
        assert row.laps_completed == 0
        assert row.last_lap_ns is None
        assert row.best_lap_ns is None
        assert _cell(live, lane, "Runden") == "0"
        assert _cell(live, lane, "Letzte Runde") == format_lap_time(None)

    _push(stream, transport, _FINISH)
    live.refresh()
    assert page.isVisible() is False
    assert page._timer.isActive() is False
    assert len(seen) == 6
    assert [event.timestamp_ns for event in seen] == sorted(event.timestamp_ns for event in seen)
    lane_1 = [event for event in seen if event.lane == 1]
    lane_2 = [event for event in seen if event.lane == 2]
    assert lane_1[1].timestamp_ns - lane_1[0].timestamp_ns == _LANE_1
    assert lane_1[2].timestamp_ns - lane_1[1].timestamp_ns == _LANE_1_AGAIN
    assert lane_2[1].timestamp_ns - lane_2[0].timestamp_ns == _LANE_2
    assert lane_2[2].timestamp_ns - lane_2[1].timestamp_ns == _LANE_2_AGAIN
    for lane, best, last in ((1, _LANE_1, _LANE_1_AGAIN), (2, _LANE_2, _LANE_2_AGAIN)):
        row = _live_row(runner, lane)
        assert row.laps_completed == 2
        assert row.best_lap_ns == best
        assert row.last_lap_ns == last
        assert row.lap_times_ns == (best, last)
        assert _cell(live, lane, "Runden") == "2"
        assert _cell(live, lane, "Beste Runde") == format_lap_time(best)
        assert _cell(live, lane, "Letzte Runde") == format_lap_time(last)
    crossings = [
        item
        for item in link.observations
        if item.physical_id in {"s1", "s2"} and item.sequence is not None
    ]
    assert [item.kind for item in crossings] == ["accepted"] * 6
    assert [item.host_time_ns for item in crossings] == [event.timestamp_ns for event in seen]
    assert link._state.mapping == mapping
    assert stream.closed is False


def test_the_log_names_why_a_muted_crossing_was_not_delivered(qtbot: QtBot) -> None:
    stream = _Stream()
    transport = SerialSensorTransport("COM7", stream=stream, clock=_Clock(), sync_gap_ns=0)
    link = SensorLink(transport, mode=TransportMode.SERIAL)
    page = SensorTimingPage(
        _translator(), link, SensorConfigurationStore(migrated_database()), None, None
    )
    qtbot.addWidget(page)
    page.show()
    _identify(link, transport, stream)
    _sync(link, transport)
    link.bind_race(
        (_assignment("s1", "start_finish", 1), _assignment("s2", "start_finish", 2)),
        _LOGICAL,
        frozenset({1, 2}),
    )
    restarted = HelloFrame("b0002", "mega2560", "1.0.0", "ns", ("s1", "s2"))
    stream.session = "b0002"
    stream.push((format_hello(restarted) + "\n").encode("ascii"))
    transport.pump()
    link.poll()
    _sync(link, transport)
    stream.push((_frame("b0002", 1, 100, "s1") + "\n").encode("ascii"))
    transport.pump()
    page.refresh()
    assert link.take_events() == ()
    matched = False
    for row in range(page.log.rowCount()):
        if log_cell(page, row, 0) == "s1" and log_cell(page, row, 3) == "100":
            assert log_cell(page, row, 1) == "start_finish"
            assert log_cell(page, row, 2) == "1"
            assert log_cell(page, row, 4) != ""
            assert log_cell(page, row, 5) == ("Nicht an das Rennen übergeben: neue Gerätesitzung")
            matched = True
    assert matched is True
    link.release()


def test_hiding_the_simulation_page_still_releases_the_reader(qtbot: QtBot) -> None:
    link = SensorLink()
    page = SensorTimingPage(
        _translator(), link, SensorConfigurationStore(migrated_database()), None, None
    )
    qtbot.addWidget(page)
    page.show()
    assert link.connected is True
    assert link.mode is TransportMode.SIMULATION
    page.hide()
    assert link.connected is False


def _prepared(
    qtbot: QtBot, env: Env
) -> tuple[SensorTimingPage, SensorLink, SerialSensorTransport, _Stream, TrackInfo]:
    env.runtime.plugins.enable("timing_sensor")
    factory = env.runtime.services.get(TimingSourceFactory, "sensor")
    assert isinstance(factory, SensorTimingFactory)
    stream = _Stream()
    transport = SerialSensorTransport("COM7", stream=stream, clock=_Clock(), sync_gap_ns=0)
    link = factory._link
    link.use_transport(transport, TransportMode.SERIAL)
    track = env.track(lanes=2)
    setup = TimingSetup.from_position_ids(["start_finish"]).with_sensor(
        TimingSensor("gate", "start_finish")
    )
    env.runtime.services.get(TimingSetupService).save_setup(track.id, setup)
    factory._store.save_track(
        int(track.id),
        (
            SensorAssignment(physical_id="s1", position_id="start_finish", lane=1),
            SensorAssignment(physical_id="s2", position_id="start_finish", lane=2),
        ),
    )
    page = SensorTimingPage(
        env.runtime.translator,
        link,
        factory._store,
        env.runtime.services.get(TimingSetupService),
        env.tracks,
    )
    qtbot.addWidget(page)
    page.show()
    QApplication.processEvents()
    _identify(link, transport, stream)
    _sync(link, transport)
    assert link.sync is SyncStatus.SYNCHRONIZED
    assert link.connected is True
    return page, link, transport, stream, track


def _push(
    stream: _Stream,
    transport: SerialSensorTransport,
    lines: tuple[tuple[int, int, str], ...],
) -> None:
    for sequence, device_ns, physical in lines:
        text = _frame(_HELLO.session_id, sequence, device_ns, physical) + "\n"
        stream.push(text.encode("ascii"))
    transport.pump()
