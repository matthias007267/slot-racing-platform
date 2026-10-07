"""Camera race events reach the engine, and a single start/finish line times laps.

The detector is not under test here. These cases publish the sensor event the
camera already accepts, or they only check that a race consumer is fed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from slot_racing.core.clock import NANOS_PER_SECOND as S
from slot_racing.core.clock import ManualClock
from slot_racing.core.domain import (
    DriverId,
    Participant,
    RaceId,
    RaceStatus,
    TimingLayout,
    default_timing_setup,
)
from slot_racing.core.events import (
    Event,
    EventBus,
    LapCompleted,
    LapStarted,
    SensorTriggered,
)
from slot_racing.core.timing import TimingSourceFactory
from slot_racing.modules.races.engine import RaceConfig, RaceEngine
from slot_racing.modules.races.runner import layout_for_reported_positions
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.timing_camera.detection import ZoneState
from slot_racing.modules.timing_camera.lease import CameraLease
from slot_racing.modules.timing_camera.provider import CameraTimingFactory, CameraTimingProvider
from slot_racing.modules.timing_camera.session import CameraSession
from slot_racing.modules.timing_camera.store import CameraConfigurationStore
from tests.modules.conftest import Env
from tests.modules.test_camera_race_integration import (
    ZONE_X,
    DeviceHub,
    attach,
    blank,
    consume,
    create_camera_race,
    cross,
    drive,
    listen,
    of_type,
    save_document,
    zone,
    zone_rect,
)


class Line:
    def __init__(self, lanes: int = 1, laps: int = 3, origin_ns: int = 0) -> None:
        self.clock = ManualClock(start_ns=origin_ns)
        self.bus = EventBus()
        self.events: list[Event] = []
        self.bus.subscribe(Event, self.events.append)
        participants = tuple(Participant(DriverId(lane), lane) for lane in range(1, lanes + 1))
        layout = TimingLayout.from_position_ids(["start_finish"])
        self.engine = RaceEngine(
            RaceConfig(RaceId(3), laps, participants, layout),
            self.bus,
            self.clock,
        )

    def go(self) -> None:
        self.engine.start()

    def cross(self, timestamp_ns: int, lane: int = 1) -> None:
        self.bus.publish(
            SensorTriggered(
                timestamp_ns=timestamp_ns,
                source_id="camera",
                sensor_id="start_finish",
                position_id="start_finish",
                lane=lane,
            )
        )

    def laps(self, lane: int | None = None) -> list[LapCompleted]:
        found = [event for event in self.events if isinstance(event, LapCompleted)]
        if lane is None:
            return found
        return [event for event in found if event.lane == lane]


def test_the_default_layout_keeps_only_positions_the_source_can_report() -> None:
    narrowed = layout_for_reported_positions(
        default_timing_setup().layout, frozenset({"start_finish"})
    )
    assert [position.id for position in narrowed.positions] == ["start_finish"]
    assert [position.id for position in narrowed.lap_sequence] == ["start_finish"]
    unchanged = layout_for_reported_positions(default_timing_setup().layout, None)
    assert unchanged == default_timing_setup().layout


def test_the_first_start_finish_after_go_starts_timing_and_stores_no_lap() -> None:
    line = Line(laps=3)
    line.go()
    assert line.engine.results()[0].laps_completed == 0
    line.cross(10 * S)
    assert line.laps() == []
    assert line.engine.results()[0].laps_completed == 0
    assert line.engine.status is RaceStatus.RUNNING
    started = [event for event in line.events if isinstance(event, LapStarted)]
    assert [(event.lane, event.lap_number, event.race_time_ns) for event in started] == [
        (1, 1, 10 * S)
    ]


def test_the_second_start_finish_stores_the_difference() -> None:
    line = Line()
    line.go()
    line.cross(10 * S)
    line.cross(15 * S + S // 4)
    laps = line.laps()
    assert len(laps) == 1
    assert laps[0].lap_number == 1
    assert laps[0].lap_time_ns == 5 * S + S // 4
    assert line.engine.results()[0].laps_completed == 1


def test_the_third_start_finish_stores_the_next_lap() -> None:
    line = Line()
    line.go()
    line.cross(10 * S)
    line.cross(15 * S + S // 4)
    line.cross(20 * S)
    assert [event.lap_time_ns for event in line.laps()] == [5 * S + S // 4, 4 * S + 3 * S // 4]
    assert [event.lap_number for event in line.laps()] == [1, 2]


def test_two_lanes_start_their_clocks_independently() -> None:
    line = Line(lanes=2)
    line.go()
    line.cross(10 * S, lane=1)
    line.cross(11 * S, lane=2)
    line.cross(15 * S, lane=1)
    line.cross(17 * S, lane=2)
    assert [(event.lane, event.lap_time_ns) for event in line.laps()] == [
        (1, 5 * S),
        (2, 6 * S),
    ]
    assert [result.laps_completed for result in line.engine.results()] == [1, 1]


def test_a_crossing_before_go_does_not_start_the_lap_clock() -> None:
    line = Line(origin_ns=5 * S)
    line.cross(4 * S)
    line.go()
    assert line.laps() == []
    assert not any(isinstance(event, LapStarted) for event in line.events)
    line.cross(4 * S)
    assert line.laps() == []
    assert line.engine.results()[0].laps_completed == 0
    line.cross(12 * S)
    started = [event for event in line.events if isinstance(event, LapStarted)]
    assert [(event.lap_number, event.race_time_ns) for event in started] == [(1, 7 * S)]
    assert line.laps() == []


def test_a_second_race_does_not_keep_the_first_lap_clock() -> None:
    first = Line(laps=1)
    first.go()
    first.cross(10 * S)
    first.cross(16 * S)
    assert first.engine.status is RaceStatus.FINISHED
    second = Line(laps=1)
    second.go()
    second.cross(30 * S)
    assert second.laps() == []
    assert second.engine.results()[0].laps_completed == 0
    second.cross(34 * S)
    assert second.laps()[0].lap_time_ns == 4 * S


def test_default_layout_does_not_drop_a_start_finish_only_camera(env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    track = env.track(name="Standard", lanes=2)
    race = env.races.create_race("Standard", track.id, 3, "camera")
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    events = listen(env)
    runner = env.controller.start_race(race.id)
    try:
        device = hub.live()
        consume(device, blank())
        drive(runner)
        cross(device, zone_rect(ZONE_X))
        drive(runner)
        triggered = of_type(events, SensorTriggered)
        assert [(event.lane, event.position_id) for event in triggered] == [(1, "start_finish")]
        assert of_type(events, LapCompleted) == []
        started = of_type(events, LapStarted)
        assert len(started) == 1
        assert started[0].race_time_ns == triggered[0].timestamp_ns
        assert runner.snapshot().rows[0].laps_completed == 0
    finally:
        runner.close()


def test_race_consumer_gets_frames_and_one_detection_is_one_timing_event(env: Env) -> None:
    hub = DeviceHub()
    lease = CameraLease()
    session = CameraSession(lease, devices=hub)
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    factory = CameraTimingFactory(
        configurations=CameraConfigurationStore(env.runtime.database),
        devices=hub,
        lease=lease,
        session=session,
    )
    env.runtime.services.register(
        TimingSourceFactory,
        factory,
        owner="camera-events",
        name="camera-events",
    )
    race_id, _track = create_camera_race(env, lanes=1)
    events = listen(env)
    runner = env.controller.start_race(race_id)
    try:
        attached = [
            event
            for event in session.events()
            if event.action == "camera_consumer_attached" and event.consumer.startswith("race-")
        ]
        assert len(attached) == 1
        assert attached[0].reason == "race_started"
        device = hub.live()
        consume(device, blank())
        drive(runner)
        source = runner._engine._sources[0]
        assert isinstance(source, CameraTimingProvider)
        assert source.frames_observed >= 1
        assert source._detector is not None
        first_detector = id(source._detector)
        cross(device, zone_rect(ZONE_X))
        drive(runner)
        triggered = of_type(events, SensorTriggered)
        assert [(event.lane, event.position_id) for event in triggered] == [(1, "start_finish")]
        assert of_type(events, LapCompleted) == []
        assert runner.snapshot().rows[0].laps_completed == 0
        assert runner.status is RaceStatus.RUNNING
    finally:
        runner.close()
        session.close(reason="shutdown")
    assert first_detector != 0


def test_a_new_race_builds_a_new_detector(env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    first_id, _track = create_camera_race(env, lanes=1, laps=3)
    first = env.controller.start_race(first_id)
    try:
        device = hub.live()
        consume(device, blank())
        drive(first)
        cross(device, zone_rect(ZONE_X))
        drive(first)
        first_source = first._engine._sources[0]
        assert isinstance(first_source, CameraTimingProvider)
        assert first_source._detector is not None
        detector_a = id(first_source._detector)
        assert first.snapshot().rows[0].laps_completed == 0
        first.stop()
    finally:
        first.close()

    second_id, _track = create_camera_race(env, lanes=1, laps=3, name="Zweite")
    second = env.controller.start_race(second_id)
    try:
        device = hub.live()
        consume(device, blank())
        drive(second)
        second_source = second._engine._sources[0]
        assert isinstance(second_source, CameraTimingProvider)
        assert second_source._detector is not None
        detector_b = id(second_source._detector)
        assert detector_a != detector_b
        assert second_source._detector.zone_state("start_finish", 1) is ZoneState.CLEAR
        cross(device, zone_rect(ZONE_X))
        drive(second)
        assert second.snapshot().rows[0].laps_completed == 0
    finally:
        second.close()


def test_audio_on_and_off_still_deliver_a_camera_timing_event(qtbot: QtBot, env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1, laps=3)
    events = listen(env)
    live = LiveRaceView(
        env.runtime.translator,
        env.controller,
        service=env.races,
        config=env.runtime.config,
    )
    live.cue_interval_ms = 60_000
    live.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    qtbot.addWidget(live)
    env.runtime.config.audio_enabled = True
    runner = env.controller.prepare_race(race_id)
    try:
        live.show()
        live.open_for_start(runner)
        for _ in range(5):
            live.advance_start_cue()
        assert runner.status is RaceStatus.RUNNING
        device = hub.live()
        consume(device, blank())
        drive(runner)
        cross(device, zone_rect(ZONE_X))
        drive(runner)
        assert [(event.lane, event.position_id) for event in of_type(events, SensorTriggered)] == [
            (1, "start_finish")
        ]
        assert of_type(events, LapCompleted) == []
        assert runner.snapshot().source_errors == ()
        runner.stop()
    finally:
        runner.close()

    env.runtime.config.audio_enabled = False
    again_id, _track = create_camera_race(env, lanes=1, laps=3, name="Leise")
    quiet = listen(env)
    again = env.controller.prepare_race(again_id)
    try:
        live.open_for_start(again)
        for _ in range(5):
            live.advance_start_cue()
        assert again.status is RaceStatus.RUNNING
        device = hub.live()
        consume(device, blank())
        drive(again)
        cross(device, zone_rect(ZONE_X))
        drive(again)
        assert of_type(quiet, SensorTriggered)
        assert again.snapshot().source_errors == ()
    finally:
        again.close()
