"""Capture thread, bounded queue and device probe. No real camera is opened."""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from collections.abc import Callable

import pytest

from slot_racing.core.clock import ManualClock
from slot_racing.core.domain import (
    DriverId,
    Participant,
    RaceId,
    RaceStatus,
    TimingLayout,
    TimingSensor,
    TimingSetup,
)
from slot_racing.core.errors import ProviderUnavailable
from slot_racing.core.events import Event, EventBus, LapCompleted, SensorTriggered
from slot_racing.core.timing import TimingSessionSpec
from slot_racing.core.timing_registry import TimingProviderRegistry
from slot_racing.modules.races.engine import RaceConfig, RaceEngine
from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import (
    MAX_FRAMES_PER_POLL,
    MAX_QUEUED_FRAMES,
    CameraClosedError,
    CameraFrameSource,
    CameraOpenError,
    CameraReadError,
    CaptureDevice,
    LatestFrameBuffer,
    _timed,
)
from slot_racing.modules.timing_camera.detection import DetectionZone, DetectorSettings
from slot_racing.modules.timing_camera.frame_source import (
    FrameSource,
    ManualFrameSource,
    TimedFrame,
)
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.lease import CameraBusyError, CameraLease
from slot_racing.modules.timing_camera.opencv_device import OpenCVCapture
from slot_racing.modules.timing_camera.provider import CameraTimingFactory, CameraTimingProvider

WIDTH = 80
HEIGHT = 30


def lane_roi(lane: int, x: int = 30) -> DetectionRoi:
    return DetectionRoi(x, (lane - 1) * 10, 4, 10)


def blank() -> GrayFrame:
    return GrayFrame.blank(WIDTH, HEIGHT)


def car(lane: int, x: int = 28) -> GrayFrame:
    y = (lane - 1) * 10 + 2
    return blank().paint(DetectionRoi(x, y, 8, 6), 255)


def column(lane: int, x: int) -> GrayFrame:
    """One tile column of the 4-pixel zone. A pass is x, then x + 2."""
    return blank().paint(DetectionRoi(x, (lane - 1) * 10, 2, 10), 255)


def session() -> TimingSessionSpec:
    setup = TimingSetup(
        TimingLayout.from_position_ids(["start_finish"]),
        (TimingSensor("sensor-sf", "start_finish"),),
    )
    return TimingSessionSpec(setup, (1,), 1)


def zones(*lanes: int) -> DetectorSettings:
    return DetectorSettings(
        tuple(DetectionZone("start_finish", lane, lane_roi(lane)) for lane in lanes)
    )


class ScriptedCapture:
    """Blocks in ``read`` until a test pushes a frame or closes the device."""

    def __init__(self, *, fail_open: bool = False) -> None:
        self.fail_open = fail_open
        self.opened = False
        self.open_count = 0
        self.close_count = 0
        self.reads = 0
        self._items: deque[GrayFrame | BaseException] = deque()
        self._cond = threading.Condition()

    def open(self) -> None:
        if self.fail_open:
            raise CameraOpenError("device missing")
        with self._cond:
            self.opened = True
            self.open_count += 1

    def push(self, frame: GrayFrame) -> None:
        with self._cond:
            self._items.append(frame)
            self._cond.notify()

    def fail(self, error: BaseException) -> None:
        with self._cond:
            self._items.append(error)
            self._cond.notify()

    def read(self) -> GrayFrame:
        with self._cond:
            while self.opened and not self._items:
                self._cond.wait(timeout=0.2)
            if not self.opened:
                raise CameraClosedError()
            self.reads += 1
            item = self._items.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        with self._cond:
            self.opened = False
            self.close_count += 1
            self._cond.notify_all()

    def wait_until_reads(self, count: int) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.reads >= count:
                return
            time.sleep(0.01)
        raise AssertionError(f"capture delivered {self.reads} frames, expected {count}")


def started(
    capture: ScriptedCapture,
    settings: DetectorSettings | None = None,
    *,
    queue_size: int = MAX_QUEUED_FRAMES,
    max_read_failures: int = 1,
) -> tuple[CameraTimingProvider, CameraFrameSource, list[SensorTriggered]]:
    frames = CameraFrameSource(capture, queue_size=queue_size, max_read_failures=max_read_failures)
    source = CameraTimingFactory(
        frames, settings if settings is not None else zones(1), blank()
    ).create_source(session())
    assert isinstance(source, CameraTimingProvider)
    received: list[SensorTriggered] = []
    source.start(received.append)
    return source, frames, received


def wait_for(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_a_newer_frame_replaces_one_that_detection_has_not_taken() -> None:
    slot = LatestFrameBuffer()
    first = TimedFrame(blank(), 1)
    second = TimedFrame(blank(), 2)
    third = TimedFrame(blank(), 3)
    slot.put(first)
    slot.put(second)
    slot.put(third)
    assert slot.dropped == 2
    assert len(slot) == 1
    assert slot.take() == third
    assert slot.take() is None
    assert len(slot) == 0


def test_a_missing_device_fails_start_and_releases_the_camera() -> None:
    capture = ScriptedCapture(fail_open=True)
    frames = CameraFrameSource(capture)
    source = CameraTimingFactory(frames, zones(1), blank()).create_source(session())
    received: list[SensorTriggered] = []
    with pytest.raises(ProviderUnavailable) as caught:
        source.start(received.append)
    assert caught.value.key == "error.timing_provider.camera_not_connected"
    assert not source.is_running
    assert received == []
    assert not capture.opened
    assert capture.close_count >= 1
    source.stop()
    source.stop()


def test_a_present_device_starts_and_stop_joins_the_thread() -> None:
    capture = ScriptedCapture()
    source, frames, _received = started(capture)
    assert source.is_running
    assert frames.is_capturing
    source.stop()
    source.stop()
    source.stop()
    assert not source.is_running
    assert not frames.is_capturing
    assert frames.queued == 0
    assert capture.close_count >= 1
    assert not capture.opened


def test_grabbed_frames_keep_their_timestamp_until_poll() -> None:
    capture = ScriptedCapture()
    source, _frames, received = started(capture)
    try:
        before = time.perf_counter_ns()
        capture.push(column(1, 30))
        wait_for(lambda: source.frames_observed >= 1)
        capture.push(column(1, 32))
        wait_for(lambda: source.frames_observed >= 2)
        assert received == []
        time.sleep(0.03)
        polled_at = time.perf_counter_ns()
        source.poll()
        assert len(received) == 1
        stamp = received[0].timestamp_ns
        assert before <= stamp < polled_at - 20_000_000
        assert received[0].sensor_id == "sensor-sf"
        assert received[0].position_id == "start_finish"
        assert received[0].lane == 1
        assert received[0].source_id == "camera"
    finally:
        source.stop()


def test_frames_are_processed_in_grab_order() -> None:
    capture = ScriptedCapture()
    source, _frames, received = started(capture, zones(1, 3))
    try:
        for frame, seen in (
            (column(1, 30), 1),
            (column(1, 32), 2),
            (column(3, 30), 3),
            (column(3, 32), 4),
        ):
            capture.push(frame)

            def caught(expected: int = seen) -> bool:
                return source.frames_observed >= expected and source.caught_up()

            wait_for(caught)
        assert received == []
        source.poll()
        assert [event.lane for event in received] == [1, 3]
        assert received[0].timestamp_ns < received[1].timestamp_ns
    finally:
        source.stop()


def test_poll_latest_discards_older_preview_frames_and_stop_ends_the_thread() -> None:
    capture = ScriptedCapture()
    frames = CameraFrameSource(capture)
    frames.start()
    try:
        capture.push(blank())
        capture.push(blank().paint(DetectionRoi(0, 0, 4, 4), 40))
        capture.push(GrayFrame.blank(WIDTH, HEIGHT, 9))
        capture.wait_until_reads(3)
        wait_for(lambda: frames.captured == 3)
        latest = frames.poll_latest()
        assert latest is not None
        assert latest.frame.pixels[0] == 9
        assert frames.queued == 0
        assert frames.dropped == 2
        assert frames.captured == 3
        assert frames.last_read_ns >= 0
    finally:
        frames.stop()
    assert not frames.is_capturing


def test_the_capture_thread_does_not_wait_for_a_consumer() -> None:
    capture = ScriptedCapture()
    frames = CameraFrameSource(capture)
    frames.start()
    try:
        for _ in range(5):
            capture.push(blank())
        capture.wait_until_reads(5)
        wait_for(lambda: frames.captured == 5)
        assert frames.queued <= MAX_QUEUED_FRAMES
        assert frames.dropped == 4
        assert frames.queued == 1
    finally:
        frames.stop()


def test_poll_handles_only_a_limited_number_of_frames() -> None:
    class Burst(FrameSource):
        def __init__(self) -> None:
            self.calls = 0

        def poll_frame(self) -> TimedFrame | None:
            self.calls += 1
            return TimedFrame(blank(), self.calls)

    frames = Burst()
    source = CameraTimingFactory(frames).create_source(session())
    source.start(lambda _event: None)
    source.poll()
    assert frames.calls == MAX_FRAMES_PER_POLL
    source.stop()


def test_pause_frames_are_not_counted_after_resume() -> None:
    capture = ScriptedCapture()
    source, _frames, received = started(capture)
    try:
        source.pause()
        capture.push(car(1))
        capture.wait_until_reads(1)
        source.poll()
        assert received == []
        assert source.frames_observed == 0

        source.resume()
        capture.push(blank())
        wait_for(lambda: source.frames_observed >= 1 and source.caught_up())
        source.poll()
        assert received == []

        capture.push(column(1, 30))
        wait_for(lambda: source.frames_observed >= 2 and source.caught_up())
        capture.push(column(1, 32))
        wait_for(lambda: source.frames_observed >= 3 and source.caught_up())
        source.poll()
        assert [event.lane for event in received] == [1]
    finally:
        source.stop()


def test_a_car_already_in_the_zone_after_resume_is_not_a_new_crossing() -> None:
    capture = ScriptedCapture()
    source, _frames, received = started(capture)
    try:
        capture.push(column(1, 30))
        wait_for(lambda: source.frames_observed >= 1 and source.caught_up())
        capture.push(column(1, 32))
        wait_for(lambda: source.frames_observed >= 2 and source.caught_up())
        source.poll()
        assert len(received) == 1

        source.pause()
        capture.push(car(1))
        capture.wait_until_reads(3)
        source.poll()
        assert len(received) == 1
        assert source.frames_observed == 2

        source.resume()
        capture.push(car(1))
        wait_for(lambda: source.frames_observed >= 3 and source.caught_up())
        source.poll()
        capture.push(car(1))
        wait_for(lambda: source.frames_observed >= 4 and source.caught_up())
        source.poll()
        assert len(received) == 1
    finally:
        source.stop()


def test_a_read_failure_is_reported_when_the_host_polls() -> None:
    capture = ScriptedCapture()
    setup = TimingSetup(
        TimingLayout.from_position_ids(["start_finish"]),
        (TimingSensor("sensor-sf", "start_finish"),),
    )
    frames = CameraFrameSource(capture, max_read_failures=1)
    source = CameraTimingFactory(frames, zones(1), blank()).create_source(
        TimingSessionSpec(setup, (1,), 1)
    )
    bus = EventBus()
    engine = RaceEngine(
        RaceConfig(RaceId(1), 1, (Participant(DriverId(1), 1),), setup.layout),
        bus,
        ManualClock(),
        [source],
    )
    engine.start()
    try:
        capture.fail(CameraReadError("usb unplugged"))
        wait_for(lambda: not frames.is_capturing)
        engine.poll_sources()
        assert "camera" in engine.source_errors
        assert "usb unplugged" in engine.source_errors["camera"]
        assert engine.status is RaceStatus.RUNNING
    finally:
        engine.stop()


def test_capture_frames_complete_a_lap_through_the_race_engine() -> None:
    capture = ScriptedCapture()
    setup = TimingSetup(
        TimingLayout.from_position_ids(["start_finish"]),
        (TimingSensor("sensor-sf", "start_finish"),),
    )
    frames = CameraFrameSource(capture)
    built = CameraTimingFactory(frames, zones(1), blank()).create_source(
        TimingSessionSpec(setup, (1,), 1)
    )
    assert isinstance(built, CameraTimingProvider)
    source = built
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(Event, events.append)
    engine = RaceEngine(
        RaceConfig(RaceId(1), 1, (Participant(DriverId(1), 1),), setup.layout),
        bus,
        ManualClock(),
        [source],
    )
    engine.start()
    try:
        capture.push(column(1, 30))
        wait_for(lambda: source.frames_observed >= 1 and source.caught_up())
        capture.push(column(1, 32))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(
            isinstance(event, LapCompleted) for event in events
        ):
            engine.poll_sources()
            time.sleep(0.01)
        laps = [event for event in events if isinstance(event, LapCompleted)]
        triggered = [event for event in events if isinstance(event, SensorTriggered)]
        assert len(laps) == 1
        assert len(triggered) == 1
        assert triggered[0].sensor_id == "sensor-sf"
        assert triggered[0].timestamp_ns == laps[0].timestamp_ns
        assert engine.status is RaceStatus.FINISHED
    finally:
        engine.close()


def test_a_device_that_opens_is_available_and_closed_again() -> None:
    capture = ScriptedCapture()
    factory = CameraTimingFactory(devices=lambda _config: capture)
    assert factory.availability().available
    assert capture.open_count == 1
    assert capture.close_count == 1
    assert not capture.opened


def test_a_device_that_does_not_open_is_unavailable() -> None:
    capture = ScriptedCapture(fail_open=True)
    factory = CameraTimingFactory(devices=lambda _config: capture)
    assert not factory.availability().available
    assert factory.availability().reason_key == "error.timing_provider.camera_not_connected"
    assert capture.close_count >= 1
    registry = TimingProviderRegistry(lambda: [factory])
    with pytest.raises(ProviderUnavailable) as caught:
        registry.create_source("camera", session())
    assert caught.value.key == "error.timing_provider.camera_not_connected"


def test_a_real_missing_device_index_is_unavailable() -> None:
    """Uses OpenCV, but an index no test machine has, so no camera is required."""
    factory = CameraTimingFactory(camera=CameraConfig(device_index=99))
    first = factory.availability()
    second = factory.availability()
    assert not first.available
    assert first.reason_key == "error.timing_provider.camera_not_connected"
    assert not second.available


def test_the_device_index_is_configurable() -> None:
    seen: list[int] = []

    def devices(config: CameraConfig) -> CaptureDevice:
        seen.append(config.device_index)
        return ScriptedCapture()

    factory = CameraTimingFactory(
        camera=CameraConfig(device_index=2, width=320, height=240, fps=15), devices=devices
    )
    assert factory.availability().available
    assert seen == [2]


class _Image:
    def __init__(self, rows: list[list[int]], *, color: bool = False) -> None:
        self._rows = rows
        self.ndim = 3 if color else 2
        width = len(rows[0])
        self.shape = (len(rows), width, 3) if color else (len(rows), width)

    def reshape(self, size: int) -> list[int]:
        assert size == -1
        return [pixel for row in self._rows for pixel in row]

    def __getitem__(self, key: object) -> _Image:
        if not isinstance(key, tuple) or len(key) < 2:
            raise TypeError("image slices are two-dimensional")
        y_key, x_key = key[0], key[1]
        if not isinstance(y_key, slice) or not isinstance(x_key, slice):
            raise TypeError("image slices are two-dimensional")
        rows = [row[x_key] for row in self._rows[y_key]]
        return _Image(rows, color=self.ndim == 3)


class _FakeCv2:
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    COLOR_BGR2GRAY = 6

    def __init__(
        self, *, opened: bool = True, actual: tuple[float, float, float] = (320, 240, 15)
    ) -> None:
        self.opened = opened
        self.actual = actual
        self.instances: list[_FakeCap] = []
        self.converted = False
        self.converted_shapes: list[tuple[int, ...]] = []
        self.image: _Image | None = _Image([[1, 2], [3, 4]])

    def VideoCapture(self, index: int) -> _FakeCap:  # noqa: N802
        capture = _FakeCap(self, index)
        self.instances.append(capture)
        return capture

    def cvtColor(self, image: _Image, code: int) -> _Image:  # noqa: N802
        assert code == self.COLOR_BGR2GRAY
        self.converted = True
        self.converted_shapes.append(image.shape)
        return _Image(image._rows)


class _FakeCap:
    def __init__(self, api: _FakeCv2, index: int) -> None:
        self.api = api
        self.index = index
        self.released = False
        self.props: dict[int, float] = {}

    def isOpened(self) -> bool:  # noqa: N802
        return self.api.opened and not self.released

    def set(self, prop: int, value: float) -> bool:
        self.props[prop] = value
        return True

    def get(self, prop: int) -> float:
        width, height, fps = self.api.actual
        return {3: width, 4: height, 5: fps}.get(prop, 0)

    def read(self) -> tuple[bool, _Image | None]:
        if self.api.image is None:
            return False, None
        return True, self.api.image

    def release(self) -> None:
        self.released = True


def test_opencv_requests_size_and_fps_and_records_the_driver_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeCv2()
    monkeypatch.setitem(sys.modules, "cv2", api)
    device = OpenCVCapture(CameraConfig(device_index=1, width=640, height=480, fps=30))
    device.open()
    try:
        capture = api.instances[0]
        assert capture.index == 1
        assert capture.props[api.CAP_PROP_FRAME_WIDTH] == 640
        assert capture.props[api.CAP_PROP_FRAME_HEIGHT] == 480
        assert capture.props[api.CAP_PROP_FPS] == 30
        assert capture.props[38] == 1
        assert device.actual_width == 320
        assert device.actual_height == 240
        assert device.actual_fps == 15
        frame = device.read()
        assert (frame.width, frame.height) == (2, 2)
        assert tuple(frame.pixels) == (1, 2, 3, 4)
    finally:
        device.close()
    assert api.instances[0].released


def test_opencv_converts_only_the_zones_once_regions_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [[column for column in range(8)] for _ in range(6)]
    api = _FakeCv2()
    api.image = _Image(rows, color=True)
    monkeypatch.setitem(sys.modules, "cv2", api)
    device = OpenCVCapture(CameraConfig())
    device.open()
    try:
        assert device.read_zoned() is None
        full = device.read()
        assert len(full.pixels) == 8 * 6
        assert api.converted_shapes == [(6, 8, 3)]
        device.set_regions((DetectionRoi(1, 2, 3, 2), DetectionRoi(5, 0, 2, 1)))
        zoned = device.read_zoned()
        assert zoned is not None
        width, height, crops = zoned
        assert (width, height) == (8, 6)
        assert [(crop.width, crop.height) for crop in crops] == [(3, 2), (2, 1)]
        converted = sum(shape[0] * shape[1] for shape in api.converted_shapes[1:])
        assert converted == 3 * 2 + 2 * 1
        assert converted < len(full.pixels)
        assert sum(len(crop.pixels) for crop in crops) == converted
        device.set_regions((DetectionRoi(7, 0, 2, 1),))
        with pytest.raises(ValueError, match="outside"):
            device.read_zoned()
    finally:
        device.close()


def test_grab_queues_zone_crops_instead_of_the_full_picture() -> None:
    device = _RegionDevice()
    source = CameraFrameSource(device)
    full = source._grab()
    assert isinstance(full, GrayFrame)
    assert len(full.pixels) == 8 * 6
    source.use_regions((DetectionRoi(1, 1, 2, 2),))
    crops = source._grab()
    assert isinstance(crops, tuple)
    delivered = _timed(crops, 1)
    assert delivered.crops is not None
    assert len(delivered.crops[0].pixels) == 4
    assert len(delivered.frame.pixels) == 1
    assert device.full_reads == 1
    assert device.zoned_reads == 1


def test_regions_are_taken_from_the_delivered_frame() -> None:
    saved = DetectorSettings((DetectionZone("start_finish", 1, DetectionRoi(40, 10, 20, 10)),))
    frames = _RecordingFrames()
    source = CameraTimingProvider(session(), frames, saved, zone_frame=(100, 50))
    received: list[SensorTriggered] = []
    source.start(received.append)
    try:
        armed = frames.regions
        assert armed is not None and len(armed) == 0
        frames.submit(GrayFrame.blank(50, 25), 1)
        source.poll()
        assert received == []
        armed = frames.regions
        assert armed == (DetectionRoi(20, 5, 10, 5),)
        detector = source._detector
        assert detector is not None
        assert detector.reference_pixels == 2
        assert detector.reference_pixels < 50 * 25
        assert detector.pixels_compared == 0
        left = GrayFrame.blank(10, 5).paint(DetectionRoi(0, 0, 5, 5), 255)
        right = GrayFrame.blank(10, 5).paint(DetectionRoi(5, 0, 5, 5), 255)
        frames.submit(GrayFrame(1, 1, b"\x00"), 2, crops=(left,))
        source.poll()
        assert received == []
        frames.submit(GrayFrame(1, 1, b"\x00"), 3, crops=(right,))
        source.poll()
        assert [(event.position_id, event.lane) for event in received] == [("start_finish", 1)]
        assert detector.pixels_compared == 4
    finally:
        source.stop()
    assert source._detector is None


class _RecordingFrames(ManualFrameSource):
    def __init__(self) -> None:
        super().__init__()
        self.regions: tuple[DetectionRoi, ...] | None = None

    def use_regions(self, regions: tuple[DetectionRoi, ...]) -> None:
        self.regions = regions


class _RegionDevice:
    def __init__(self) -> None:
        self.regions: tuple[DetectionRoi, ...] = ()
        self.full_reads = 0
        self.zoned_reads = 0

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def set_regions(self, regions: tuple[DetectionRoi, ...]) -> None:
        self.regions = regions

    def read(self) -> GrayFrame:
        self.full_reads += 1
        return GrayFrame.blank(8, 6, 3)

    def read_zoned(self) -> tuple[int, int, tuple[GrayFrame, ...]] | None:
        if not self.regions:
            return None
        self.zoned_reads += 1
        return (8, 6, (GrayFrame.blank(2, 2, 9),))


def test_opencv_converts_color_frames_to_gray(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _FakeCv2()
    api.image = _Image([[9, 8], [7, 6]], color=True)
    monkeypatch.setitem(sys.modules, "cv2", api)
    device = OpenCVCapture(CameraConfig())
    device.open()
    try:
        frame = device.read()
    finally:
        device.close()
    assert api.converted
    assert tuple(frame.pixels) == (9, 8, 7, 6)


def test_opencv_missing_device_is_released(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _FakeCv2(opened=False)
    monkeypatch.setitem(sys.modules, "cv2", api)
    device = OpenCVCapture(CameraConfig(device_index=4))
    with pytest.raises(CameraOpenError):
        device.open()
    assert api.instances[0].index == 4
    assert api.instances[0].released
    device.close()


def test_opencv_reports_nothing_when_the_driver_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeCv2(actual=(0, 0, 0))
    monkeypatch.setitem(sys.modules, "cv2", api)
    device = OpenCVCapture(CameraConfig())
    device.open()
    device.close()
    assert device.actual_width is None
    assert device.actual_height is None
    assert device.actual_fps is None


def test_the_preview_cannot_open_the_camera_while_a_race_holds_it() -> None:
    lease = CameraLease()
    race_device = ScriptedCapture()
    preview_device = ScriptedCapture()
    race = CameraFrameSource(race_device, lease=lease, lease_owner=CameraLease.RACE)
    preview = CameraFrameSource(preview_device, lease=lease, lease_owner=CameraLease.PREVIEW)
    race.start()
    try:
        with pytest.raises(CameraBusyError):
            preview.start()
        assert not preview_device.opened
        assert lease.holder() == CameraLease.RACE
    finally:
        race.stop()
    preview.start()
    try:
        assert preview_device.opened
        assert lease.holder() == CameraLease.PREVIEW
    finally:
        preview.stop()
    preview.stop()
    assert lease.holder() is None


def test_a_held_camera_is_reported_in_use_without_opening_it() -> None:
    lease = CameraLease()
    assert lease.try_acquire(CameraLease.PREVIEW)
    opened: list[str] = []

    class _Device:
        def open(self) -> None:
            opened.append("open")

        def close(self) -> None:
            opened.append("close")

        def read(self) -> GrayFrame:
            raise CameraClosedError()

    factory = CameraTimingFactory(settings=zones(1), devices=lambda _config: _Device(), lease=lease)
    availability = factory.availability()
    assert not availability.available
    assert availability.reason_key == "error.timing_provider.camera_in_use"
    assert opened == []
    source = factory.create_source(session())
    with pytest.raises(ProviderUnavailable) as caught:
        source.start(lambda _event: None)
    assert caught.value.key == "error.timing_provider.camera_in_use"
    assert "open" not in opened
    source.stop()
