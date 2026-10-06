"""Camera lifecycle and frame pipeline. No physical camera is opened."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import ClassVar

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from slot_racing.app.main_window import MainWindow
from slot_racing.app.runtime import Runtime
from slot_racing.core.config import AppConfig
from slot_racing.core.storage import Database
from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import (
    CameraClosedError,
    CameraFrameSource,
    CameraReadError,
    LatestFrameBuffer,
)
from slot_racing.modules.timing_camera.detection import create_lane_detector
from slot_racing.modules.timing_camera.diagnostic import CaptureCounters
from slot_racing.modules.timing_camera.frame_source import TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.lease import CameraLease
from slot_racing.modules.timing_camera.preview import CameraPreview
from slot_racing.modules.timing_camera.provider import CameraTimingFactory, CameraTimingProvider
from slot_racing.modules.timing_camera.session import CameraSession
from slot_racing.modules.timing_camera.store import CameraConfigurationStore
from slot_racing.modules.timing_camera.ui.page import CameraSetupPage
from tests.modules.test_camera_capture import blank, session, zones
from tests.modules.test_camera_configuration import database
from tests.modules.test_camera_diagnostic import _session as diagnostic_session
from tests.modules.test_camera_setup_ui import saved_configuration, translator

WIDTH = 80
HEIGHT = 30
CAMERA = CameraConfig(device_index=1, width=WIDTH, height=HEIGHT, fps=25)
OTHER = CameraConfig(device_index=2, width=WIDTH, height=HEIGHT, fps=25)


class Device:
    """Blocks in ``read`` until a frame is pushed or the device is closed."""

    def __init__(self, *, open_delay_s: float = 0.0, close_delay_s: float = 0.0) -> None:
        self.open_delay_s = open_delay_s
        self.close_delay_s = close_delay_s
        self.opened = False
        self.open_count = 0
        self.close_count = 0
        self.reads = 0
        self.failures = 0
        self._items: list[GrayFrame | BaseException] = []
        self._cond = threading.Condition()

    def open(self) -> None:
        if self.open_delay_s:
            time.sleep(self.open_delay_s)
        with self._cond:
            self.opened = True
            self.open_count += 1

    def close(self) -> None:
        if self.close_delay_s:
            time.sleep(self.close_delay_s)
        with self._cond:
            self.opened = False
            self.close_count += 1
            self._cond.notify_all()

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
            item = self._items.pop(0)
        if isinstance(item, BaseException):
            self.failures += 1
            raise item
        return item

    def wait_reads(self, count: int) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.reads >= count:
                return
            time.sleep(0.005)
        raise AssertionError(f"reads={self.reads}, expected {count}")


class Hub:
    def __init__(self, *, open_delay_s: float = 0.0, close_delay_s: float = 0.0) -> None:
        self.open_delay_s = open_delay_s
        self.close_delay_s = close_delay_s
        self.created: list[Device] = []

    def __call__(self, config: CameraConfig) -> Device:
        if not isinstance(config, CameraConfig):
            raise TypeError("config must be a CameraConfig")
        device = Device(open_delay_s=self.open_delay_s, close_delay_s=self.close_delay_s)
        self.created.append(device)
        return device


def make_session(
    hub: Hub | None = None,
) -> tuple[CameraSession, Hub]:
    devices = hub if hub is not None else Hub()
    return CameraSession(CameraLease(), devices=devices), devices


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


def test_navigation_does_not_open_or_close_the_camera(qtbot: QtBot) -> None:
    capture, hub = make_session()
    preview = CameraPreview(CameraLease(), session=capture)
    page = CameraSetupPage(
        translator(),
        CameraConfigurationStore(database()),
        preview,
    )
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page.show()
    qtbot.addWidget(page)
    factory = CameraTimingFactory(camera=CAMERA, session=capture)
    assert capture.open_count == 1
    assert hub.created[0].open_count == 1
    assert len(camera_threads()) == 1
    for _ in range(20):
        page.hide()
        assert factory.availability().available
        page.show()
    assert capture.open_count == 1
    assert capture.close_count == 0
    assert hub.created[0].open_count == 1
    assert hub.created[0].close_count == 0
    assert len(hub.created) == 1
    assert len(camera_threads()) == 1
    hidden = [event for event in capture.events() if event.reason == "page_hidden"]
    assert hidden
    assert {event.action for event in hidden} == {"camera_consumer_detached"}
    closed_on_hide = [
        event
        for event in capture.events()
        if event.action == "camera_closed" and event.reason == "page_hidden"
    ]
    assert closed_on_hide == []
    started = time.perf_counter()
    capture.close(reason="shutdown")
    assert time.perf_counter() - started < 0.5
    assert capture.close_count == 1
    assert hub.created[0].close_count >= 1
    assert camera_threads() == []


def test_open_and_close_block_the_caller_and_navigation_does_not() -> None:
    capture, hub = make_session(Hub(open_delay_s=0.2, close_delay_s=0.2))
    started = time.perf_counter()
    capture.ensure(CAMERA, reason="preview")
    opened = time.perf_counter() - started
    assert opened >= 0.2
    assert capture.last_open_ns >= 150_000_000
    started = time.perf_counter()
    for _ in range(20):
        capture.attach("preview", reason="page_shown")
        capture.detach("preview", reason="page_hidden")
    assert time.perf_counter() - started < 0.15
    assert capture.open_count == 1
    assert hub.created[0].open_count == 1
    started = time.perf_counter()
    capture.close(reason="shutdown")
    closed = time.perf_counter() - started
    assert closed >= 0.2
    assert capture.last_close_ns >= 150_000_000
    assert capture.last_join_ns < 200_000_000
    assert camera_threads() == []


def test_preview_comes_and_goes_without_a_second_open() -> None:
    capture, hub = make_session()
    first = capture.open_preview(CAMERA)
    assert first.poll_latest() is None
    first.stop()
    second = capture.open_preview(CAMERA)
    try:
        assert capture.open_count == 1
        assert capture.close_count == 0
        assert len(hub.created) == 1
        assert hub.created[0].open_count == 1
        actions = [event.action for event in capture.events()]
        assert actions.count("camera_open") == 1
        assert "camera_consumer_detached" in actions
        assert "camera_consumer_attached" in actions
    finally:
        second.stop()
        capture.close(reason="shutdown")


def test_diagnosis_uses_the_open_capture_and_drops_its_detector(qtbot: QtBot) -> None:
    capture, hub = make_session()
    preview = CameraPreview(CameraLease(), session=capture)
    store = CameraConfigurationStore(database())
    store.save(saved_configuration())
    page = CameraSetupPage(translator(), store, preview)
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page.show()
    qtbot.addWidget(page)
    page.diagnostic.click()
    from slot_racing.modules.timing_camera.ui.diagnostic_dialog import DetectionDiagnosticDialog

    dialog = page.findChild(DetectionDiagnosticDialog)
    assert isinstance(dialog, DetectionDiagnosticDialog)
    dialog.start_diagnosis()
    try:
        assert len(hub.created) == 1
        assert capture.open_count == 1
        device = hub.created[0]
        device.push(GrayFrame.blank(640, 480, 20))
        device.wait_reads(1)
        assert dialog._session is not None
        wait_until(
            lambda: "capture_seq=1" in dialog.log_text(),
            "diagnostic did not receive the captured frame",
        )
        dialog.stop_diagnosis()
        assert dialog._session is not None
        assert not dialog._session.running
        assert capture.is_open
        assert capture.open_count == 1
        assert capture.close_count == 0
    finally:
        dialog.close()
        capture.close(reason="shutdown")


def test_a_race_builds_a_fresh_detector_and_keeps_the_camera() -> None:
    capture, hub = make_session()
    capture.ensure(CAMERA, reason="preview")
    factory = CameraTimingFactory(camera=CAMERA, settings=zones(1), session=capture, devices=hub)
    spec = session()
    first = factory.create_source(spec)
    assert isinstance(first, CameraTimingProvider)
    received: list[object] = []
    first.start(received.append)
    try:
        assert capture.open_count == 1
        assert len(hub.created) == 1
        device = hub.created[0]
        device.push(blank())
        wait_until(lambda: first.frames_observed >= 1, "first race saw no frame")
        first.poll()
        detector = first._detector
        assert detector is not None
    finally:
        first.stop()
    assert first._detector is None
    assert capture.is_open
    assert capture.open_count == 1
    assert capture.close_count == 0
    second = factory.create_source(spec)
    assert isinstance(second, CameraTimingProvider)
    second.start(received.append)
    try:
        assert second._detector is not None
        assert second._detector is not detector
        assert capture.open_count == 1
        assert len(hub.created) == 1
        assert len(camera_threads()) == 1
    finally:
        second.stop()
        capture.close(reason="shutdown")
    assert capture.close_count == 1
    assert camera_threads() == []


def test_switching_cameras_closes_the_old_one_once() -> None:
    capture, hub = make_session()
    capture.ensure(CAMERA, reason="preview")
    capture.ensure(OTHER, reason="device_changed")
    try:
        assert len(hub.created) == 2
        assert hub.created[0].open_count == 1
        assert hub.created[0].close_count >= 1
        assert not hub.created[0].opened
        assert hub.created[1].open_count == 1
        assert hub.created[1].opened
        assert capture.open_count == 2
        assert capture.close_count == 1
        assert len(camera_threads()) == 1
    finally:
        capture.close(reason="shutdown")
    assert len(camera_threads()) == 0


def test_a_read_failure_stops_once_and_does_not_reopen() -> None:
    capture, hub = make_session()
    capture.ensure(CAMERA, reason="preview")
    device = hub.created[0]
    for _ in range(3):
        device.fail(OSError("unplugged"))
    wait_until(lambda: not capture.is_open, "capture kept running after read failures")
    with pytest.raises(CameraReadError):
        capture.check()
    assert capture.open_count == 1
    assert capture.close_count == 1
    with pytest.raises(CameraReadError):
        capture.check()
    assert capture.open_count == 1
    assert len(hub.created) == 1
    assert camera_threads() == []
    assert any(event.reason == "read_failure" for event in capture.events())


def test_a_fast_consumer_receives_every_sequence_and_a_slow_one_keeps_the_latest() -> None:
    clock = iter(range(0, 1_000_000_000, 40_000_000))

    def stamps() -> int:
        return next(clock)

    hub = Hub()
    frames = CameraFrameSource(hub(CAMERA), clock=stamps)
    frames.start()
    fast = frames.subscribe()
    try:
        seen: list[int] = []

        def consume() -> None:
            stop = threading.Event()
            while len(seen) < 5:
                frame = fast.wait(stop)
                assert frame is not None
                time.sleep(0.003)
                seen.append(frame.sequence)
                fast.ack()

        worker = threading.Thread(target=consume)
        worker.start()
        device = hub.created[0]
        for index in range(1, 6):
            device.push(blank())

            def caught(expected: int = index) -> bool:
                return len(seen) >= expected

            wait_until(caught, "detector fell behind a 40 ms frame")
        worker.join(timeout=2)
        assert seen == [1, 2, 3, 4, 5]
        assert fast.dropped == 0
        assert frames.captured == 5
        assert frames.read_failures == 0
        assert frames.last_capture_dt_ns == 40_000_000
    finally:
        frames.stop()

    clock = iter(range(0, 1_000_000_000, 40_000_000))
    frames = CameraFrameSource(hub(CAMERA), clock=lambda: next(clock))
    slow = frames.subscribe()
    frames.start()
    try:
        device = hub.created[-1]
        for _ in range(5):
            device.push(blank())
        device.wait_reads(5)
        wait_until(lambda: frames.captured == 5, "slow capture did not finish")
        assert len(slow) == 1
        assert slow.dropped == 4
        latest = slow.take()
        assert latest is not None
        assert latest.sequence == 5
        assert frames.read_failures == 0
    finally:
        frames.stop()
    assert camera_threads() == []


def test_overwriting_every_third_frame_is_a_source_overwrite_with_an_80ms_gap() -> None:
    stamps = iter(range(0, 1_000_000_000, 40_000_000))
    hub = Hub()
    frames = CameraFrameSource(hub(CAMERA), clock=lambda: next(stamps))
    slot = frames.subscribe()
    frames.start()
    try:
        device = hub.created[0]
        taken: list[TimedFrame] = []
        for index in range(1, 6):
            device.push(blank())
            device.wait_reads(index)

            def published(expected: int = index) -> bool:
                return frames.captured >= expected

            wait_until(published, "frame was not published")
            if index == 3:
                continue
            frame = slot.take()
            assert frame is not None
            taken.append(frame)
        assert [frame.sequence for frame in taken] == [1, 2, 4, 5]
        assert slot.dropped == 1
        gaps = [
            taken[index].timestamp_ns - taken[index - 1].timestamp_ns
            for index in range(1, len(taken))
        ]
        assert 80_000_000 in gaps
        assert frames.read_failures == 0
    finally:
        frames.stop()

    session = diagnostic_session(zones(1), WIDTH, HEIGHT)
    session.start()
    try:
        for frame in taken:
            session.submit(
                frame,
                CaptureCounters(
                    captured=frame.sequence,
                    source_overwrites=1,
                    sequence=frame.sequence,
                    capture_dt_ns=40_000_000,
                    read_failures=0,
                ),
            )
            assert session.wait_idle()
        session.stop()
        text = session.text()
        assert "capture_seq=2" in text
        assert "capture_seq=4" in text
        assert "dt_ms=80.000" in text
        assert "source_overwrites=1" in text
        assert "capture_read_failures=0" in text
        performance = session.snapshot().performance
        assert performance.frames_skipped == 0
        assert performance.source_overwrites == 1
        assert performance.last_dt_ns == 40_000_000
    finally:
        session.close()


def test_a_50ms_sampler_replaces_40ms_frames() -> None:
    """The old preview timer sampled the latest slot. Capture stayed at 40 ms."""
    slot = LatestFrameBuffer()
    published = 0
    delivered: list[int] = []
    for step in range(0, 1000, 10):
        if step % 40 == 0:
            published += 1
            slot.put(TimedFrame(blank(), step * 1_000_000, sequence=published))
        if step % 50 == 0 and step > 0:
            frame = slot.take()
            if frame is not None:
                delivered.append(frame.timestamp_ns)
    gaps = [delivered[index] - delivered[index - 1] for index in range(1, len(delivered))]
    assert slot.dropped > 0
    assert 40_000_000 in gaps
    assert 80_000_000 in gaps
    assert len(delivered) < published


def test_distribution_and_detection_stay_cheap() -> None:
    picture = blank()
    frame = TimedFrame(picture, 1, sequence=1)
    started = time.perf_counter()
    slot = LatestFrameBuffer()
    for index in range(1000):
        slot.put(TimedFrame(picture, index, sequence=index + 1))
        if index % 2 == 0:
            slot.take()
    distribution_s = time.perf_counter() - started
    detector = create_lane_detector(zones(1))
    picture = blank()
    started = time.perf_counter()
    for index in range(100):
        detector.observe(picture, index * 40_000_000)
    analysis_s = time.perf_counter() - started
    per_frame_ms = analysis_s * 10
    assert distribution_s < 0.05, f"distribution_s={distribution_s:.6f}"
    assert per_frame_ms < 5, f"analysis_s={analysis_s:.6f} per_frame_ms={per_frame_ms:.3f}"
    assert frame.sequence == 1


class _OpenCV:
    instances: ClassVar[list[_OpenCV]] = []

    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self.open_count = 0
        self.close_count = 0
        self.opened = False
        self._cond = threading.Condition()
        _OpenCV.instances.append(self)

    def open(self) -> None:
        with self._cond:
            self.opened = True
            self.open_count += 1

    def close(self) -> None:
        with self._cond:
            self.opened = False
            self.close_count += 1
            self._cond.notify_all()

    def read(self) -> GrayFrame:
        with self._cond:
            while self.opened:
                self._cond.wait(timeout=0.05)
            raise CameraClosedError()

    def set_regions(self, regions: object) -> None:
        return None

    def read_zoned(self) -> None:
        return None


@pytest.fixture
def runtime() -> Iterator[Runtime]:
    app = Runtime.create(AppConfig(), database=Database.in_memory())
    yield app
    app.shutdown()


def test_main_window_navigation_keeps_one_capture(
    qtbot: QtBot, runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _OpenCV.instances.clear()
    monkeypatch.setattr(
        "slot_racing.modules.timing_camera.opencv_device.OpenCVCapture",
        _OpenCV,
    )

    def fail_probe(limit: int = 5) -> tuple[int, ...]:
        raise AssertionError(f"device probe during navigation ({limit})")

    monkeypatch.setattr(
        "slot_racing.modules.timing_camera.opencv_device.probe_device_indices",
        fail_probe,
    )
    runtime.plugins.enable("timing_camera")
    window = MainWindow(runtime)
    qtbot.addWidget(window)
    window.show()
    pages = ("vehicles", "tracks", "races", "camera_setup", "drivers")
    for step in range(20):
        window.select(pages[step % len(pages)])
        QApplication.processEvents()
    assert _OpenCV.instances
    assert sum(device.open_count for device in _OpenCV.instances) == 1
    assert sum(device.close_count for device in _OpenCV.instances) == 0
    assert len(camera_threads()) == 1
    runtime.plugins.disable("timing_camera")
    assert sum(device.close_count for device in _OpenCV.instances) >= 1
    assert camera_threads() == []


def test_shutdown_joins_without_a_second_timeout() -> None:
    capture, _hub = make_session()
    capture.ensure(CAMERA, reason="preview")
    assert len(camera_threads()) == 1
    started = time.perf_counter()
    capture.close(reason="shutdown")
    assert time.perf_counter() - started < 0.5
    assert capture.close_count == 1
    assert capture.thread_stop_count == 1
    assert camera_threads() == []
