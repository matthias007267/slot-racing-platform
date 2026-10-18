"""Free-race laps from the shared USB sensor reader, including a closed diagnostic page.

The bytes come from the scripted transport. No test opens a serial port.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication, QGroupBox
from pytestqt.qtbot import QtBot

from slot_racing.core.catalog import TrackInfo
from slot_racing.core.domain import TimingSensor, TimingSetup, default_timing_setup
from slot_racing.core.events import SensorTriggered
from slot_racing.core.timing import TimingSetupService, TimingSourceFactory
from slot_racing.modules.races.runner import RaceRunner
from slot_racing.modules.races.ui.formatting import format_lap_time
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
from slot_racing.modules.timing_sensor.clock_map import SyncStatus
from slot_racing.modules.timing_sensor.configuration import DeviceHint, SensorAssignment
from slot_racing.modules.timing_sensor.devices import SerialPortFacts
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.protocol import HelloFrame, format_hello
from slot_racing.modules.timing_sensor.provider import SensorTimingFactory
from slot_racing.modules.timing_sensor.store import SensorConfigurationStore
from slot_racing.modules.timing_sensor.transport import SerialSensorTransport
from slot_racing.modules.timing_sensor.ui.page import SensorTimingPage
from tests.database import migrated_database
from tests.modules.conftest import Env
from tests.modules.test_free_race import _cell, _live_row
from tests.modules.test_sensor_timing import _LOGICAL, _assignment, _click, _frame, _translator
from tests.modules.test_sensor_timing import (
    _cell as log_cell,
)
from tests.modules.test_sensor_usb import _HELLO, _Clock, _identify, _Stream, _sync
from tests.modules.test_ui_management import open_page
from tests.modules.test_ui_provider import items

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
    assert page._timer.isActive() is True
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
    assert page._timer.isActive() is True
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


def test_free_and_time_trial_count_laps_on_the_shared_sensor_link(
    qtbot: QtBot, env: Env, tmp_path: Path
) -> None:
    """Both race modes see the same measured clock after the diagnostic page closes.

    The stored timing layout still has sectors. The sensors only watch start/finish,
    so those sectors must not be required. A developer offset is stored and is not
    the race clock. One crossing before the start is rejected and still listed.
    """
    page, link, transport, stream, track = _shared(qtbot, env)
    measured = link._state.mapping
    assert measured is not None and measured.offset_ns != 0
    page.offset_edit.setText("50")
    _click(page, "sensor-map-apply")
    assert link._state.mapping == measured
    assert link.mapping_origin == "measured"
    assert env.runtime.services.get(TimingSourceFactory, "sensor") is not None
    stored = page._store.load()
    assert stored.developer_offset_ns == 50
    assert stored.developer_expanded is True
    page.hide()
    QApplication.processEvents()
    assert page.isVisible() is False
    assert page._timer.isActive() is True
    assert stream.closed is False

    race = env.races.create_free_race("Sensor frei", track.id, timing_provider="sensor")
    env.races.set_free_assignments(race.id, [(1, *env.pair(1)), (2, *env.pair(2))])
    runner = env.controller.start_race(race.id)
    live = _live(qtbot, env, runner)
    started = runner.started_at_ns
    assert started is not None
    assert link.race_bound is True
    baseline = stream.device_ns
    seen: list[SensorTriggered] = []
    env.controller.events.subscribe(SensorTriggered, seen.append)
    _push(stream, transport, ((1, 1_000_000, "s1"),))
    live.refresh()
    assert seen[0].timestamp_ns < started
    assert _live_row(runner, 1).laps_completed == 0
    assert _live_row(runner, 2).laps_completed == 0

    start = (
        (2, baseline + 1_000_000_000, "s1"),
        (3, baseline + 1_100_000_000, "s2"),
    )
    finish = (
        (4, baseline + 4_000_000_000, "s1"),
        (5, baseline + 6_000_000_000, "s2"),
        (6, baseline + 8_000_000_000, "s1"),
        (7, baseline + 12_000_000_000, "s2"),
    )
    _push(stream, transport, start)
    live.refresh()
    assert _cell(live, 1, "Runden") == "0"
    assert _cell(live, 2, "Runden") == "0"
    _push(stream, transport, finish)
    live.refresh()
    lane_1 = 3_000_000_000
    lane_1_again = 4_000_000_000
    lane_2 = 4_900_000_000
    lane_2_again = 6_000_000_000
    for lane, best, last in ((1, lane_1, lane_1_again), (2, lane_2, lane_2_again)):
        row = _live_row(runner, lane)
        assert row.laps_completed == 2
        assert row.best_lap_ns == best
        assert row.last_lap_ns == last
        assert _cell(live, lane, "Runden") == "2"
        assert _cell(live, lane, "Beste Runde") == format_lap_time(best)
        assert _cell(live, lane, "Letzte Runde") == format_lap_time(last)
    assert runner.snapshot().source_errors == ()
    assert link._state.mapping == measured
    runner.stop()
    assert link.race_bound is False
    assert stream.closed is False

    trial = env.races.create_time_trial("Sensor Zeit", track.id, timing_provider="sensor")
    first_driver, first_vehicle = env.pair(3)
    second_driver, second_vehicle = env.pair(4)
    env.races.add_participant(trial.id, first_driver, first_vehicle, 1)
    env.races.add_participant(trial.id, second_driver, second_vehicle, 2)
    trial_runner = env.controller.start_race(trial.id)
    trial_live = _live(qtbot, env, trial_runner)
    trial_started = trial_runner.started_at_ns
    assert trial_started is not None
    assert link.race_bound is True
    assert link.mode is TransportMode.SERIAL
    _push(
        stream,
        transport,
        (
            (8, baseline + 20_000_000_000, "s1"),
            (9, baseline + 21_000_000_000, "s2"),
        ),
    )
    trial_live.refresh()
    assert _cell(trial_live, 1, "Runden") == "0"
    _push(
        stream,
        transport,
        (
            (10, baseline + 23_000_000_000, "s1"),
            (11, baseline + 26_000_000_000, "s2"),
        ),
    )
    trial_live.refresh()
    assert _live_row(trial_runner, 1).laps_completed == 1
    assert _live_row(trial_runner, 1).last_lap_ns == 3_000_000_000
    assert _live_row(trial_runner, 2).laps_completed == 1
    assert _live_row(trial_runner, 2).last_lap_ns == 5_000_000_000
    assert _cell(trial_live, 1, "Runden") == "1"
    assert _cell(trial_live, 1, "Letzte Runde") == format_lap_time(3_000_000_000)
    assert _cell(trial_live, 2, "Letzte Runde") == format_lap_time(5_000_000_000)
    assert trial_runner.snapshot().source_errors == ()
    assert page.isVisible() is False
    assert stream.closed is False
    assert link._state.mapping == measured

    page.refresh()
    texts = [log_cell(page, row, 5) for row in range(page.log.rowCount())]
    assert "Übernommen" not in texts
    assert "Von der Engine abgelehnt: Zeit vor dem Rennstart" in texts
    assert "Runde abgeschlossen" in texts
    payload = page.diagnostic_payload()
    target = tmp_path / "sensor-diagnostic.json"
    page.write_diagnostic(target)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written == payload
    encoded = json.dumps(payload)
    assert "Driver" not in encoded
    assert "Porsche" not in encoded
    assert payload["protocol"] == "PSF/1"
    firmware = payload["firmware"]
    assert isinstance(firmware, dict)
    assert firmware["version"] == "1.0.0"
    assert firmware["device"] == "mega2560"
    timing = payload["timing"]
    assert isinstance(timing, dict)
    assert timing["positions"] == ["start_finish", "sector_1", "sector_2"]
    assignments = timing["assignments"]
    assert isinstance(assignments, list)
    assert {item["position_id"] for item in assignments} == {"start_finish"}
    race_info = payload["race"]
    assert isinstance(race_info, dict)
    assert race_info["mode"] == "time_trial"
    assert race_info["provider"] == "sensor"
    assert race_info["state"] == "running"
    events = payload["events"]
    assert isinstance(events, list)
    assert any(
        item["stage"] == "engine"
        and item["reason"] == "invalid_timestamp"
        and isinstance(item["race_relative_ns"], int)
        and item["race_relative_ns"] < 0
        for item in events
    )
    assert any(item["stage"] == "lap" and item["reason"] == "completed" for item in events)
    changes = payload["changes"]
    assert isinstance(changes, list)
    assert any(
        item["kind"] == "race_started" and item["detail"] == "free sensor" for item in changes
    )
    assert any(
        item["kind"] == "race_started" and item["detail"] == "time_trial sensor" for item in changes
    )
    trial_runner.stop()


def test_a_sector_sensor_is_not_treated_as_the_finish_line(qtbot: QtBot, env: Env) -> None:
    page, _link, transport, stream, track = _shared(qtbot, env)
    page.hide()
    QApplication.processEvents()
    link_store = page._store
    link_store.save_track(
        int(track.id),
        (
            SensorAssignment(physical_id="s1", position_id="start_finish", lane=1),
            SensorAssignment(physical_id="s2", position_id="sector_1", lane=1),
        ),
    )
    race = env.races.create_free_race("Sektor", track.id, timing_provider="sensor")
    env.races.set_free_assignments(race.id, [(1, *env.pair(1))])
    runner = env.controller.start_race(race.id)
    live = _live(qtbot, env, runner)
    baseline = stream.device_ns
    _push(stream, transport, ((1, baseline + 2_000_000_000, "s1"),))
    live.refresh()
    assert _live_row(runner, 1).laps_completed == 0
    page.refresh()
    assert "Von der Engine abgelehnt: falscher Messpunkt" in _result_texts(page)
    _push(
        stream,
        transport,
        (
            (2, baseline + 3_000_000_000, "s2"),
            (3, baseline + 5_000_000_000, "s1"),
        ),
    )
    live.refresh()
    row = _live_row(runner, 1)
    assert row.laps_completed == 1
    started = runner.started_at_ns
    assert started is not None
    assert row.last_lap_ns is not None and row.last_lap_ns > 0
    assert "Runde abgeschlossen" in _result_texts(page)
    assert page.isVisible() is False
    runner.stop()


def test_a_synced_sensor_can_be_chosen_for_a_new_race(qtbot: QtBot, env: Env) -> None:
    page, link, _transport, stream, track = _shared(qtbot, env)
    page.hide()
    QApplication.processEvents()
    assert link.mapping_origin == "measured"
    assert stream.closed is False
    _window, races = open_page(qtbot, env, "races")
    assert isinstance(races, RacesPage)
    races.new_race()
    wizard = races.wizard
    wizard.name_edit.setText("Sensorrennen")
    assert wizard.go_next()
    assert wizard.track_combo.findData(track.id) >= 0
    wizard.track_combo.setCurrentIndex(wizard.track_combo.findData(track.id))
    sensor = next(item for item in items(wizard.provider_combo) if item[1] == "sensor")
    assert sensor == ("Sensor", "sensor", True)
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("sensor"))
    assert wizard.provider_status.text() == "Status: verfügbar"
    assert wizard.go_next()


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


def _shared(
    qtbot: QtBot, env: Env
) -> tuple[SensorTimingPage, SensorLink, SerialSensorTransport, _Stream, TrackInfo]:
    env.clock.set(100_000_000_000_000)
    env.runtime.plugins.enable("timing_sensor")
    factory = env.runtime.services.get(TimingSourceFactory, "sensor")
    assert isinstance(factory, SensorTimingFactory)
    stream = _Stream()
    transport = SerialSensorTransport("COM11", stream=stream, clock=env.clock.now_ns, sync_gap_ns=0)
    track = env.track(lanes=2)
    env.runtime.services.get(TimingSetupService).save_setup(track.id, default_timing_setup())
    factory._store.save_preferences(
        mode="serial",
        port="COM4",
        track_id=int(track.id),
        developer_expanded=True,
        developer_offset_ns=0,
        developer_numerator=1,
        developer_denominator=1,
        device=DeviceHint(
            port="COM4",
            vid=0x2341,
            pid=0x0042,
            serial_number="BOARD1",
            location="1-1.2",
        ),
    )
    factory._store.save_track(
        int(track.id),
        (
            SensorAssignment(physical_id="s1", position_id="start_finish", lane=1),
            SensorAssignment(physical_id="s2", position_id="start_finish", lane=2),
        ),
    )
    facts = SerialPortFacts(
        "COM11", vid=0x2341, pid=0x0042, serial_number="BOARD1", location="1-1.2"
    )

    def opener(port: str) -> SerialSensorTransport:
        assert port == "COM11"
        return transport

    page = SensorTimingPage(
        env.runtime.translator,
        factory._link,
        factory._store,
        env.runtime.services.get(TimingSetupService),
        env.tracks,
        ports=lambda: (facts,),
        serial_transport=opener,
    )
    qtbot.addWidget(page)
    page.show()
    QApplication.processEvents()
    assert page.port_combo.currentText() == "COM11"
    developer = page.findChild(QGroupBox, "sensor-developer")
    assert developer is not None and developer.isChecked() is True
    assert page.offset_edit.text() == "0"
    assert factory._link.connected is False
    _identify(factory._link, transport, stream)
    _sync(factory._link, transport)
    page.refresh()
    assert factory._link.sync.value == "synchronized"
    assert factory._link.mapping_origin == "measured"
    assert page.readiness_label.text() == "Messbereit"
    return page, factory._link, transport, stream, track


def _live(qtbot: QtBot, env: Env, runner: RaceRunner) -> LiveRaceView:
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
    return live


def _result_texts(page: SensorTimingPage) -> list[str]:
    page.refresh()
    return [log_cell(page, row, 5) for row in range(page.log.rowCount())]


def _push(
    stream: _Stream,
    transport: SerialSensorTransport,
    lines: tuple[tuple[int, int, str], ...],
) -> None:
    for sequence, device_ns, physical in lines:
        text = _frame(_HELLO.session_id, sequence, device_ns, physical) + "\n"
        stream.push(text.encode("ascii"))
    transport.pump()
