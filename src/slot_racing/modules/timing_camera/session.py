"""One physical camera for the whole application.

Preview, diagnosis and a race are consumers. They do not open their own
capture device. Navigation detaches a consumer. It does not close the device.
The device closes when the application shuts down, the user picks another
camera or a capture error leaves the device unusable.

The detector is not part of this object. A race and a diagnosis each build
their own detector and throw it away when they stop.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from slot_racing.core.diagnostics import record
from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import (
    CameraFrameSource,
    CameraOpenError,
    CameraReadError,
    CaptureDevice,
    LatestFrameBuffer,
)
from slot_racing.modules.timing_camera.frame_source import FrameSource, TimedFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.lease import CameraLease

logger = logging.getLogger(__name__)

DeviceFactory = Callable[[CameraConfig], CaptureDevice]
_MAX_EVENTS = 200


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """One capture lifecycle step. Debug and tests read this; the UI does not."""

    action: str
    reason: str
    consumer: str
    timestamp_ns: int
    duration_ns: int


@dataclass(frozen=True, slots=True)
class PipelineSnapshot:
    """Counters for one capture. Consumers add their own overwrite counts."""

    read_attempts: int
    read_successes: int
    read_failures: int
    capture_frames: int
    last_sequence: int
    last_capture_dt_ns: int
    last_publish_ns: int
    last_open_ns: int
    last_close_ns: int
    last_join_ns: int
    open_count: int
    close_count: int
    thread_start_count: int
    thread_stop_count: int


class SessionConsumer(FrameSource):
    """One consumer of a shared capture. ``stop`` detaches it and leaves the device open."""

    def __init__(
        self,
        session: CameraSession,
        config: CameraConfig,
        name: str,
        *,
        ensure_reason: str,
        attach_reason: str,
        detach_reason: str,
        resets_regions: bool = False,
    ) -> None:
        self._session = session
        self._config = config
        self._name = name
        self._ensure_reason = ensure_reason
        self._attach_reason = attach_reason
        self._detach_reason = detach_reason
        self._resets_regions = resets_regions
        self._running = False
        self._paused = False
        self.frames_received = 0

    @property
    def is_capturing(self) -> bool:
        return self._running and self._session.is_open

    @property
    def replaced(self) -> int:
        """Unread frames this consumer's slot replaced. Not a camera read failure."""
        slot = self._session.slot_for(self._name)
        if slot is None:
            return 0
        return slot.dropped

    def start(self) -> None:
        self._session.ensure(self._config, reason=self._ensure_reason)
        self._session.attach(self._name, reason=self._attach_reason)
        self._running = True
        self._paused = False

    def stop(self) -> None:
        self._running = False
        self._paused = False
        self._session.detach(self._name, reason=self._detach_reason)
        if self._resets_regions:
            self._session.use_regions(())

    def pause(self) -> None:
        if self._running:
            self._paused = True

    def resume(self) -> bool:
        if not self._running:
            return False
        slot = self._session.slot_for(self._name)
        if slot is not None:
            slot.clear()
        self._paused = False
        return True

    def check(self) -> None:
        self._session.check()

    def poll_frame(self) -> TimedFrame | None:
        self._session.check()
        if not self._running or self._paused:
            return None
        slot = self._session.slot_for(self._name)
        if slot is None:
            return None
        frame = slot.take()
        if frame is None:
            return None
        self.frames_received += 1
        return frame

    def wait_frame(self, stop: threading.Event) -> TimedFrame | None:
        while not stop.is_set():
            slot = self._session.slot_for(self._name)
            if slot is None:
                return None
            frame = slot.wait(stop)
            if stop.is_set():
                return None
            if frame is None:
                replacement = self._session.slot_for(self._name)
                if replacement is None or replacement is slot or replacement.retired:
                    return None
                continue
            if self._paused:
                slot.ack()
                continue
            self.frames_received += 1
            return frame
        return None

    def ack_frame(self) -> None:
        slot = self._session.slot_for(self._name)
        if slot is not None:
            slot.ack()

    def clear_unread(self) -> None:
        slot = self._session.slot_for(self._name)
        if slot is not None:
            slot.clear()

    def wake(self) -> None:
        slot = self._session.slot_for(self._name)
        if slot is not None:
            slot.wake()

    def detection_idle(self) -> bool:
        slot = self._session.slot_for(self._name)
        if slot is None:
            return True
        return slot.idle()

    def use_regions(self, regions: tuple[DetectionRoi, ...]) -> None:
        self._session.use_regions(regions)


class CameraSession:
    """Owns the physical camera. Consumers come and go without reopening it."""

    def __init__(
        self,
        lease: CameraLease,
        *,
        devices: DeviceFactory | None = None,
    ) -> None:
        if not isinstance(lease, CameraLease):
            raise TypeError("lease must be a CameraLease")
        if devices is not None and not callable(devices):
            raise TypeError("devices must be a callable")
        self._lease = lease
        self._devices = devices
        self._lock = threading.RLock()
        self._source: CameraFrameSource | None = None
        self._config: CameraConfig | None = None
        self._identity: tuple[int, int, int, int, str] | None = None
        self._slots: dict[str, LatestFrameBuffer] = {}
        self._events: list[LifecycleEvent] = []
        self._error: BaseException | None = None
        self._failed_identity: tuple[int, int, int, int, str] | None = None
        self._open_count = 0
        self._close_count = 0
        self._thread_starts = 0
        self._thread_stops = 0
        self._race_ids = 0
        self.last_open_ns = 0
        self.last_close_ns = 0
        self.last_join_ns = 0

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._is_live_locked()

    @property
    def open_count(self) -> int:
        return self._open_count

    @property
    def close_count(self) -> int:
        return self._close_count

    @property
    def thread_start_count(self) -> int:
        return self._thread_starts

    @property
    def thread_stop_count(self) -> int:
        return self._thread_stops

    def events(self) -> tuple[LifecycleEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def known_indices(self) -> tuple[int, ...]:
        """The open device, if capture is live. Does not probe other indices."""
        with self._lock:
            config = self._config
            if config is None or not self._is_live_locked():
                return ()
            return (config.device_index,)

    def hardware_state(self, camera: CameraConfig) -> str:
        """``live``, ``failed`` or ``closed``. Does not open or close the device."""
        if not isinstance(camera, CameraConfig):
            raise TypeError("camera must be a CameraConfig")
        identity = _identity(camera)
        with self._lock:
            if self._is_live_locked() and self._identity == identity:
                return "live"
            if self._failed_identity == identity:
                return "failed"
            return "closed"

    def pipeline(self) -> PipelineSnapshot:
        with self._lock:
            source = self._source
            if source is None:
                return PipelineSnapshot(
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    self.last_open_ns,
                    self.last_close_ns,
                    self.last_join_ns,
                    self._open_count,
                    self._close_count,
                    self._thread_starts,
                    self._thread_stops,
                )
            return PipelineSnapshot(
                source.read_attempts,
                source.captured,
                source.read_failures,
                source.captured,
                source.sequence,
                source.last_capture_dt_ns,
                source.last_publish_ns,
                self.last_open_ns,
                self.last_close_ns,
                self.last_join_ns,
                self._open_count,
                self._close_count,
                self._thread_starts,
                self._thread_stops,
            )

    def slot_for(self, name: str) -> LatestFrameBuffer | None:
        with self._lock:
            return self._slots.get(name)

    def ensure(self, config: CameraConfig, *, reason: str) -> None:
        """Open the device once. Reopen only when the device or the mode changed."""
        if not isinstance(config, CameraConfig):
            raise TypeError("config must be a CameraConfig")
        _reason(reason)
        identity = _identity(config)
        with self._lock:
            if self._is_live_locked() and self._identity == identity:
                return
            preserved = list(self._slots)
            if self._source is not None:
                close_reason = "reconfigure" if self._identity != identity else "reopen"
                self._close_locked(close_reason)
            try:
                self._open_locked(config, reason)
            except Exception:
                self._failed_identity = identity
                raise
            for name in preserved:
                self._attach_locked(name, "reconfigure")

    def close(self, *, reason: str) -> None:
        """Release the device. Safe to call more than once."""
        _reason(reason)
        with self._lock:
            self._close_locked(reason)

    def attach(self, name: str, *, reason: str) -> LatestFrameBuffer:
        """Register a consumer. The device must already be open."""
        _consumer(name)
        _reason(reason)
        with self._lock:
            return self._attach_locked(name, reason)

    def detach(self, name: str, *, reason: str) -> None:
        """Drop a consumer. The device stays open while the session still holds it."""
        _consumer(name)
        _reason(reason)
        with self._lock:
            self._detach_locked(name, reason)

    def check(self) -> None:
        """Raise after a capture failure and release the device once."""
        with self._lock:
            source = self._source
            error = self._error
        if source is None:
            if error is not None:
                raise CameraReadError(str(error)) from error
            return
        try:
            source.check()
        except CameraReadError as caught:
            with self._lock:
                self._error = caught
                if self._identity is not None:
                    self._failed_identity = self._identity
                self._close_locked("read_failure")
            raise

    def use_regions(self, regions: tuple[DetectionRoi, ...]) -> None:
        with self._lock:
            source = self._source
        if source is not None:
            source.use_regions(regions)

    def open_preview(self, config: CameraConfig) -> SessionConsumer:
        """Attach the setup preview. A repeated call does not open the device again."""
        consumer = SessionConsumer(
            self,
            config,
            "preview",
            ensure_reason="preview",
            attach_reason="page_shown",
            detach_reason="page_hidden",
        )
        consumer.start()
        return consumer

    def race_consumer(self, config: CameraConfig) -> SessionConsumer:
        """A race consumer. Starting it opens the camera only when nothing else has."""
        with self._lock:
            self._race_ids += 1
            name = f"race-{self._race_ids}"
        return SessionConsumer(
            self,
            config,
            name,
            ensure_reason="race_start",
            attach_reason="race_started",
            detach_reason="race_stopped",
            resets_regions=True,
        )

    def _is_live_locked(self) -> bool:
        source = self._source
        return source is not None and source.is_capturing

    def _attach_locked(self, name: str, reason: str) -> LatestFrameBuffer:
        current = self._slots.get(name)
        if current is not None and not current.retired:
            return current
        source = self._source
        if source is None or not source.is_capturing:
            raise CameraOpenError("camera is not open")
        slot = source.subscribe()
        self._slots[name] = slot
        self._note("camera_consumer_attached", reason, name, 0)
        return slot

    def _detach_locked(self, name: str, reason: str) -> None:
        slot = self._slots.pop(name, None)
        source = self._source
        if slot is None:
            return
        if source is not None:
            source.unsubscribe(slot)
        else:
            slot.retire()
        self._note("camera_consumer_detached", reason, name, 0)

    def _open_locked(self, config: CameraConfig, reason: str) -> None:
        device = self._make_device(config)
        source = CameraFrameSource(
            device,
            lease=self._lease,
            lease_owner=CameraLease.CAPTURE,
        )
        try:
            source.start()
        except Exception:
            self.last_open_ns = source.last_open_ns
            self._note("camera_closed", "open_failed", "", source.last_open_ns)
            raise
        self._source = source
        self._config = config
        self._identity = _identity(config)
        self._error = None
        self._failed_identity = None
        self._open_count += 1
        self._thread_starts += 1
        self.last_open_ns = source.last_open_ns
        self._note("camera_open", reason, "", source.last_open_ns)
        self._note("camera_started", reason, "", 0)

    def _close_locked(self, reason: str) -> None:
        source = self._source
        if source is None:
            return
        names = list(self._slots)
        for name in names:
            self._detach_locked(name, reason)
        self._note("camera_stopping", reason, "", 0)
        self._source = None
        self._config = None
        self._identity = None
        try:
            source.stop()
        finally:
            self._close_count += 1
            self._thread_stops += 1
            self.last_close_ns = source.last_close_ns
            self.last_join_ns = source.last_join_ns
            self._note("camera_closed", reason, "", source.last_close_ns + source.last_join_ns)

    def _make_device(self, config: CameraConfig) -> CaptureDevice:
        if self._devices is not None:
            return self._devices(config)
        from slot_racing.modules.timing_camera.opencv_device import OpenCVCapture

        return OpenCVCapture(config)

    def _note(self, action: str, reason: str, consumer: str, duration_ns: int) -> None:
        event = LifecycleEvent(action, reason, consumer, time.perf_counter_ns(), duration_ns)
        self._events.append(event)
        if len(self._events) > _MAX_EVENTS:
            del self._events[: len(self._events) - _MAX_EVENTS]
        logger.debug(
            "CAMERA_LIFECYCLE action=%s consumer=%s reason=%s duration_ms=%.1f",
            action,
            consumer or "-",
            reason,
            duration_ns / 1_000_000,
        )
        record(
            action,
            module="timing_camera",
            page="camera",
            result=reason,
            consumer=consumer or "-",
        )


def _identity(config: CameraConfig) -> tuple[int, int, int, int, str]:
    return (config.device_index, config.width, config.height, config.fps, config.backend)


def _reason(reason: str) -> None:
    if not isinstance(reason, str) or reason.strip() == "":
        raise ValueError("reason must be a non-empty string")


def _consumer(name: str) -> None:
    if not isinstance(name, str) or name.strip() == "":
        raise ValueError("consumer must be a non-empty string")
