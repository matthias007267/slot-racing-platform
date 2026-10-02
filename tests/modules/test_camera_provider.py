"""Camera timing provider: synthetic frames become sensor events and race laps.

No camera device is opened. Frames are pushed through a manual frame source.
"""

from __future__ import annotations

import sys

import pytest

from slot_racing.core.clock import ManualClock
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import (
    DriverId,
    Participant,
    RaceId,
    RaceStatus,
    TimingLayout,
    TimingSensor,
    TimingSetup,
)
from slot_racing.core.errors import ProviderConfigurationError, ProviderUnavailable
from slot_racing.core.events import Event, EventBus, LapCompleted, SensorTriggered
from slot_racing.core.i18n import Translator
from slot_racing.core.plugin import (
    ContributionRegistry,
    PluginManager,
    ServiceRegistry,
)
from slot_racing.core.timing import (
    ManuallyTriggerable,
    ProviderCapabilities,
    TimingSessionSpec,
    TimingSource,
    TimingSourceFactory,
)
from slot_racing.core.timing_registry import TimingProviderRegistry
from slot_racing.modules.races.engine import RaceConfig, RaceEngine
from slot_racing.modules.timing_camera.detection import DetectionZone, DetectorSettings
from slot_racing.modules.timing_camera.frame_source import ManualFrameSource
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.plugin import CameraTimingPlugin
from slot_racing.modules.timing_camera.provider import (
    PROVIDER_ID,
    CameraTimingFactory,
    CameraTimingProvider,
)

WIDTH = 80
HEIGHT = 30
LINE_X = 30
LINE_WIDTH = 4


def lane_roi(lane: int, x: int = LINE_X) -> DetectionRoi:
    return DetectionRoi(x, (lane - 1) * 10, LINE_WIDTH, 10)


def blank() -> GrayFrame:
    return GrayFrame.blank(WIDTH, HEIGHT)


def car(lane: int, x: int) -> GrayFrame:
    y = (lane - 1) * 10 + 2
    return blank().paint(DetectionRoi(x, y, 8, 6), 255)


def with_cars(frame: GrayFrame, lanes: dict[int, int]) -> GrayFrame:
    for lane, x in lanes.items():
        y = (lane - 1) * 10 + 2
        frame = frame.paint(DetectionRoi(x, y, 8, 6), 255)
    return frame


def monitored(*zones: DetectionZone) -> DetectorSettings:
    return DetectorSettings(zones)


def spec_for(
    positions: tuple[str, ...],
    sensors: dict[str, str],
    lanes: tuple[int, ...] = (1,),
) -> TimingSessionSpec:
    layout = TimingLayout.from_position_ids(positions)
    setup = TimingSetup(
        layout,
        tuple(TimingSensor(sensors[position], position) for position in positions),
    )
    return TimingSessionSpec(setup, lanes, 1)


def running(
    session: TimingSessionSpec,
    settings: DetectorSettings,
    background: GrayFrame | None = None,
) -> tuple[CameraTimingProvider, ManualFrameSource, list[SensorTriggered]]:
    frames = ManualFrameSource()
    reference = blank() if background is None else background
    source = CameraTimingFactory(frames, settings, reference).create_source(session)
    assert isinstance(source, CameraTimingProvider)
    received: list[SensorTriggered] = []
    source.start(received.append)
    return source, frames, received


def plugin_registry() -> tuple[PluginManager, TimingProviderRegistry]:
    services = ServiceRegistry()
    manager = PluginManager(
        bus=EventBus(),
        services=services,
        contributions=ContributionRegistry(),
        translator=Translator(),
        clock=ManualClock(),
        config=AppConfig(),
    )
    manager.register(CameraTimingPlugin())

    def factories() -> list[TimingSourceFactory]:
        return services.find_all(TimingSourceFactory)

    return manager, TimingProviderRegistry(factories)


def test_factory_identity_and_capabilities() -> None:
    factory = CameraTimingFactory()
    assert factory.provider_id == "camera"
    assert factory.provider_id == PROVIDER_ID
    assert factory.capabilities == ProviderCapabilities(
        supports_multiple_lanes=True, supports_test_mode=False
    )
    assert not factory.availability().available
    assert factory.availability().reason_key == "error.timing_provider.camera_not_connected"


def test_factory_creates_a_source_without_a_camera() -> None:
    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"})
    source = CameraTimingFactory().create_source(session)
    assert isinstance(source, TimingSource)
    assert not isinstance(source, ManuallyTriggerable)
    assert source.source_id == "camera"
    assert not source.is_running
    source.start(lambda _event: None)
    assert source.is_running
    source.poll()
    source.stop()
    source.stop()
    assert not source.is_running


def test_plugin_registers_the_camera_provider_once_and_opens_no_device() -> None:
    assert "cv2" not in sys.modules
    manager, registry = plugin_registry()
    manager.enable("timing_camera")
    assert "cv2" not in sys.modules
    assert registry.provider_ids() == ["camera"]
    info = registry.info("camera")
    assert info.capabilities.supports_multiple_lanes
    assert not info.capabilities.supports_test_mode
    assert not info.available
    assert info.availability.reason_key == "error.timing_provider.camera_not_connected"

    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"})
    with pytest.raises(ProviderUnavailable) as caught:
        registry.create_source("camera", session)
    assert caught.value.key == "error.timing_provider.camera_not_connected"
    created = registry.factory("camera").create_source(session)
    assert isinstance(created, TimingSource)

    manager.disable("timing_camera")
    assert registry.provider_ids() == []


def test_two_camera_factories_are_rejected_as_a_duplicate_provider() -> None:
    registry = TimingProviderRegistry(lambda: [CameraTimingFactory(), CameraTimingFactory()])
    with pytest.raises(ProviderConfigurationError) as caught:
        registry.provider_ids()
    assert caught.value.key == "error.timing_provider.duplicate"


def test_a_zone_without_an_active_sensor_is_rejected() -> None:
    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"})
    settings = monitored(DetectionZone("missing", 1, lane_roi(1)))
    with pytest.raises(ProviderConfigurationError) as caught:
        CameraTimingFactory(settings=settings).create_source(session)
    assert caught.value.key == "error.timing_provider.camera_position_unknown"


def test_duplicate_lanes_are_rejected() -> None:
    session = TimingSessionSpec(TimingSetup.from_position_ids(["start_finish"]), (1, 1), 1)
    with pytest.raises(ProviderConfigurationError) as caught:
        CameraTimingFactory().validate(session)
    assert caught.value.key == "error.timing_provider.lanes_duplicate"


def test_start_makes_the_source_running() -> None:
    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"})
    zone = monitored(DetectionZone("start_finish", 1, lane_roi(1)))
    source, _frames, _received = running(session, zone)
    assert source.is_running
    with pytest.raises(RuntimeError):
        source.start(lambda _event: None)


def test_a_crossing_becomes_one_sensor_event() -> None:
    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"})
    source, frames, received = running(
        session, monitored(DetectionZone("start_finish", 1, lane_roi(1)))
    )
    frames.submit(car(1, x=10), 1_000)
    source.poll()
    assert received == []

    frames.submit(car(1, x=28), 2_000)
    source.poll()
    assert received == [
        SensorTriggered(
            timestamp_ns=2_000,
            source_id="camera",
            sensor_id="sensor-sf",
            position_id="start_finish",
            lane=1,
        )
    ]
    frames.submit(car(1, x=28), 3_000)
    source.poll()
    assert len(received) == 1


def test_one_frame_with_two_lanes_emits_two_ordered_events() -> None:
    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"}, lanes=(1, 3))
    settings = monitored(
        DetectionZone("start_finish", 3, lane_roi(3)),
        DetectionZone("start_finish", 1, lane_roi(1)),
    )
    source, frames, received = running(session, settings)
    frames.submit(with_cars(blank(), {1: 28, 3: 28}), 123_456_789)
    source.poll()
    reported = [
        (event.lane, event.timestamp_ns, event.sensor_id, event.position_id) for event in received
    ]
    assert reported == [
        (1, 123_456_789, "sensor-sf", "start_finish"),
        (3, 123_456_789, "sensor-sf", "start_finish"),
    ]
    assert {event.source_id for event in received} == {"camera"}


def test_each_position_uses_the_sensor_from_the_setup() -> None:
    session = spec_for(
        ("start_finish", "sector_1"),
        {"start_finish": "sensor-sf", "sector_1": "sensor-s1"},
    )
    settings = monitored(
        DetectionZone("sector_1", 1, lane_roi(1, x=50)),
        DetectionZone("start_finish", 1, lane_roi(1)),
    )
    source, frames, received = running(session, settings)
    frame = with_cars(blank(), {1: 28}).paint(DetectionRoi(48, 2, 8, 6), 255)
    frames.submit(frame, 5_000)
    source.poll()
    reported = [
        (event.position_id, event.sensor_id, event.lane, event.timestamp_ns) for event in received
    ]
    assert reported == [
        ("sector_1", "sensor-s1", 1, 5_000),
        ("start_finish", "sensor-sf", 1, 5_000),
    ]


def test_empty_static_and_outside_motion_emit_nothing() -> None:
    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"})
    zone = DetectionZone("start_finish", 1, lane_roi(1))
    source, frames, received = running(session, monitored(zone))
    frames.submit(blank(), 1)
    frames.submit(car(1, x=0), 2)
    source.poll()
    assert received == []

    static = blank().paint(lane_roi(1), 255)
    parked, parked_frames, parked_events = running(session, monitored(zone), background=static)
    parked_frames.submit(static, 3)
    parked_frames.submit(static, 4)
    parked.poll()
    assert parked_events == []


def test_stop_drops_pending_frames_and_can_be_repeated() -> None:
    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"})
    source, frames, received = running(
        session, monitored(DetectionZone("start_finish", 1, lane_roi(1)))
    )
    frames.submit(car(1, x=28), 10)
    source.stop()
    source.stop()
    assert not source.is_running
    source.poll()
    frames.submit(car(1, x=28), 20)
    source.poll()
    assert received == []


def test_pause_suppresses_frames_and_resume_accepts_the_next_one() -> None:
    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"})
    source, frames, received = running(
        session, monitored(DetectionZone("start_finish", 1, lane_roi(1)))
    )
    frames.submit(car(1, x=28), 100)
    source.pause()
    source.poll()
    assert received == []

    frames.submit(car(1, x=28), 200)
    source.poll()
    assert received == []

    source.resume()
    source.poll()
    assert [event.timestamp_ns for event in received] == [100]

    frames.submit(blank(), 250)
    source.poll()
    frames.submit(car(1, x=28), 300)
    source.poll()
    assert [event.timestamp_ns for event in received] == [100, 300]


def test_a_frame_submitted_before_start_is_ignored() -> None:
    session = spec_for(("start_finish",), {"start_finish": "sensor-sf"})
    frames = ManualFrameSource()
    source = CameraTimingFactory(
        frames, monitored(DetectionZone("start_finish", 1, lane_roi(1))), blank()
    ).create_source(session)
    assert isinstance(source, CameraTimingProvider)
    frames.submit(car(1, x=28), 1)
    received: list[SensorTriggered] = []
    source.start(received.append)
    source.poll()
    assert received == []


def test_a_synthetic_crossing_completes_a_lap_in_the_race_engine() -> None:
    setup = TimingSetup(
        TimingLayout.from_position_ids(["start_finish"]),
        (TimingSensor("sensor-sf", "start_finish"),),
    )
    frames = ManualFrameSource()
    settings = monitored(DetectionZone("start_finish", 1, lane_roi(1)))
    source = CameraTimingFactory(frames, settings, blank()).create_source(
        TimingSessionSpec(setup, (1,), 1)
    )
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(Event, events.append)
    config = RaceConfig(RaceId(1), 1, (Participant(DriverId(1), 1),), setup.layout)
    engine = RaceEngine(config, bus, ManualClock(), [source])

    engine.start()
    frames.submit(blank(), 500_000_000)
    engine.poll_sources()
    assert not any(isinstance(event, LapCompleted) for event in events)

    frames.submit(car(1, x=28), 1_000_000_000)
    engine.poll_sources()

    triggered = [event for event in events if isinstance(event, SensorTriggered)]
    assert triggered == [
        SensorTriggered(
            timestamp_ns=1_000_000_000,
            source_id="camera",
            sensor_id="sensor-sf",
            position_id="start_finish",
            lane=1,
        )
    ]
    laps = [event for event in events if isinstance(event, LapCompleted)]
    assert len(laps) == 1
    lap = laps[0]
    assert isinstance(lap, LapCompleted)
    assert (lap.lane, lap.lap_number, lap.lap_time_ns) == (1, 1, 1_000_000_000)
    assert engine.status is RaceStatus.FINISHED
    assert not source.is_running
