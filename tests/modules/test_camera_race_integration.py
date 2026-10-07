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
from slot_racing.core.timing_registry import TimingProviderRegistry
from slot_racing.modules.races.engine import SAME_POSITION_DEBOUNCE_NS
from slot_racing.modules.races.runner import RaceRunner
from slot_racing.modules.races.ui.live_view import LiveRaceView
from slot_racing.modules.races.ui.races_page import RacesPage
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
from slot_racing.modules.timing_camera.detection import TravelDirection, effective_block_size
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.lease import CameraBusyError, CameraLease
from slot_racing.modules.timing_camera.preview import CameraPreview
from slot_racing.modules.timing_camera.provider import CameraTimingFactory
from slot_racing.modules.timing_camera.store import (
    CAMERA_CONFIGURATION_KEY,
    CameraConfigurationStore,
)
from slot_racing.modules.timing_camera.ui.stage import CameraStage
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


def zone_rect(
    x: float,
    y: float = 0.0,
    width: float = 0.10,
    height: float = 0.25,
    frame_width: int = WIDTH,
    frame_height: int = HEIGHT,
) -> DetectionRoi:
    return roi_to_pixels(
        NormalizedRoi(x=x, y=y, width=width, height=height),
        frame_width,
        frame_height,
    )


def cross(
    device: SyncCapture,
    *rois: DetectionRoi,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> None:
    """Queue a left-to-right pass of whole tiles, one frame at a time.

    The painted group is large enough for the default sensitivity and then
    shifts by one tile, so a half-zone that still covers the same blocks is
    not used.
    """
    lefts: list[DetectionRoi] = []
    rights: list[DetectionRoi] = []
    for roi in rois:
        block = effective_block_size(20, roi.width, roi.height, TravelDirection.LEFT_TO_RIGHT)
        rows = max(1, roi.height // block)
        cols = max(1, roi.width // block)
        cells = rows * cols
        required = 1 if cells <= 2 else min(3, max(2, cells // 3))
        used = max(1, (required + rows - 1) // rows)
        span_w = used * block
        span_h = rows * block
        lefts.append(DetectionRoi(roi.x, roi.y, span_w, span_h))
        rights.append(DetectionRoi(roi.x + block, roi.y, span_w, span_h))
    consume(device, painted(tuple(lefts), width, height))
    consume(device, painted(tuple(rights), width, height))


def pass_again(
    device: SyncCapture,
    runner: RaceRunner,
    events: list[Event],
    *rois: DetectionRoi,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> None:
    """Clear the line and cross it once more, after the same-position debounce.

    The first start/finish crossing starts the lap clock. This one stores the lap.
    """
    last = [event.timestamp_ns for event in events if isinstance(event, SensorTriggered)]
    if last:
        remain = SAME_POSITION_DEBOUNCE_NS - (time.perf_counter_ns() - last[-1])
        if remain > 0:
            time.sleep(remain / 1_000_000_000 + 0.02)
    consume(device, blank(width, height))
    drive(runner)
    cross(device, *rois, width=width, height=height)
    drive(runner)


def camera_threads() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == "slot-racing-camera" and thread.is_alive()
    ]


def drive(runner: RaceRunner) -> None:
    """Wait until camera detection has finished, then forward its events.

    The worker detects without this call. ``tick`` only delivers crossings that
    are already stored, on the test thread, the same way the live view does.
    """
    engine = getattr(runner, "_engine", None)
    sources = () if engine is None else getattr(engine, "_sources", ())
    for source in sources:
        caught_up = getattr(source, "caught_up", None)
        if callable(caught_up):
            wait_until(caught_up, "camera detection did not catch up")
    runner.tick()


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
    # The capture thread has published this frame and is blocked in the next
    # read. Detection has to take it before the next push replaces it.
    time.sleep(0.02)


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
        drive(runner)
        assert of_type(events, SensorTriggered) == []

        consume(device, painted((CAR_OUTSIDE,)))
        drive(runner)
        assert of_type(events, SensorTriggered) == []

        before = time.perf_counter_ns()
        cross(device, zone_rect(ZONE_X))
        after_capture = time.perf_counter_ns()
        drive(runner)

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
        assert of_type(events, LapCompleted) == []
        pass_again(device, runner, events, zone_rect(ZONE_X))
        crossings = of_type(events, SensorTriggered)
        laps = of_type(events, LapCompleted)
        assert len(laps) == 1
        assert laps[0].lap_number == 1
        assert laps[0].lane == 1
        assert laps[0].lap_time_ns == crossings[-1].timestamp_ns - crossings[0].timestamp_ns
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
        drive(runner)
        save_document(
            env,
            (zone("start_finish", 1, x=0.0),),
            StoredCamera(device_index=9, width=WIDTH, height=HEIGHT, fps=12),
        )
        assert CameraConfigurationStore(env.runtime.database).load().camera.device_index == 9

        consume(device, painted((CAR_OUTSIDE,)))
        drive(runner)
        assert of_type(events, SensorTriggered) == []

        cross(device, zone_rect(ZONE_X))
        drive(runner)
        triggered = of_type(events, SensorTriggered)
        assert [event.position_id for event in triggered] == ["start_finish"]
        pass_again(device, runner, events, zone_rect(ZONE_X))
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
        drive(first)
        cross(device, zone_rect(ZONE_X))
        drive(first)
        pass_again(device, first, first_events, zone_rect(ZONE_X))
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
        drive(second)
        consume(device, painted((CAR_IN_ZONE,)))
        drive(second)
        assert of_type(second_events, SensorTriggered) == []
        cross(device, zone_rect(0.0))
        drive(second)
        triggered = of_type(second_events, SensorTriggered)
        assert [(event.position_id, event.lane, event.sensor_id) for event in triggered] == [
            ("start_finish", 1, "sensor-start_finish")
        ]
        pass_again(device, second, second_events, zone_rect(0.0))
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
        drive(runner)
        before = time.perf_counter_ns()
        cross(device, zone_rect(ZONE_X), zone_rect(ZONE_X, y=0.50))
        after_capture = time.perf_counter_ns()
        drive(runner)

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
        pass_again(device, runner, events, zone_rect(ZONE_X), zone_rect(ZONE_X, y=0.50))
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
        drive(runner)
        before = time.perf_counter_ns()
        cross(device, zone_rect(ZONE_X), zone_rect(0.50), zone_rect(0.80))
        after_capture = time.perf_counter_ns()
        drive(runner)

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
        drive(runner)
        cross(device, zone_rect(ZONE_X), zone_rect(0.80))
        drive(runner)
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
        drive(runner)
        assert of_type(events, SensorTriggered) == []

        runner.pause()
        assert runner.status is RaceStatus.PAUSED
        reads_before = device.reads
        consume(device, painted((CAR_IN_ZONE,)))
        drive(runner)
        assert device.reads == reads_before + 1
        assert of_type(events, SensorTriggered) == []

        runner.resume()
        assert runner.snapshot().status is RaceStatus.RUNNING
        consume(device, painted((zone_rect(ZONE_X),)))
        drive(runner)
        assert of_type(events, SensorTriggered) == []

        consume(device, blank())
        drive(runner)
        assert of_type(events, SensorTriggered) == []

        before = time.perf_counter_ns()
        cross(device, zone_rect(ZONE_X))
        after_capture = time.perf_counter_ns()
        drive(runner)
        triggered = of_type(events, SensorTriggered)
        assert len(triggered) == 1
        assert before <= triggered[0].timestamp_ns <= after_capture
        assert triggered[0].position_id == "start_finish"
        pass_again(device, runner, events, zone_rect(ZONE_X))
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
        drive(runner)
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
    assert MAX_QUEUED_FRAMES == 1
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
        drive(runner)
        # A repeated car in the same blocks is not a crossing. The slot also
        # keeps only the newest unread frame, so a burst cannot replay history.
        for _ in range(4):
            consume(device, painted((CAR_IN_ZONE,)))
        consume(device, blank())
        consume(device, blank())
        drive(runner)
        assert of_type(events, SensorTriggered) == []
        assert runner.status is RaceStatus.RUNNING

        cross(device, zone_rect(ZONE_X))
        drive(runner)
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
        drive(runner)
        snapshot = runner.snapshot()
        assert runner.status is RaceStatus.RUNNING
        assert snapshot.status is RaceStatus.RUNNING
        assert any(
            "CameraReadError" in message and "no frame" in message
            for message in snapshot.source_errors
        )
        assert not any("camera_not_connected" in message for message in snapshot.source_errors)
        drive(runner)
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
        drive(runner)
        consume(device, painted((DetectionRoi(0, 0, 4, 4),), width, height))
        drive(runner)
        assert of_type(events, SensorTriggered) == []
        cross(device, expected, width=width, height=height)
        drive(runner)
        triggered = of_type(events, SensorTriggered)
        assert len(triggered) == 1
        assert triggered[0].position_id == "start_finish"
        pass_again(device, runner, events, expected, width=width, height=height)
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
        assert not live.start_lights.isVisible()
        assert live.findChildren(CameraStage) == []
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

        cross(device, zone_rect(0.50))
        live.refresh()
        assert live.status_label.text().endswith("Läuft")
        assert column_text(live.table, 0, "Runden") == "0"

        consume(device, blank())
        live.refresh()
        cross(device, zone_rect(ZONE_X))
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


@pytest.mark.parametrize(
    ("stored_width", "stored_height", "frame_width", "frame_height"),
    [
        (1280, 720, 640, 480),
        (640, 480, 1920, 1080),
        (800, 600, 320, 240),
    ],
)
def test_start_finish_uses_the_delivered_frame_instead_of_the_request(
    env: Env,
    stored_width: int,
    stored_height: int,
    frame_width: int,
    frame_height: int,
) -> None:
    """A smaller or larger picture must not reject lane 1 or move the zone."""
    normalized = NormalizedRoi(x=0.80, y=0.10, width=0.15, height=0.20)
    reference = roi_to_pixels(normalized, stored_width, stored_height)
    actual = roi_to_pixels(normalized, frame_width, frame_height)
    if frame_width < stored_width or frame_height < stored_height:
        outside = (
            reference.x + reference.width > frame_width
            or reference.y + reference.height > frame_height
        )
        assert outside
    assert actual != reference
    assert actual.x + actual.width <= frame_width
    assert actual.y + actual.height <= frame_height
    hub = DeviceHub()
    save_document(
        env,
        (
            zone("start_finish", 1, x=0.80, y=0.10, width=0.15, height=0.20),
            zone("start_finish", 2, x=0.80, y=0.55, width=0.15, height=0.20),
        ),
        StoredCamera(device_index=4, width=stored_width, height=stored_height, fps=12),
    )
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1)
    events = listen(env)
    runner = env.controller.start_race(race_id)
    try:
        device = hub.live()
        consume(device, blank(frame_width, frame_height))
        drive(runner)
        assert runner.snapshot().source_errors == ()
        assert of_type(events, SensorTriggered) == []
        assert actual.x + actual.width <= frame_width
        assert actual.y + actual.height <= frame_height
        cross(device, actual, width=frame_width, height=frame_height)
        drive(runner)
        snapshot = runner.snapshot()
        assert snapshot.source_errors == ()
        triggered = of_type(events, SensorTriggered)
        assert [(event.position_id, event.lane) for event in triggered] == [("start_finish", 1)]
        pass_again(device, runner, events, actual, width=frame_width, height=frame_height)
        assert of_type(events, LapCompleted)
        assert runner.status is RaceStatus.FINISHED
    finally:
        runner.close()


def engine_status(runner: RaceRunner) -> RaceStatus:
    """Read the engine status. A direct ``is`` check would stick for mypy."""
    return runner.status


def test_a_camera_race_starts_on_go_and_ignores_the_countdown(qtbot: QtBot, env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1)
    events = listen(env)
    live = LiveRaceView(env.runtime.translator, env.controller, service=env.races)
    live.cue_interval_ms = 60_000
    live.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    qtbot.addWidget(live)
    runner = env.controller.prepare_race(race_id)
    try:
        live.show()
        live.open_for_start(runner)
        assert live.start_lights.lit_lights == 1
        assert live.start_lights.isVisible()
        assert not live.start_lights.showing_go
        assert runner.status is RaceStatus.CREATED
        assert not any(device.opened for device in hub.created)
        env.clock.advance(5_000_000_000)
        live.refresh()
        assert runner.snapshot().elapsed_ns == 0
        assert of_type(events, LapCompleted) == []
        for lit in (2, 3, 4, 5):
            live.advance_start_cue()
            assert live.start_lights.lit_lights == lit
            assert runner.status is RaceStatus.CREATED
            assert runner.snapshot().elapsed_ns == 0
            assert of_type(events, LapCompleted) == []
        live.advance_start_cue()
        assert live.start_lights.lit_lights == 0
        assert live.start_lights.showing_go
        assert live.start_lights.isVisible()
        assert engine_status(runner) is RaceStatus.RUNNING
        env.clock.advance(2_000_000_000)
        live.refresh()
        assert runner.snapshot().elapsed_ns == 2_000_000_000
        device = hub.live()
        consume(device, blank())
        drive(runner)
        cross(device, zone_rect(ZONE_X))
        drive(runner)
        assert runner.snapshot().source_errors == ()
        pass_again(device, runner, events, zone_rect(ZONE_X))
        assert of_type(events, LapCompleted)
        assert live.findChildren(CameraStage) == []
        live.advance_start_cue()
        assert not live.start_lights.isVisible()
    finally:
        live.advance_start_cue()
        runner.close()


def test_the_races_page_counts_down_only_for_a_camera_race(qtbot: QtBot, env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1)
    page = RacesPage(
        env.runtime.translator,
        env.races,
        env.controller,
        env.drivers,
        env.vehicles,
        env.tracks,
        env.runtime.services.get(TimingProviderRegistry),
    )
    page.live.cue_interval_ms = 60_000
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    qtbot.addWidget(page)
    page.show()
    try:
        assert page.start_race(race_id)
        assert page.stack.currentWidget() is page.live
        assert page.live.start_lights.lit_lights == 1
        assert page.live.start_lights.isVisible()
        assert not page.live.start_lights.showing_go
        active = env.controller.active
        assert active is not None
        assert active.status is RaceStatus.CREATED
        assert active.snapshot().elapsed_ns == 0
        assert not any(device.opened for device in hub.created)
    finally:
        page.live.advance_start_cue()
        if env.controller.active is not None:
            env.controller.active.close()


def test_three_and_four_lanes_each_report_a_crossing(env: Env) -> None:
    for lane_count in (3, 4):
        hub = DeviceHub()
        height = 0.2
        zones = tuple(
            zone("start_finish", lane, x=ZONE_X, y=(lane - 1) * 0.25, height=height)
            for lane in range(1, lane_count + 1)
        )
        save_document(env, zones)
        attach(env, hub)
        race_id, _track = create_camera_race(env, lanes=lane_count, name=f"Lanes {lane_count}")
        events = listen(env)
        runner = env.controller.start_race(race_id)
        try:
            device = hub.live()
            consume(device, blank())
            drive(runner)
            cross(
                device,
                *(
                    zone_rect(ZONE_X, y=(lane - 1) * 0.25, height=height)
                    for lane in range(1, lane_count + 1)
                ),
            )
            drive(runner)
            triggered = of_type(events, SensorTriggered)
            assert [event.lane for event in triggered] == list(range(1, lane_count + 1))
            assert len({event.timestamp_ns for event in triggered}) == 1
            pass_again(
                device,
                runner,
                events,
                *(
                    zone_rect(ZONE_X, y=(lane - 1) * 0.25, height=height)
                    for lane in range(1, lane_count + 1)
                ),
            )
            assert runner.status is RaceStatus.FINISHED
        finally:
            runner.close()
        env.runtime.services.remove_owner("camera-e2e")


def test_a_camera_time_trial_keeps_running_after_a_lap_until_it_is_stopped(env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    track = env.track(name="Training", lanes=2)
    save_layout(env, track.id, ("start_finish",))
    race = env.races.create_time_trial("Zeitfahren", track.id, "camera")
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
        pass_again(device, runner, events, zone_rect(ZONE_X))
        assert len(of_type(events, LapCompleted)) == 1
        assert engine_status(runner) is RaceStatus.RUNNING
        assert env.races.require_race(race.id).status is RaceStatus.RUNNING
        runner.stop()
        assert engine_status(runner) is RaceStatus.FINISHED
        assert runner.snapshot().aborted is False
        assert env.races.require_race(race.id).status is RaceStatus.FINISHED
    finally:
        runner.close()


def test_an_aborted_camera_race_can_be_restarted_without_its_old_laps(env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1, name="Abbruch")
    events = listen(env)
    runner = env.controller.start_race(race_id)
    try:
        device = hub.live()
        consume(device, blank())
        drive(runner)
        runner.stop()
        assert runner.snapshot().aborted is True
        assert env.races.require_race(race_id).status is RaceStatus.ABORTED
        assert of_type(events, LapCompleted) == []
    finally:
        runner.close()

    restarted = env.races.restart_aborted(race_id)
    assert restarted.id != race_id
    assert env.races.require_race(race_id).status is RaceStatus.ABORTED
    again = env.controller.start_race(restarted.id)
    try:
        device = hub.live()
        consume(device, blank())
        drive(again)
        cross(device, zone_rect(ZONE_X))
        drive(again)
        pass_again(device, again, events, zone_rect(ZONE_X))
        assert len(of_type(events, LapCompleted)) == 1
        assert again.status is RaceStatus.FINISHED
        assert env.races.require_race(restarted.id).status is RaceStatus.FINISHED
        assert env.races.require_race(race_id).status is RaceStatus.ABORTED
    finally:
        again.close()


def test_hiding_the_live_view_during_the_lights_does_not_start_the_race(
    qtbot: QtBot, env: Env
) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1)
    live = LiveRaceView(env.runtime.translator, env.controller, service=env.races)
    live.cue_interval_ms = 40
    live.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    qtbot.addWidget(live)
    runner = env.controller.prepare_race(race_id)
    live.show()
    live.open_for_start(runner)
    live.advance_start_cue()
    assert live.start_lights.lit_lights == 2
    live.hide()
    qtbot.wait(400)
    assert engine_status(runner) is RaceStatus.CREATED
    assert runner.snapshot().elapsed_ns == 0
    assert not live.start_lights.isVisible()
    assert not any(device.opened for device in hub.created)
    live.show()
    live.open_for_start(runner)
    assert live.start_lights.lit_lights == 1
    assert not live.start_lights.showing_go
    assert engine_status(runner) is RaceStatus.CREATED
    runner.close()


def test_closing_the_live_view_during_the_lights_does_not_start_the_race(
    qtbot: QtBot, env: Env
) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1, name="Schließen")
    live = LiveRaceView(env.runtime.translator, env.controller, service=env.races)
    live.cue_interval_ms = 40
    live.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    runner = env.controller.prepare_race(race_id)
    live.show()
    live.open_for_start(runner)
    live.close()
    qtbot.wait(400)
    assert engine_status(runner) is RaceStatus.CREATED
    assert not any(device.opened for device in hub.created)
    live.deleteLater()
    qtbot.wait(50)
    runner.close()


def test_a_restarted_camera_race_begins_on_the_first_lamp(qtbot: QtBot, env: Env) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    race_id, _track = create_camera_race(env, lanes=1, name="Neu")
    live = LiveRaceView(env.runtime.translator, env.controller, service=env.races)
    live.cue_interval_ms = 60_000
    live.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    qtbot.addWidget(live)
    runner = env.controller.prepare_race(race_id)
    live.show()
    try:
        live.open_for_start(runner)
        for _ in range(5):
            live.advance_start_cue()
        assert live.start_lights.showing_go
        assert engine_status(runner) is RaceStatus.RUNNING
        runner.stop()
        assert runner.snapshot().aborted
        restarted = env.races.restart_aborted(race_id)
        again = env.controller.prepare_race(restarted.id)
        live.open_for_start(again)
        assert live.start_lights.lit_lights == 1
        assert not live.start_lights.showing_go
        assert engine_status(again) is RaceStatus.CREATED
        assert env.races.require_race(race_id).status is RaceStatus.ABORTED
        for lit in (2, 3, 4, 5):
            live.advance_start_cue()
            assert live.start_lights.lit_lights == lit
            assert engine_status(again) is RaceStatus.CREATED
        live.advance_start_cue()
        assert live.start_lights.showing_go
        assert live.start_lights.lit_lights == 0
        assert engine_status(again) is RaceStatus.RUNNING
    finally:
        if env.controller.active is not None:
            env.controller.active.close()


def test_lap_and_time_trial_races_both_wait_for_the_lights_to_go_out(
    qtbot: QtBot, env: Env
) -> None:
    hub = DeviceHub()
    save_document(env, (zone("start_finish", 1, x=ZONE_X),))
    attach(env, hub)
    live = LiveRaceView(env.runtime.translator, env.controller, service=env.races)
    live.cue_interval_ms = 60_000
    live.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    qtbot.addWidget(live)
    live.show()
    lap_id, _track = create_camera_race(env, lanes=1, name="Runden")
    track = env.track(name="Zeit", lanes=2)
    save_layout(env, track.id, ("start_finish",))
    open_trial = env.races.create_time_trial("Offen", track.id, "camera")
    timed = env.races.create_time_trial("Dauer", track.id, "camera", duration_minutes=12)
    for race_id in (open_trial.id, timed.id):
        driver_id, vehicle_id = env.pair(1)
        env.races.add_participant(race_id, driver_id, vehicle_id, 1)
    try:
        for race_id in (lap_id, open_trial.id, timed.id):
            runner = env.controller.prepare_race(race_id)
            live.open_for_start(runner)
            assert live.start_lights.lit_lights == 1
            assert engine_status(runner) is RaceStatus.CREATED
            assert runner.snapshot().elapsed_ns == 0
            for lit in (2, 3, 4, 5):
                live.advance_start_cue()
                assert live.start_lights.lit_lights == lit
                assert engine_status(runner) is RaceStatus.CREATED
            live.advance_start_cue()
            assert live.start_lights.showing_go
            assert engine_status(runner) is RaceStatus.RUNNING
            if race_id == timed.id:
                runner.tick()
                assert engine_status(runner) is RaceStatus.RUNNING
            runner.stop()
            runner.close()
            live.advance_start_cue()
            assert not live.start_lights.isVisible()
    finally:
        if env.controller.active is not None:
            env.controller.active.close()
