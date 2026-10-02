"""Capture thread and the bounded queue between the camera and ``poll``.

The thread only reads pictures. It does not detect cars, publish events or
touch the UI. The host monotonic clock is read immediately after a frame
returns, because ``poll`` may run much later:

```text
read frame
    ↓
perf_counter_ns()
    ↓
queue
    ↓
poll() keeps that timestamp
```

The live view asks for events about every 100 ms. A timing path should not fall
seconds behind, so the queue keeps only the newest frames (:data:`MAX_QUEUED_FRAMES`).
When it is full the oldest frame is dropped and the new one is kept. ``poll``
handles at most :data:`MAX_FRAMES_PER_POLL` frames, which is one poll interval
at the requested camera rate plus a little catch-up.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Protocol

from slot_racing.modules.timing_camera._checks import require_range
from slot_racing.modules.timing_camera.frame_source import FrameSource, TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.lease import CameraBusyError, CameraLease

# Newest frames only. Two pictures are enough for the next 100 ms poll to see
# the latest grab without walking through a backlog.
MAX_QUEUED_FRAMES = 2
# 30 fps over a 100 ms poll is about three frames. Four leaves a little catch-up
# and still returns control to the host quickly.
MAX_FRAMES_PER_POLL = 4
DEFAULT_READ_FAILURES = 3


class CameraOpenError(Exception):
    """The device could not be opened. Nothing is left running."""


class CameraReadError(Exception):
    """The open device stopped delivering frames."""


class CameraClosedError(Exception):
    """``read`` woke up because the device was closed. Not a device failure."""


class CaptureDevice(Protocol):
    """One camera, or a test double. Implementations may import a capture library."""

    def open(self) -> None:
        """Open the device. Raise :class:`CameraOpenError` when that fails."""

    def read(self) -> GrayFrame:
        """Block until the next grayscale frame, or raise when the device fails."""

    def close(self) -> None:
        """Release the device. Safe to call more than once."""


class FrameQueue:
    """A small queue that drops the oldest frame when a newer one arrives full.

    Low latency wins over delivering every picture. The grab timestamp on a
    frame is never rewritten.
    """

    def __init__(self, capacity: int) -> None:
        require_range("capacity", capacity, 1)
        self._capacity = capacity
        self._items: deque[TimedFrame] = deque()
        self._lock = threading.Lock()
        self.dropped = 0

    def put(self, frame: TimedFrame) -> None:
        with self._lock:
            while len(self._items) >= self._capacity:
                self._items.popleft()
                self.dropped += 1
            self._items.append(frame)

    def take(self) -> TimedFrame | None:
        with self._lock:
            if not self._items:
                return None
            return self._items.popleft()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class CameraFrameSource(FrameSource):
    """Reads a :class:`CaptureDevice` on its own thread and queues grayscale frames.

    During a pause the thread keeps reading, so the camera buffer stays current,
    but those frames are not queued. ``resume`` also drops anything still queued
    and asks the timing source to resynchronize the detector: a car that is
    already standing in a zone must not become a crossing.
    """

    def __init__(
        self,
        device: CaptureDevice,
        *,
        queue_size: int = MAX_QUEUED_FRAMES,
        max_read_failures: int = DEFAULT_READ_FAILURES,
        lease: CameraLease | None = None,
        lease_owner: str = CameraLease.RACE,
    ) -> None:
        if not callable(getattr(device, "open", None)):
            raise TypeError("device must be a capture device")
        if lease is not None and not isinstance(lease, CameraLease):
            raise TypeError("lease must be a CameraLease")
        self._device = device
        self._queue = FrameQueue(queue_size)
        self._max_read_failures = require_range("max_read_failures", max_read_failures, 1)
        self._lease = lease
        self._lease_owner = lease_owner
        self._holding_lease = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._paused = False
        self._resume_ns: int | None = None
        self._running = False
        self._error: BaseException | None = None
        self._error_lock = threading.Lock()

    @property
    def queued(self) -> int:
        return len(self._queue)

    @property
    def dropped(self) -> int:
        return self._queue.dropped

    @property
    def is_capturing(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        if self._running:
            raise RuntimeError("camera capture is already running")
        self._stop.clear()
        self._paused = False
        self._resume_ns = None
        with self._error_lock:
            self._error = None
        self._queue.clear()
        if self._lease is not None and not self._lease.try_acquire(self._lease_owner):
            raise CameraBusyError("camera is in use")
        self._holding_lease = self._lease is not None
        try:
            self._device.open()
        except Exception:
            self._device.close()
            self._release_lease()
            raise
        self._running = True
        self._thread = threading.Thread(target=self._run, name="slot-racing-camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._paused = False
        self._running = False
        self._device.close()
        self._queue.clear()
        self._release_lease()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2)
            if thread.is_alive():
                raise RuntimeError("camera capture thread did not stop")

    def _release_lease(self) -> None:
        if self._holding_lease and self._lease is not None:
            self._lease.release(self._lease_owner)
        self._holding_lease = False

    def pause(self) -> None:
        if self._running:
            self._paused = True

    def resume(self) -> bool:
        """Drop pause frames. The next grabbed frame resynchronizes detection."""
        if not self._running:
            return False
        # Stamp first, then drop the queue, so a frame read during the pause
        # cannot land as the first frame of the resumed race.
        self._resume_ns = time.perf_counter_ns()
        self._queue.clear()
        self._paused = False
        return True

    def check(self) -> None:
        with self._error_lock:
            error = self._error
        if error is not None:
            raise CameraReadError(str(error)) from error

    def poll_frame(self) -> TimedFrame | None:
        self.check()
        if not self._running or self._paused:
            return None
        while True:
            frame = self._queue.take()
            if frame is None:
                return None
            resume_ns = self._resume_ns
            if resume_ns is not None and frame.timestamp_ns < resume_ns:
                continue
            return frame

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                image = self._device.read()
            except CameraClosedError:
                return
            except Exception as error:
                if self._stop.is_set():
                    return
                failures += 1
                if failures >= self._max_read_failures:
                    self._fail(error)
                    return
                continue
            failures = 0
            # The stamp belongs to the grab, not to a later poll.
            timestamp_ns = time.perf_counter_ns()
            if self._paused or (self._resume_ns is not None and timestamp_ns < self._resume_ns):
                continue
            self._queue.put(TimedFrame(image, timestamp_ns))

    def _fail(self, error: BaseException) -> None:
        with self._error_lock:
            if self._error is None:
                self._error = error
        self._device.close()
