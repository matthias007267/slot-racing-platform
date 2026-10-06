"""Detection runs on its own worker and keeps only the latest unread frame."""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QTimer
from pytestqt.qtbot import QtBot

from slot_racing.modules.timing_camera.capture import LatestFrameBuffer
from slot_racing.modules.timing_camera.configuration import scale_detector_settings
from slot_racing.modules.timing_camera.detection import DetectionZone, DetectorSettings
from slot_racing.modules.timing_camera.frame_source import TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from tests.modules.test_camera_capture import (
    ScriptedCapture,
    column,
    started,
    wait_for,
    zones,
)


def test_wait_blocks_until_a_frame_arrives_and_stop_releases_it() -> None:
    slot = LatestFrameBuffer()
    found: list[TimedFrame | None] = []

    def consume(stop: threading.Event) -> None:
        found.append(slot.wait(stop))

    stop = threading.Event()
    worker = threading.Thread(target=consume, args=(stop,))
    worker.start()
    time.sleep(0.05)
    assert worker.is_alive()
    assert found == []
    frame = TimedFrame(GrayFrame(1, 1, b"\x00"), 5)
    slot.put(frame)
    worker.join(timeout=1)
    assert found == [frame]
    assert not slot.idle()
    slot.ack()
    assert slot.idle()

    stop.set()
    slot.wake()
    released = slot.wait(stop)
    assert released is None
    assert slot.idle()


def test_detection_observes_frames_before_poll_and_keeps_every_crossing() -> None:
    capture = ScriptedCapture()
    source, _frames, received = started(capture, zones(1, 2))
    try:
        names = {thread.name for thread in threading.enumerate()}
        assert "slot-racing-detection" in names
        assert "slot-racing-camera" in names
        started_at = time.perf_counter()
        capture.push(column(1, 30))
        wait_for(lambda: source.frames_observed >= 1 and source.caught_up())
        capture.push(column(2, 30))
        wait_for(lambda: source.frames_observed >= 2 and source.caught_up())
        capture.push(column(1, 32))
        wait_for(lambda: source.frames_observed >= 3 and source.caught_up())
        capture.push(column(2, 32))
        wait_for(lambda: source.frames_observed >= 4 and source.caught_up())
        assert time.perf_counter() - started_at < 0.5
        assert received == []
        source.poll()
        assert [(event.lane, event.position_id) for event in received] == [
            (1, "start_finish"),
            (2, "start_finish"),
        ]
        assert received[0].timestamp_ns < received[1].timestamp_ns
    finally:
        source.stop()
    assert not any(
        thread.name == "slot-racing-detection" and thread.is_alive()
        for thread in threading.enumerate()
    )
    source.poll()
    assert len(received) == 2


def test_a_gui_timer_keeps_running_while_detection_works(qtbot: QtBot) -> None:
    capture = ScriptedCapture()
    source, _frames, received = started(capture)
    fired: list[str] = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: fired.append(threading.current_thread().name))
    timer.start()
    try:
        qtbot.waitUntil(lambda: len(fired) >= 1, timeout=1000)
        capture.push(column(1, 30))
        qtbot.waitUntil(lambda: source.frames_observed >= 1, timeout=1000)
        assert received == []
        assert fired
        assert all(name != "slot-racing-detection" for name in fired)
    finally:
        timer.stop()
        source.stop()


def test_stop_drops_a_frame_that_arrives_after_the_race() -> None:
    capture = ScriptedCapture()
    source, frames, received = started(capture)
    try:
        capture.push(column(1, 30))
        wait_for(lambda: source.frames_observed >= 1)
        source.stop()
        assert not frames.is_capturing
        capture.push(column(1, 32))
        source.poll()
        assert received == []
    finally:
        source.stop()


def test_a_zone_outside_the_source_frame_is_rejected() -> None:
    settings = DetectorSettings((DetectionZone("start_finish", 1, DetectionRoi(90, 0, 20, 10)),))
    with pytest.raises(ValueError, match="extends outside the frame"):
        scale_detector_settings(settings, 100, 50, 200, 100)
