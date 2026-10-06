"""Capture thread and the latest-frame slot between the camera and detection.

The thread only reads pictures. It does not detect cars, publish events or
touch the UI. The host monotonic clock is read immediately after a frame
returns:

```text
read frame
    ↓
perf_counter_ns()
    ↓
latest-frame slot (an unread frame is replaced)
    ↓
detection worker, or poll_latest for the setup preview
```

``put`` never waits. A newer picture replaces one the consumer has not taken,
so a slow consumer skips ahead instead of walking through old frames. There is
no backlog and no backpressure on the camera.

``open`` and ``close`` run on the caller. For a race that caller is the GUI
thread: a driver that stalls inside ``open`` or ``close`` freezes the window
even though ``read`` itself blocks only on the capture thread. ``stop`` then
joins that thread for at most two seconds. The synchronous ``open`` stays,
because a missing camera has to fail the race start before the session is
running. ``read`` is not moved onto the GUI thread.

Host-driven sources that have no capture thread still deliver frames through
``poll``, at most :data:`MAX_FRAMES_PER_POLL` per call.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from slot_racing.modules.timing_camera._checks import require_range
from slot_racing.modules.timing_camera.frame_source import FrameSource, TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.lease import CameraBusyError, CameraLease

# One unread frame. A second arrival replaces it. A deeper slot would keep
# pictures the detector is already too late to use.
MAX_QUEUED_FRAMES = 1
# Host-driven sources (tests, manual frames) still have no worker. One poll
# handles a few of their frames and then returns.
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


class LatestFrameBuffer:
    """At most one unread frame. ``put`` replaces it and never blocks.

    The grab timestamp on a frame is never rewritten. ``wait`` blocks until a
    frame is published or ``stop`` is set. It is the detection worker's wakeup,
    not a timer. A frame taken by ``wait`` stays in flight until :meth:`ack`,
    so a caller can see that detection has not caught up yet.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: TimedFrame | None = None
        self._in_flight = False
        self.dropped = 0

    def put(self, frame: TimedFrame) -> None:
        with self._cond:
            if self._frame is not None:
                self.dropped += 1
            self._frame = frame
            self._cond.notify_all()

    def take(self) -> TimedFrame | None:
        """The unread frame, if there is one. Does not wait and is not in flight."""
        with self._cond:
            frame = self._frame
            self._frame = None
            return frame

    def wait(self, stop: threading.Event) -> TimedFrame | None:
        """Block until the newest unread frame is available, or ``stop`` is set.

        A frame that arrives after ``stop`` is left unread. Detection does not
        start it.
        """
        with self._cond:
            while self._frame is None and not stop.is_set():
                self._cond.wait()
            if stop.is_set():
                return None
            frame = self._frame
            self._frame = None
            self._in_flight = True
            return frame

    def ack(self) -> None:
        """Detection finished the frame taken by :meth:`wait`."""
        with self._cond:
            self._in_flight = False

    def clear(self) -> None:
        with self._cond:
            self._frame = None
            self._cond.notify_all()

    def wake(self) -> None:
        """Unblock :meth:`wait`. Used when capture fails or detection stops."""
        with self._cond:
            self._cond.notify_all()

    def idle(self) -> bool:
        """True when nothing is unread and nothing is still being detected."""
        with self._cond:
            return self._frame is None and not self._in_flight

    def __len__(self) -> int:
        with self._cond:
            return 0 if self._frame is None else 1


class CameraFrameSource(FrameSource):
    """Reads a :class:`CaptureDevice` on its own thread and keeps the latest frame.

    During a pause the thread keeps reading, so the camera buffer stays current,
    but those frames are not published. ``resume`` also drops anything still
    unread and asks the timing source to resynchronize the detector: a car that
    is already standing in a zone must not become a crossing.
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
        # Callers may still pass a size. The slot stays one frame either way:
        # a larger buffer would hold pictures detection is already too late for.
        require_range("queue_size", queue_size, 1)
        self._queue = LatestFrameBuffer()
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
        self._captured = 0
        self._last_read_ns = 0

    @property
    def queued(self) -> int:
        return len(self._queue)

    @property
    def dropped(self) -> int:
        return self._queue.dropped

    @property
    def captured(self) -> int:
        """Frames read from the device since ``start``. Safe to sample for a later display."""
        return self._captured

    @property
    def last_read_ns(self) -> int:
        """How long the most recent ``read`` took, in nanoseconds. Zero before the first frame."""
        return self._last_read_ns

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
        self._captured = 0
        self._last_read_ns = 0
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

    def use_regions(self, regions: tuple[DetectionRoi, ...]) -> None:
        """Ask the device to read only these rectangles. Ignored when it cannot.

        An empty tuple restores a full-frame read. The capture thread picks the
        change up on its next picture, so the frame already in hand stays whole.
        """
        if not isinstance(regions, tuple):
            raise TypeError("regions must be a tuple")
        setter = getattr(self._device, "set_regions", None)
        if callable(setter):
            setter(regions)

    def check(self) -> None:
        with self._error_lock:
            error = self._error
        if error is not None:
            raise CameraReadError(str(error)) from error

    def poll_frame(self) -> TimedFrame | None:
        self.check()
        if not self._running or self._paused:
            return None
        frame = self._queue.take()
        if frame is None:
            return None
        resume_ns = self._resume_ns
        if resume_ns is not None and frame.timestamp_ns < resume_ns:
            return None
        return frame

    def wait_frame(self, stop: threading.Event) -> TimedFrame | None:
        """Block until the newest frame is available, or ``stop`` is set.

        The detection worker uses this. It does not sleep on a timer. A frame
        taken here stays in flight until :meth:`ack_frame`.
        """
        return self._queue.wait(stop)

    def ack_frame(self) -> None:
        """The worker finished the frame from :meth:`wait_frame`."""
        self._queue.ack()

    def clear_unread(self) -> None:
        """Drop a frame nobody has started detecting."""
        self._queue.clear()

    def wake(self) -> None:
        """Unblock a worker waiting in :meth:`wait_frame`."""
        self._queue.wake()

    def detection_idle(self) -> bool:
        """True when the latest slot is empty and no waited frame is in flight."""
        return self._queue.idle()

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                started = time.perf_counter_ns()
                grabbed = self._grab()
                self._last_read_ns = time.perf_counter_ns() - started
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
            self._captured += 1
            # The stamp belongs to the grab, not to a later poll.
            timestamp_ns = time.perf_counter_ns()
            if self._paused or (self._resume_ns is not None and timestamp_ns < self._resume_ns):
                continue
            self._queue.put(_timed(grabbed, timestamp_ns))

    def _grab(self) -> GrayFrame | tuple[GrayFrame, ...]:
        """One full picture, or the zone crops when the device was asked for them."""
        reader = getattr(self._device, "read_zoned", None)
        if callable(reader):
            zoned = reader()
            if zoned is not None:
                _width, _height, crops = zoned
                if not isinstance(crops, tuple) or any(
                    not isinstance(crop, GrayFrame) for crop in crops
                ):
                    raise CameraReadError("camera frame is missing")
                return crops
        return self._device.read()

    def _fail(self, error: BaseException) -> None:
        with self._error_lock:
            if self._error is None:
                self._error = error
        self._device.close()
        self._queue.wake()


# A crop delivery has no full picture. The carrier is not scanned.
_CROP_CARRIER = GrayFrame(1, 1, b"\x00")


def _timed(grabbed: GrayFrame | tuple[GrayFrame, ...], timestamp_ns: int) -> TimedFrame:
    if isinstance(grabbed, GrayFrame):
        return TimedFrame(grabbed, timestamp_ns)
    return TimedFrame(_CROP_CARRIER, timestamp_ns, crops=grabbed)
