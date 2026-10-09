"""Capture thread and the latest-frame slot between the camera and detection.

The thread only reads pictures. It does not detect cars, publish events or
touch the UI. The host monotonic clock is read immediately after a frame
returns:

```text
read frame
    ↓
capture clock (``perf_counter_ns`` for hardware)
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
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol, cast

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

    ``dropped`` counts unread frames replaced by a newer one. That is a
    latest-frame overwrite, not a failed camera read and not a detector skip.
    The grab timestamp on a frame is never rewritten. ``wait`` blocks until a
    frame is published or ``stop`` is set. It is the detection worker's wakeup,
    not a timer. A frame taken by ``wait`` stays in flight until :meth:`ack`,
    so a caller can see that detection has not caught up yet.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: TimedFrame | None = None
        self._in_flight = False
        self._retired = False
        self._takes = 0
        self.dropped = 0

    @property
    def retired(self) -> bool:
        with self._cond:
            return self._retired

    def put(self, frame: TimedFrame) -> None:
        with self._cond:
            if self._retired:
                return
            if self._frame is not None:
                self.dropped += 1
            self._frame = frame
            self._cond.notify_all()

    def take(self) -> TimedFrame | None:
        """The unread frame, if there is one. Does not wait and is not in flight."""
        with self._cond:
            if self._retired:
                return None
            frame = self._frame
            self._frame = None
            return frame

    def wait(self, stop: threading.Event) -> TimedFrame | None:
        """Block until the newest unread frame is available, or ``stop`` is set.

        A frame that arrives after ``stop`` is left unread. Detection does not
        start it. A retired slot returns ``None`` so a replaced consumer can
        attach to the new capture.
        """
        with self._cond:
            while self._frame is None and not stop.is_set() and not self._retired:
                self._cond.wait()
            if stop.is_set() or self._retired:
                return None
            frame = self._frame
            self._frame = None
            self._in_flight = True
            self._takes += 1
            return frame

    def retire(self) -> None:
        """Unblock waiters. Further frames are ignored. The slot is not reused."""
        with self._cond:
            self._retired = True
            self._frame = None
            self._cond.notify_all()

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

    @property
    def takes(self) -> int:
        """Frames removed for detection. Analysis may still be running."""
        with self._cond:
            return self._takes

    def __len__(self) -> int:
        with self._cond:
            return 0 if self._frame is None else 1


def capture_clock_of(device: object) -> Callable[[], int] | None:
    """A simulated device may name its grab clock. Hardware leaves this unset.

    ``None`` keeps :meth:`CameraFrameSource` on ``perf_counter_ns``.
    """
    clock = getattr(device, "capture_clock", None)
    if clock is None:
        return None
    if not callable(clock):
        raise TypeError("capture_clock must be a callable")
    return cast(Callable[[], int], clock)


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
        clock: Callable[[], int] | None = None,
    ) -> None:
        if not callable(getattr(device, "open", None)):
            raise TypeError("device must be a capture device")
        if lease is not None and not isinstance(lease, CameraLease):
            raise TypeError("lease must be a CameraLease")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be a callable")
        self._device = device
        # Callers may still pass a size. The slot stays one frame either way:
        # a larger buffer would hold pictures detection is already too late for.
        require_range("queue_size", queue_size, 1)
        self._queue = LatestFrameBuffer()
        self._max_read_failures = require_range("max_read_failures", max_read_failures, 1)
        self._lease = lease
        self._lease_owner = lease_owner
        self._holding_lease = False
        self._clock = time.perf_counter_ns if clock is None else clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._paused = False
        self._resume_ns: int | None = None
        self._running = False
        self._error: BaseException | None = None
        self._error_lock = threading.Lock()
        self._subscribers: list[LatestFrameBuffer] = []
        self._sub_lock = threading.Lock()
        self._captured = 0
        self._sequence = 0
        self._read_attempts = 0
        self._read_failures = 0
        self._previous_stamp: int | None = None
        self._last_read_ns = 0
        self._last_capture_dt_ns = 0
        self._last_publish_ns = 0
        self.last_open_ns = 0
        self.last_close_ns = 0
        self.last_join_ns = 0
        self.reported_fps: float | None = None

    @property
    def queued(self) -> int:
        return len(self._queue)

    @property
    def dropped(self) -> int:
        """Unread frames replaced in the primary slot.

        This is a latest-frame overwrite. It is not a failed ``read``. Subscribers
        keep their own overwrite counts; those are not added here.
        """
        return self._queue.dropped

    @property
    def frames_taken(self) -> int:
        """Frames the worker has taken from the primary slot, before analysis."""
        return self._queue.takes

    @property
    def captured(self) -> int:
        """Frames read from the device since ``start``. Safe to sample for a later display."""
        return self._captured

    @property
    def sequence(self) -> int:
        """Sequence of the last frame read from the device. Zero before the first one."""
        return self._sequence

    @property
    def read_attempts(self) -> int:
        return self._read_attempts

    @property
    def read_failures(self) -> int:
        return self._read_failures

    @property
    def last_read_ns(self) -> int:
        """How long the most recent ``read`` took, in nanoseconds. Zero before the first frame."""
        return self._last_read_ns

    @property
    def last_capture_dt_ns(self) -> int:
        """Gap between the last two successful grabs. Zero until the second frame."""
        return self._last_capture_dt_ns

    @property
    def last_publish_ns(self) -> int:
        """Time spent handing the last frame to its slots. Zero before the first publish."""
        return self._last_publish_ns

    def subscribe(self) -> LatestFrameBuffer:
        """A private latest-frame slot. Publishing does not wait for the consumer."""
        slot = LatestFrameBuffer()
        with self._sub_lock:
            self._subscribers.append(slot)
        return slot

    def unsubscribe(self, slot: LatestFrameBuffer) -> None:
        """Drop ``slot`` and wake anyone blocked in its ``wait``."""
        with self._sub_lock, suppress(ValueError):
            self._subscribers.remove(slot)
        slot.retire()

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
        self._sequence = 0
        self._read_attempts = 0
        self._read_failures = 0
        self._previous_stamp = None
        self._last_read_ns = 0
        self._last_capture_dt_ns = 0
        self._last_publish_ns = 0
        self.reported_fps = None
        if self._lease is not None and not self._lease.try_acquire(self._lease_owner):
            raise CameraBusyError("camera is in use")
        self._holding_lease = self._lease is not None
        opened = time.perf_counter_ns()
        try:
            self._device.open()
        except Exception:
            self.last_open_ns = time.perf_counter_ns() - opened
            self._device.close()
            self._release_lease()
            raise
        self.last_open_ns = time.perf_counter_ns() - opened
        self.reported_fps = _reported_fps(getattr(self._device, "actual_fps", None))
        self._running = True
        self._thread = threading.Thread(target=self._run, name="slot-racing-camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._paused = False
        self._running = False
        closed = time.perf_counter_ns()
        self._device.close()
        self.last_close_ns = time.perf_counter_ns() - closed
        self._queue.clear()
        self._retire_subscribers()
        self._release_lease()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            joined = time.perf_counter_ns()
            thread.join(timeout=2)
            self.last_join_ns = time.perf_counter_ns() - joined
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
        # cannot land as the first frame of the resumed race. Hardware uses
        # ``perf_counter_ns``. A simulated clock stays in its own time domain.
        self._resume_ns = self._clock()
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

    def _retire_subscribers(self) -> None:
        with self._sub_lock:
            slots = tuple(self._subscribers)
            self._subscribers.clear()
        for slot in slots:
            slot.retire()

    def _emit(self, frame: TimedFrame) -> None:
        started = time.perf_counter_ns()
        with self._sub_lock:
            slots = tuple(self._subscribers)
        if slots:
            for slot in slots:
                slot.put(frame)
        else:
            self._queue.put(frame)
        self._last_publish_ns = time.perf_counter_ns() - started

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            self._read_attempts += 1
            try:
                started = time.perf_counter_ns()
                grabbed = self._grab()
                self._last_read_ns = time.perf_counter_ns() - started
            except CameraClosedError:
                return
            except Exception as error:
                self._read_failures += 1
                if self._stop.is_set():
                    return
                failures += 1
                if failures >= self._max_read_failures:
                    self._fail(error)
                    return
                continue
            failures = 0
            self._captured += 1
            self._sequence += 1
            # The stamp belongs to the grab, not to a later poll.
            timestamp_ns = self._clock()
            previous = self._previous_stamp
            if previous is not None:
                self._last_capture_dt_ns = timestamp_ns - previous
            self._previous_stamp = timestamp_ns
            if self._paused or (self._resume_ns is not None and timestamp_ns < self._resume_ns):
                continue
            self._emit(_timed(grabbed, timestamp_ns, self._sequence))

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
        closed = time.perf_counter_ns()
        self._device.close()
        self.last_close_ns = time.perf_counter_ns() - closed
        # The device is released here. The lease stays with this source until
        # ``stop``, so a preview cannot open a second capture while the race
        # that owned the failure is still shutting down.
        self._retire_subscribers()
        self._queue.wake()


# A crop delivery has no full picture. The carrier is not scanned.
_CROP_CARRIER = GrayFrame(1, 1, b"\x00")


def _reported_fps(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    fps = float(value)
    return fps if fps > 0 else None


def _timed(
    grabbed: GrayFrame | tuple[GrayFrame, ...], timestamp_ns: int, sequence: int = 0
) -> TimedFrame:
    if isinstance(grabbed, GrayFrame):
        return TimedFrame(grabbed, timestamp_ns, sequence=sequence)
    return TimedFrame(_CROP_CARRIER, timestamp_ns, crops=grabbed, sequence=sequence)
