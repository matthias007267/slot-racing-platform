"""Saved camera configuration timing a whole race.

The path is the production one: RaceController, the timing registry, a new
CameraTimingFactory with no injected zones, the capture thread, the lane
detector, SensorTriggered and the race engine. The device is scripted. No test
here pushes a SensorTriggered straight into the engine.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import (
    RaceId,
    RaceStatus,
    TimingLayout,
    TimingSensor,
    TimingSetup,
    TrackId,
)
from slot_racing.core.errors import ProviderConfigurationError, ProviderUnavailable
from slot_racing.core.events import (
    Event,
    LapCompleted,
    RaceFinished,
    SectorCompleted,
    SensorTriggered,
)
from slot_racing.core.storage import Setting
from slot_racing.core.timing import TimingSetupService, TimingSourceFactory
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.results_view import ResultsView
from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import MAX_FRAMES_PER_POLL, MAX_QUEUED_FRAMES
from slot_racing.modules.timing_camera.configuration import (
    CameraConfiguration,
    NormalizedRoi,
    StoredCamera,
    StoredDetection,
    StoredDetectionZone,
    roi_to_pixels,
)
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.lease import CameraBusyError, CameraLease
from slot_racing.modules.timing_camera.preview import CameraPreview
from slot_racing.modules.timing_camera.provider import CameraTimingFactory
from slot_racing.modules.timing_camera.store import (
    CAMERA_CONFIGURATION_KEY,
    CameraConfigurationStore,
)
from tests.modules.conftest import Env
from tests.modules.test_camera_capture import ScriptedCapture
from tests.modules.test_ui_management import column_text

WIDTH = 100
HEIGHT = 40
CAMERA = StoredCamera(device_index=4, width=WIDTH, height=HEIGHT, fps=12)
EXPECTED = CameraConfig(device_index=4, width=WIDTH, height=HEIGHT, fps=12)
# 0.30 * 100 and 0.25 * 40 divide evenly, so the saved zone is x=30, y=0, 10x10.
ZONE_X = 0.30
CAR_IN_ZONE = DetectionRoi(28, 2, 8, 6)
CAR_OUTSIDE = DetectionRoi(0, 2, 8, 6)
CAR_LANE_2 = DetectionRoi(28, 22, 8, 6)
CAR_SECTOR = DetectionRoi(48, 2, 8, 6)
CAR_FINISH = DetectionRoi(78, 2, 8, 6)


class SyncCapture(ScriptedCapture):
    """Scripted device that reports when the capture thread has queued a frame.

    ``entries`` grows at the start of every ``read``. After a frame is taken,
    the thread queues it and only then enters ``read`` again. Waiting for both
    a new read and a new entry means the frame is already in the race queue.
    """

    def __init__(self, *, fail_open: bool = False) -> None:
        super().__init__(fail_open=fail_open)
        self.entries = 0

    def read(self) -> GrayFrame:
        with self._cond:
            self.entries += 1
            self._cond.notify_all()
        return super().read()

    def entry_count(self) -> int:
        with self._cond:
            return self.entries

    def pending(self) -> int:
        with self._cond:
            return len(self._items)


class DeviceHub:
    """One scripted device per open. Availability probes and the race get their own."""

    def __init__(self, *, fail_open: bool = False) -> None:
        self.fail_open = fail_open
        self.created: list[SyncCapture] = []
        self.configs: list[CameraConfig] = []

    def __call__(self, config: CameraConfig) -> SyncCapture:
        self.configs.append(config)
        device = SyncCapture(fail_open=self.fail_open)
        self.created.append(device)
        return device

    def live(self) -> SyncCapture:
        opened = [device for device in self.created if device.opened]
        if len(opened) != 1:
            raise AssertionError(f"expected one open camera, found {len(opened)}")
        return opened[0]


def zone(
    position_id: str,
    lane: int,
    *,
    x: float,
    y: float = 0.0,
    width: float = 0.10,
    height: float = 0.25,
) -> StoredDetectionZone:
    return StoredDetectionZone(
        position_id=position_id,
        lane=lane,
        roi=NormalizedRoi(x=x, y=y, width=width, height=height),
    )


def blank(width: int = WIDTH, height: int = HEIGHT) -> GrayFrame:
    return GrayFrame.blank(width, height)


def painted(
    patches: tuple[DetectionRoi, ...],
    width: int = WIDTH,
    height: int = HEIGHT,
) -> GrayFrame:
    image = blank(width, height)
    for roi in patches:
        image = image.paint(roi, 255)
    return image


def camera_threads() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == "slot-racing-camera" and thread.is_alive()
    ]


def wait_until(predicate: Callable[[], bool], message: str) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError(message)


def wait_armed(device: SyncCapture) -> None:
    """Block until the capture thread is waiting for the next frame."""

    def armed() -> bool:
        return device.opened and device.entry_count() >= 1 and device.pending() == 0

    wait_until(armed, "capture thread did not start")
    time.sleep(0.01)
    if not armed():
        raise AssertionError("capture thread did not stay armed")


def consume(device: SyncCapture, frame: GrayFrame) -> None:
    """Push one frame and return only after the capture thread has queued it."""
    wait_armed(device)
    entries = device.entry_count()
    reads = device.reads
    device.push(frame)
    wait_until(
        lambda: device.reads > reads and device.entry_count() > entries,
        f"capture missed the frame (reads={device.reads}, entries={device.entry_count()})",
    )


def save_document(
    env: Env,
    zones: tuple[StoredDetectionZone, ...],
    camera: StoredCamera = CAMERA,
) -> None:
    CameraConfigurationStore(env.runtime.database).save(
        CameraConfiguration(camera=camera, detection=StoredDetection(zones=zones))
    )


def save_layout(env: Env, track_id: TrackId, positions: tuple[str, ...]) -> None:
    sensors = tuple(TimingSensor(f"sensor-{position}", position) for position in positions)
    env.runtime.services.get(TimingSetupService).save_setup(
        track_id,
        TimingSetup(TimingLayout.from_position_ids(positions), sensors),
    )


def create_camera_race(
    env: Env,
    *,
    lanes: int,
    laps: int = 1,
    positions: tuple[str, ...] = ("start_finish",),
    name: str = "Kamera",
) -> tuple[RaceId, str]:
    # One car is enough for the race. The track is stored with at least two lanes.
    track = env.track(name="Ring", lanes=max(lanes, 2))
    save_layout(env, track.id, positions)
    race = env.races.create_race(name, track.id, laps, "camera")
    for lane in range(1, lanes + 1):
        driver_id, vehicle_id = env.pair(lane)
        env.races.add_participant(race.id, driver_id, vehicle_id, lane)
    return race.id, track.name


def attach(
    env: Env,
    hub: DeviceHub,
    lease: CameraLease | None = None,
) -> CameraTimingFactory:
    factory = CameraTimingFactory(
        configurations=CameraConfigurationStore(env.runtime.database),
        devices=hub,
        lease=lease,
    )
    assert factory._settings is None
    assert factory._camera is None
    assert factory._frames is None
    env.runtime.services.register(
        TimingSourceFactory,
        factory,
        owner="camera-e2e",
        name="camera-e2e",
    )
    return factory


def listen(env: Env) -> list[Event]:
    events: list[Event] = []
    env.runtime.bus.subscribe(Event, events.append)
    return events


def of_type[T: Event](events: list[Event], kind: type[T]) -> list[T]:
    return [event for event in events if isinstance(event, kind)]


def cells(table: QTableWidget, row: int) -> list[str]:
    values: list[str] = []
    for column in range(table.columnCount()):
        item = table.item(row, column)
        values.append("" if item is None else item.text())
    return values


def test_a_saved_configuration_completes_a_camera_race(env: Env) -> None:
    hub = DeviceHub()
    lease = CameraLease()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    factory = attach(env, hub, lease)
    race_id, _track = create_camera_race(env, lanes=1)
    events = listen(env)
    runner = env.controller.start_race(race_id)
    try:
        assert isinstance(factory, CameraTimingFactory)
        assert hub.configs
        assert set(hub.configs) == {EXPECTED}
        assert CameraConfig() != EXPECTED
        assert camera_threads()
        assert lease.holder() == CameraLease.RACE

        device = hub.live()
        consume(device, blank())
        runner.tick()
        assert of_type(events, SensorTriggered) == []

        consume(device, painted((CAR_OUTSIDE,)))
        runner.tick()
        assert of_type(events, SensorTriggered) == []

        before = time.perf_counter_ns()
        consume(device, painted((CAR_IN_ZONE,)))
        after_capture = time.perf_counter_ns()
        runner.tick()

        triggered = of_type(events, SensorTriggered)
        assert triggered == [
            SensorTriggered(
                timestamp_ns=triggered[0].timestamp_ns,
                source_id="camera",
                sensor_id="sensor-start_finish",
                position_id="start_finish",
                lane=1,
            )
        ]
        # The stamp is taken in the capture thread, before this tick runs poll.
        assert before <= triggered[0].timestamp_ns <= after_capture
        laps = of_type(events, LapCompleted)
        assert len(laps) == 1
        assert laps[0].lap_number == 1
        assert laps[0].lane == 1
        assert laps[0].lap_time_ns == triggered[0].timestamp_ns
        finished = of_type(events, RaceFinished)
        assert len(finished) == 1
        assert finished[0].aborted is False
        assert [
            (row.lane, row.position, row.laps_completed, row.finished)
            for row in finished[0].results
        ] == [(1, 1, 1, True)]
        assert runner.status is RaceStatus.FINISHED
        assert env.races.require_race(race_id).status is RaceStatus.FINISHED
        assert lease.holder() is None
        assert camera_threads() == []
    finally:
        runner.close()


def test_a_running_race_keeps_the_snapshot_when_the_document_changes(env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1)
    events = listen(env)
    runner = env.controller.start_race(race_id)
    try:
        device = hub.live()
        consume(device, blank())
        runner.tick()
        save_document(
            env,
            (zone("start_finish", 1, x=0.0),),
            StoredCamera(device_index=9, width=WIDTH, height=HEIGHT, fps=12),
        )
        assert CameraConfigurationStore(env.runtime.database).load().camera.device_index == 9

        consume(device, painted((CAR_OUTSIDE,)))
        runner.tick()
        assert of_type(events, SensorTriggered) == []

        consume(device, painted((CAR_IN_ZONE,)))
        runner.tick()
        triggered = of_type(events, SensorTriggered)
        assert [event.position_id for event in triggered] == ["start_finish"]
        assert runner.status is RaceStatus.FINISHED
    finally:
        runner.close()


def test_a_new_factory_loads_the_stored_document_for_the_next_race(env: Env) -> None:
    first_hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, first_hub)
    first_id, _track = create_camera_race(env, lanes=1)
    first_events = listen(env)
    first = env.controller.start_race(first_id)
    try:
        device = first_hub.live()
        consume(device, blank())
        first.tick()
        consume(device, painted((CAR_IN_ZONE,)))
        first.tick()
        assert len(of_type(first_events, LapCompleted)) == 1
    finally:
        first.close()

    second_hub = DeviceHub()
    save_document(
        env,
        (zone("start_finish", 1, x=0.0),),
        StoredCamera(device_index=8, width=WIDTH, height=HEIGHT, fps=15),
    )
    env.runtime.services.remove_owner("camera-e2e")
    attach(env, second_hub)
    second_id, _track = create_camera_race(env, lanes=1, name="Zweite")
    second_events = listen(env)
    second = env.controller.start_race(second_id)
    try:
        assert set(second_hub.configs) == {
            CameraConfig(device_index=8, width=WIDTH, height=HEIGHT, fps=15)
        }
        device = second_hub.live()
        consume(device, blank())
        second.tick()
        consume(device, painted((CAR_IN_ZONE,)))
        second.tick()
        assert of_type(second_events, SensorTriggered) == []
        consume(device, painted((CAR_OUTSIDE,)))
        second.tick()
        triggered = of_type(second_events, SensorTriggered)
        assert [(event.position_id, event.lane, event.sensor_id) for event in triggered] == [
            ("start_finish", 1, "sensor-start_finish")
        ]
        assert second.status is RaceStatus.FINISHED
    finally:
        second.close()


def test_one_frame_with_two_lanes_keeps_one_timestamp_and_finishes_both(env: Env) -> None:
    hub = DeviceHub()
    # Stored lane 2 first. The detector still emits lane 1, then lane 2.
    save_document(
        env,
        (
            zone("start_finish", 2, x=ZONE_X, y=0.50),
            zone("start_finish", 1, x=ZONE_X, y=0.0),
        ),
    )
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=2)
    events = listen(env)
    runner = env.controller.start_race(race_id)
    try:
        device = hub.live()
        consume(device, blank())
        runner.tick()
        before = time.perf_counter_ns()
        consume(device, painted((CAR_IN_ZONE, CAR_LANE_2)))
        after_capture = time.perf_counter_ns()
        runner.tick()

        triggered = of_type(events, SensorTriggered)
        reported = [
            (event.lane, event.position_id, event.sensor_id, event.source_id) for event in triggered
        ]
        assert reported == [
            (1, "start_finish", "sensor-start_finish", "camera"),
            (2, "start_finish", "sensor-start_finish", "camera"),
        ]
        assert triggered[0].timestamp_ns == triggered[1].timestamp_ns
        assert before <= triggered[0].timestamp_ns <= after_capture
        assert [(event.lane, event.lap_number) for event in of_type(events, LapCompleted)] == [
            (1, 1),
            (2, 1),
        ]
        finished = of_type(events, RaceFinished)
        assert len(finished) == 1 and finished[0].aborted is False
        standings = [
            (row.lane, row.position, row.laps_completed, row.finished)
            for row in finished[0].results
        ]
        assert standings == [(1, 1, 1, True), (2, 2, 1, True)]
    finally:
        runner.close()


def test_two_positions_in_one_frame_follow_the_existing_race_rules(env: Env) -> None:
    hub = DeviceHub()
    # Driving order ends at start/finish: sector_2, sector_1, start_finish.
    # The detector emits by lane, then position id, which is not that order.
    save_document(
        env,
        (
            zone("start_finish", 1, x=0.80),
            zone("sector_2", 1, x=0.50),
            zone("sector_1", 1, x=ZONE_X),
        ),
    )
    attach(env, hub)
    race_id, _track = create_camera_race(
        env, lanes=1, positions=("start_finish", "sector_2", "sector_1")
    )
    events = listen(env)
    runner = env.controller.start_race(race_id)
    try:
        device = hub.live()
        consume(device, blank())
        runner.tick()
        before = time.perf_counter_ns()
        consume(device, painted((CAR_IN_ZONE, CAR_SECTOR, CAR_FINISH)))
        after_capture = time.perf_counter_ns()
        runner.tick()

        triggered = of_type(events, SensorTriggered)
        assert [(event.position_id, event.sensor_id, event.lane) for event in triggered] == [
            ("sector_1", "sensor-sector_1", 1),
            ("sector_2", "sensor-sector_2", 1),
            ("start_finish", "sensor-start_finish", 1),
        ]
        assert len({event.timestamp_ns for event in triggered}) == 1
        assert before <= triggered[0].timestamp_ns <= after_capture
        # Only sector_2 is the next point. The other two crossings are kept as
        # sensor events and ignored by the existing sequence rules.
        sectors = of_type(events, SectorCompleted)
        assert [(event.sector_number, event.lane) for event in sectors] == [(1, 1)]
        assert of_type(events, LapCompleted) == []
        assert runner.status is RaceStatus.RUNNING

        consume(device, blank())
        runner.tick()
        consume(device, painted((CAR_IN_ZONE, CAR_FINISH)))
        runner.tick()
        laps = of_type(events, LapCompleted)
        assert len(laps) == 1 and laps[0].lap_number == 1
        assert [(event.sector_number,) for event in of_type(events, SectorCompleted)] == [
            (1,),
            (2,),
            (3,),
        ]
        assert runner.snapshot().status is RaceStatus.FINISHED
        assert len(of_type(events, RaceFinished)) == 1
    finally:
        runner.close()


def test_pause_drops_frames_and_resume_does_not_count_a_car_already_in_the_zone(env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1)
    events = listen(env)
    runner = env.controller.start_race(race_id)
    try:
        device = hub.live()
        consume(device, blank())
        runner.tick()
        assert of_type(events, SensorTriggered) == []

        runner.pause()
        assert runner.status is RaceStatus.PAUSED
        reads_before = device.reads
        consume(device, painted((CAR_IN_ZONE,)))
        runner.tick()
        assert device.reads == reads_before + 1
        assert of_type(events, SensorTriggered) == []

        runner.resume()
        assert runner.snapshot().status is RaceStatus.RUNNING
        consume(device, painted((CAR_IN_ZONE,)))
        runner.tick()
        assert of_type(events, SensorTriggered) == []

        consume(device, blank())
        runner.tick()
        assert of_type(events, SensorTriggered) == []

        before = time.perf_counter_ns()
        consume(device, painted((CAR_IN_ZONE,)))
        after_capture = time.perf_counter_ns()
        runner.tick()
        triggered = of_type(events, SensorTriggered)
        assert len(triggered) == 1
        assert before <= triggered[0].timestamp_ns <= after_capture
        assert triggered[0].position_id == "start_finish"
        assert runner.snapshot().status is RaceStatus.FINISHED
    finally:
        runner.close()


def test_stop_releases_the_camera_and_a_held_preview_blocks_the_race(env: Env) -> None:
    hub = DeviceHub()
    lease = CameraLease()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub, lease)
    race_id, _track = create_camera_race(env, lanes=1)
    preview = CameraPreview(lease, devices=hub)
    events = listen(env)

    held = preview.open(EXPECTED)
    preview_device = hub.created[-1]
    try:
        assert lease.holder() == CameraLease.PREVIEW
        assert held.is_capturing
        assert preview_device.open_count == 1
        with pytest.raises(ProviderUnavailable) as blocked:
            env.controller.start_race(race_id)
        assert blocked.value.key == "error.timing_provider.camera_in_use"
        assert env.races.require_race(race_id).status is RaceStatus.READY
        assert preview_device.open_count == 1
        assert hub.live() is preview_device
    finally:
        held.stop()
    assert lease.holder() is None
    assert camera_threads() == []

    runner = env.controller.start_race(race_id)
    try:
        assert lease.holder() == CameraLease.RACE
        created = len(hub.created)
        with pytest.raises(CameraBusyError):
            preview.open(EXPECTED)
        assert len(hub.created) == created + 1
        assert hub.created[-1].open_count == 0
        device = hub.live()
        assert device.open_count == 1
        consume(device, blank())
        runner.tick()
        consume(device, painted((CAR_IN_ZONE,)))
        runner.stop()
        assert runner.status is RaceStatus.FINISHED
        assert runner.snapshot().aborted
        assert env.races.require_race(race_id).status is RaceStatus.ABORTED
        assert of_type(events, SensorTriggered) == []
        assert lease.holder() is None
        assert not device.opened
        assert camera_threads() == []
    finally:
        runner.close()

    again = preview.open(EXPECTED)
    try:
        assert lease.holder() == CameraLease.PREVIEW
        assert again.is_capturing
    finally:
        again.stop()
    assert lease.holder() is None
    assert camera_threads() == []


def test_a_backlog_drops_old_frames_before_they_reach_the_race(env: Env) -> None:
    assert MAX_QUEUED_FRAMES == 2
    assert MAX_FRAMES_PER_POLL == 4
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1)
    events = listen(env)
    runner = env.controller.start_race(race_id)
    try:
        device = hub.live()
        consume(device, blank())
        runner.tick()
        # Four cars would each be a crossing. Two later blanks are the only
        # frames that fit in the queue, so the cars never become race events.
        for _ in range(4):
            consume(device, painted((CAR_IN_ZONE,)))
        consume(device, blank())
        consume(device, blank())
        runner.tick()
        assert of_type(events, SensorTriggered) == []
        assert runner.status is RaceStatus.RUNNING

        consume(device, painted((CAR_IN_ZONE,)))
        runner.tick()
        assert len(of_type(events, SensorTriggered)) == 1
    finally:
        runner.close()


def test_a_missing_camera_is_not_reported_as_a_bad_configuration(env: Env) -> None:
    hub = DeviceHub(fail_open=True)
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1)
    with pytest.raises(ProviderUnavailable) as caught:
        env.controller.start_race(race_id)
    assert caught.value.key == "error.timing_provider.camera_not_connected"
    assert not isinstance(caught.value, ProviderConfigurationError)
    assert env.races.require_race(race_id).status is RaceStatus.READY
    assert hub.created
    assert all(not device.opened for device in hub.created)
    assert camera_threads() == []


def test_an_invalid_document_does_not_open_the_camera(env: Env) -> None:
    hub = DeviceHub()
    attach(env, hub)
    with env.runtime.database.session() as session:
        session.add(Setting(key=CAMERA_CONFIGURATION_KEY, value={"version": 2}))
    race_id, _track = create_camera_race(env, lanes=1)
    with pytest.raises(ProviderUnavailable) as caught:
        env.controller.start_race(race_id)
    assert caught.value.key == "error.timing_provider.camera_configuration_invalid"
    assert caught.value.key != "error.timing_provider.camera_not_connected"
    assert caught.value.key != "error.timing_provider.camera_zones_missing"
    assert hub.created == []
    assert env.races.require_race(race_id).status is RaceStatus.READY
    assert camera_threads() == []


def test_missing_zones_are_not_reported_as_a_missing_camera(env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (), StoredCamera(device_index=7, width=800, height=600, fps=15))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1)
    with pytest.raises(ProviderConfigurationError) as caught:
        env.controller.start_race(race_id)
    assert caught.value.key == "error.timing_provider.camera_zones_missing"
    assert not isinstance(caught.value, ProviderUnavailable)
    assert set(hub.configs) == {CameraConfig(device_index=7, width=800, height=600, fps=15)}
    assert all(not device.opened for device in hub.created)
    assert env.races.require_race(race_id).status is RaceStatus.READY
    assert camera_threads() == []


def test_repeated_read_failures_are_recorded_without_crashing_the_race(env: Env) -> None:
    hub = DeviceHub()
    lease = CameraLease()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub, lease)
    race_id, _track = create_camera_race(env, lanes=1)
    runner = env.controller.start_race(race_id)
    try:
        device = hub.live()
        wait_armed(device)
        for _ in range(3):
            device.fail(OSError("no frame"))
        wait_until(lambda: not device.opened, "capture did not stop after read failures")
        wait_until(lambda: camera_threads() == [], "capture thread still alive")
        runner.tick()
        snapshot = runner.snapshot()
        assert runner.status is RaceStatus.RUNNING
        assert snapshot.status is RaceStatus.RUNNING
        assert any(
            "CameraReadError" in message and "no frame" in message
            for message in snapshot.source_errors
        )
        assert not any("camera_not_connected" in message for message in snapshot.source_errors)
        runner.tick()
        assert runner.status is RaceStatus.RUNNING
        assert lease.holder() == CameraLease.RACE
        with pytest.raises(CameraBusyError):
            CameraPreview(lease, devices=hub).open(EXPECTED)
        runner.stop()
        assert runner.snapshot().status is RaceStatus.FINISHED
        assert runner.snapshot().aborted
        assert env.races.require_race(race_id).status is RaceStatus.ABORTED
        assert lease.holder() is None
    finally:
        runner.close()
    assert camera_threads() == []


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (640, 480, DetectionRoi(64, 120, 128, 48)),
        (1280, 720, DetectionRoi(128, 180, 256, 72)),
    ],
)
def test_saved_zones_scale_to_the_stored_resolution(
    env: Env,
    width: int,
    height: int,
    expected: DetectionRoi,
) -> None:
    normalized = NormalizedRoi(x=0.10, y=0.25, width=0.20, height=0.10)
    assert roi_to_pixels(normalized, width, height) == expected
    camera = StoredCamera(device_index=2, width=width, height=height, fps=20)
    hub = DeviceHub()
    save_document(
        env,
        (zone("start_finish", 1, x=0.10, y=0.25, width=0.20, height=0.10),),
        camera,
    )
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1)
    events = listen(env)
    runner = env.controller.start_race(race_id)
    try:
        assert set(hub.configs) == {
            CameraConfig(device_index=2, width=width, height=height, fps=20)
        }
        loaded = CameraConfigurationStore(env.runtime.database).load()
        assert roi_to_pixels(loaded.detection.zones[0].roi, width, height) == expected
        device = hub.live()
        consume(device, blank(width, height))
        runner.tick()
        consume(device, painted((DetectionRoi(0, 0, 4, 4),), width, height))
        runner.tick()
        assert of_type(events, SensorTriggered) == []
        consume(device, painted((expected,), width, height))
        runner.tick()
        triggered = of_type(events, SensorTriggered)
        assert len(triggered) == 1
        assert triggered[0].position_id == "start_finish"
        assert runner.status is RaceStatus.FINISHED
    finally:
        runner.close()


def test_the_live_view_shows_a_camera_race_without_a_camera_widget(qtbot: QtBot, env: Env) -> None:
    hub = DeviceHub()
    save_document(
        env,
        (
            zone("start_finish", 1, x=ZONE_X),
            zone("sector_1", 1, x=0.50),
        ),
    )
    attach(env, hub)
    track = env.track(name="Ring", lanes=2)
    save_layout(env, track.id, ("start_finish", "sector_1"))
    race = env.races.create_race("Kamera", track.id, 1, "camera")
    driver = env.driver("Anna")
    vehicle = env.vehicle("Porsche", driver_id=driver.id)
    env.races.add_participant(race.id, driver.id, vehicle.id, 1)

    runner = env.controller.start_race(race.id)
    live = LiveRaceView(env.runtime.translator, env.controller)
    live.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    qtbot.addWidget(live)
    live.show()
    try:
        live.show_runner(runner)
        device = hub.live()
        consume(device, blank())
        live.refresh()
        assert live.status_label.text().endswith("Läuft")
        assert live.name_label.text() == "Kamera"
        assert live.track_label.text().endswith("Ring")
        assert column_text(live.table, 0, "Platz") == "1"
        assert column_text(live.table, 0, "Spur") == "1"
        assert column_text(live.table, 0, "Fahrer") == "Anna"
        assert column_text(live.table, 0, "Fahrzeug") == "Porsche 911"
        assert column_text(live.table, 0, "Aktuelle Runde") == "1/1"
        assert column_text(live.table, 0, "Runden") == "0"
        names = [child.objectName() for child in live.findChildren(QWidget)]
        assert not any(name.startswith("camera") for name in names)

        consume(device, painted((CAR_SECTOR,)))
        live.refresh()
        assert live.status_label.text().endswith("Läuft")
        assert column_text(live.table, 0, "Runden") == "0"

        consume(device, blank())
        live.refresh()
        consume(device, painted((CAR_IN_ZONE,)))
        live.refresh()
        assert live.status_label.text().endswith("Beendet")
        assert column_text(live.table, 0, "Platz") == "1"
        assert column_text(live.table, 0, "Runden") == "1"
        assert column_text(live.table, 0, "Letzte Runde") != "-"
        assert column_text(live.table, 0, "Gesamtzeit") != "-"
        assert runner.status is RaceStatus.FINISHED

        results = ResultsView(env.runtime.translator, env.races)
        qtbot.addWidget(results)
        results.show_race(race.id)
        assert "Kamera" in results.summary.text()
        assert "Beendet" in results.summary.text()
        assert column_text(results.table, 0, "Platz") == "1"
        assert column_text(results.table, 0, "Fahrer") == "Anna"
        assert column_text(results.table, 0, "Spur") == "1"
        assert column_text(results.table, 0, "Runden") == "1"
        assert column_text(results.table, 0, "Gesamtzeit") != "-"
        lap_row = cells(results.laps_table, 0)
        assert lap_row[2] == "1"
        assert " | " in lap_row[4]
    finally:
        runner.close()
