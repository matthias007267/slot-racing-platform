"""Camera timing provider.

Frames come from a :class:`~slot_racing.modules.timing_camera.frame_source.FrameSource`.
A hardware camera is read on a capture thread. Detection runs on its own worker
as soon as a new frame is the latest one, and stores ``SensorTriggered`` values
until :meth:`CameraTimingProvider.poll` forwards them. Poll does not wait and
does not detect for that camera. A manual frame source has no worker, so poll
still detects. The sensor id is taken from the session setup.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from slot_racing.core.diagnostics import record
from slot_racing.core.errors import ProviderConfigurationError, ProviderUnavailable
from slot_racing.core.events import SensorTriggered
from slot_racing.core.timing import (
    ProviderAvailability,
    ProviderCapabilities,
    SensorSink,
    TimingSessionSpec,
    TimingSource,
    TimingSourceFactory,
)
from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import (
    MAX_FRAMES_PER_POLL,
    CameraFrameSource,
    CameraOpenError,
    CameraReadError,
    CaptureDevice,
)
from slot_racing.modules.timing_camera.configuration import (
    CameraConfiguration,
    scale_detector_settings,
    to_camera_config,
    to_detector_settings,
)
from slot_racing.modules.timing_camera.detection import (
    DetectorSettings,
    LaneCrossing,
    LaneCrossingDetector,
    create_lane_detector,
)
from slot_racing.modules.timing_camera.frame_source import FrameSource, TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.lease import CameraBusyError, CameraLease
from slot_racing.modules.timing_camera.session import CameraSession
from slot_racing.modules.timing_camera.store import (
    CameraConfigurationError,
    CameraConfigurationSource,
)

DeviceFactory = Callable[[CameraConfig], CaptureDevice]

logger = logging.getLogger(__name__)

PROVIDER_ID = "camera"
_SOURCE_ID = "camera"
# A start click checks availability more than once. Reopening the device each
# time stalls the window and can make the following open fail.
_PROBE_TTL_NS = 2_000_000_000


class CameraTimingProvider(TimingSource):
    """Reports a car entering a configured zone as a standardized sensor event.

    The host calls :meth:`poll`. While paused, ``poll`` delivers nothing. After a
    camera resume the first new frame only resynchronizes detection. ``stop`` can
    be called any number of times.
    """

    def __init__(
        self,
        spec: TimingSessionSpec,
        frames: FrameSource,
        settings: DetectorSettings | None = None,
        background: GrayFrame | None = None,
        source_id: str = _SOURCE_ID,
        zone_frame: tuple[int, int] | None = None,
    ) -> None:
        if not isinstance(spec, TimingSessionSpec):
            raise TypeError("spec must be a TimingSessionSpec")
        if not isinstance(frames, FrameSource):
            raise TypeError("frames must be a FrameSource")
        if settings is not None and not isinstance(settings, DetectorSettings):
            raise TypeError("settings must be DetectorSettings")
        if not isinstance(source_id, str) or source_id.strip() == "":
            raise ValueError("source_id must be a non-empty string")
        if zone_frame is not None and not _positive_frame(zone_frame):
            raise ValueError("zone_frame must be a positive width and height")
        self._spec = spec
        self._frames = frames
        self._settings = settings
        self._background = background
        self._source_id = source_id
        self._zone_frame = zone_frame
        self._sensors = {
            sensor.position_id: sensor.id for sensor in spec.setup.sensors if sensor.active
        }
        _require_known_positions(settings, self._sensors)
        self._detector: LaneCrossingDetector | None = None
        self._sink: SensorSink | None = None
        self._paused = False
        self._resync = False
        self._regions_armed = False
        self._pending: deque[SensorTriggered] = deque()
        self._pending_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._worker_stop = threading.Event()
        self._resume_gate = threading.Event()
        self._resume_gate.set()
        self._halted = False
        self._failure: BaseException | None = None
        self._noted_first_frame = False
        self.frames_observed = 0
        self.detection_ns_total = 0
        self.detection_ns_max = 0
        self.latency_ns_total = 0
        self.latency_ns_max = 0
        self._reset_detector()

    @property
    def source_id(self) -> str:
        return self._source_id

    def reported_positions(self) -> frozenset[str] | None:
        settings = self._settings
        if settings is None or not settings.zones:
            return None
        return frozenset(zone.position_id for zone in settings.zones)

    @property
    def is_running(self) -> bool:
        return self._sink is not None

    def start(self, sink: SensorSink) -> None:
        if self._sink is not None:
            raise RuntimeError("camera timing source is already running")
        self._reset_detector()
        self._paused = False
        self._resync = False
        self._regions_armed = False
        self._failure = None
        self._halted = False
        self._noted_first_frame = False
        self.frames_observed = 0
        self.detection_ns_total = 0
        self.detection_ns_max = 0
        self.latency_ns_total = 0
        self.latency_ns_max = 0
        with self._pending_lock:
            self._pending.clear()
        self._publish_regions(())
        try:
            self._frames.start()
        except CameraBusyError as error:
            self._frames.stop()
            raise ProviderUnavailable("error.timing_provider.camera_in_use") from error
        except CameraOpenError as error:
            self._frames.stop()
            raise ProviderUnavailable("error.timing_provider.camera_not_connected") from error
        except Exception:
            self._frames.stop()
            raise
        self._sink = sink
        self._note_detector("created")
        self._worker_stop.clear()
        self._resume_gate.set()
        if self._has_capture_thread():
            self._worker = threading.Thread(
                target=self._detect, name="slot-racing-detection", daemon=True
            )
            self._worker.start()

    def stop(self) -> None:
        if self._halted:
            return
        self._halted = True
        try:
            self._worker_stop.set()
            self._resume_gate.set()
            wake = getattr(self._frames, "wake", None)
            if callable(wake):
                wake()
            self._join_worker()
            # Hand crossings that already finished to the race before the sink goes away.
            self._drain_events()
            self._sink = None
            self._paused = False
            self._resync = False
            self._regions_armed = False
            self._detector = None
            self._publish_regions(())
            self._frames.stop()
        finally:
            self._halted = False

    def pause(self) -> None:
        if self._sink is not None and not self._paused:
            self._paused = True
            self._resume_gate.clear()
            self._frames.pause()
            drop = getattr(self._frames, "clear_unread", None)
            if callable(drop):
                drop()

    def resume(self) -> None:
        if self._sink is None or not self._paused:
            return
        # Drop the unread frame while the worker still treats the race as paused,
        # then allow detection, then let the capture thread publish again.
        drop = getattr(self._frames, "clear_unread", None)
        if callable(drop):
            drop()
        self._resync = True
        self._paused = False
        self._resume_gate.set()
        if not self._frames.resume():
            self._resync = False

    def poll(self) -> None:
        """Deliver events that are already detected, or drive a host-fed source.

        A live camera detects on its own worker. This call only forwards the
        crossings that worker stored, on the caller's thread, and returns
        without waiting. A manual frame source has no worker: this call still
        detects, and it still stops after
        :data:`~slot_racing.modules.timing_camera.capture.MAX_FRAMES_PER_POLL`
        frames. The event timestamp is the frame's own ``timestamp_ns``.
        """
        self._frames.check()
        failure = self._failure
        if failure is not None:
            raise failure
        if self._worker is not None:
            self._drain_events()
            return
        if self._sink is None or self._paused:
            return
        processed = 0
        while self._sink is not None and not self._paused and processed < MAX_FRAMES_PER_POLL:
            delivered = self._frames.poll_frame()
            if delivered is None:
                return
            processed += 1
            self._note_first_frame(delivered)
            for crossing in self._crossings(delivered):
                sink = self._sink
                if sink is None:
                    return
                sink(self._event(crossing))

    def caught_up(self) -> bool:
        """True when detection has finished every frame the capture thread kept.

        Host-driven sources are always caught up: their frames wait for ``poll``.
        """
        if self._worker is None:
            return True
        idle = getattr(self._frames, "detection_idle", None)
        if not callable(idle):
            return True
        return bool(idle())

    def _has_capture_thread(self) -> bool:
        return callable(getattr(self._frames, "wait_frame", None))

    def _join_worker(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None and worker.is_alive() and threading.current_thread() is not worker:
            worker.join(timeout=2)
            if worker.is_alive():
                self._worker = worker
                raise RuntimeError("camera detection thread did not stop")

    def _detect(self) -> None:
        """Wait for a frame, detect, store the crossings, then wait again.

        The sink is not called here. Race handlers touch the UI and the
        database, and both belong on the thread that polls.
        """
        while not self._worker_stop.is_set():
            if self._paused:
                self._resume_gate.wait()
                continue
            try:
                self._frames.check()
            except CameraReadError as error:
                self._note_worker_failure(error)
                return
            frame = self._take_frame()
            if frame is None:
                return
            if self._paused or self._worker_stop.is_set():
                self._release_frame()
                continue
            started = time.perf_counter_ns()
            try:
                crossings = self._crossings(frame)
            except Exception as error:
                self._failure = error
                self._note_worker_failure(error)
                self._release_frame()
                return
            self._note_first_frame(frame)
            elapsed = time.perf_counter_ns() - started
            latency = max(0, started - frame.timestamp_ns)
            self.frames_observed += 1
            self.detection_ns_total += elapsed
            self.detection_ns_max = max(self.detection_ns_max, elapsed)
            self.latency_ns_total += latency
            self.latency_ns_max = max(self.latency_ns_max, latency)
            self._store(crossings)
            self._release_frame()

    def _take_frame(self) -> TimedFrame | None:
        wait = getattr(self._frames, "wait_frame", None)
        if not callable(wait):
            return None
        frame = wait(self._worker_stop)
        if isinstance(frame, TimedFrame):
            return frame
        return None

    def _release_frame(self) -> None:
        ack = getattr(self._frames, "ack_frame", None)
        if callable(ack):
            ack()

    def _event(self, crossing: LaneCrossing) -> SensorTriggered:
        return SensorTriggered(
            timestamp_ns=crossing.timestamp_ns,
            source_id=self._source_id,
            sensor_id=self._sensors[crossing.position_id],
            position_id=crossing.position_id,
            lane=crossing.lane,
        )

    def _store(self, crossings: tuple[LaneCrossing, ...]) -> None:
        """Keep every crossing. Frames may be skipped; these events may not."""
        if not crossings:
            return
        for crossing in crossings:
            record(
                "CAMERA_RACE_DETECTION",
                module="timing_camera",
                page="camera",
                result="accepted",
                source=self._source_id,
                lane=crossing.lane,
                position=crossing.position_id,
            )
        with self._pending_lock:
            self._pending.extend(self._event(crossing) for crossing in crossings)

    def _note_first_frame(self, frame: TimedFrame) -> None:
        if self._noted_first_frame:
            return
        self._noted_first_frame = True
        record(
            "CAMERA_RACE_FRAME_FIRST",
            module="timing_camera",
            page="camera",
            result="observed",
            source=self._source_id,
            capture_seq=frame.sequence,
        )

    def _note_detector(self, result: str) -> None:
        settings = self._settings
        record(
            "CAMERA_RACE_DETECTOR_CREATED",
            module="timing_camera",
            page="camera",
            result=result,
            source=self._source_id,
            zones=0 if settings is None else len(settings.zones),
            detector=0 if self._detector is None else id(self._detector),
        )

    def _note_worker_failure(self, error: BaseException) -> None:
        logger.exception("Camera detection worker failed")
        record(
            "CAMERA_RACE_WORKER_FAILED",
            module="timing_camera",
            page="camera",
            result=type(error).__name__,
            source=self._source_id,
        )

    def _drain_events(self) -> None:
        while True:
            with self._pending_lock:
                if not self._pending:
                    return
                event = self._pending.popleft()
            sink = self._sink
            if sink is None:
                return
            sink(event)

    def _match_delivered_frame(self, frame: GrayFrame) -> None:
        """Map saved zones onto the picture the camera actually delivered.

        Pixel zones are built for the resolution stored with them. Drivers often
        ignore that request. The fractions stay the same; only the pixel grid
        changes. An injected zone list without ``zone_frame`` is left alone, so
        tests that already speak in pixels keep those pixels.
        """
        reference = self._zone_frame
        settings = self._settings
        if reference is None or settings is None:
            return
        if (frame.width, frame.height) == reference:
            return
        scaled = scale_detector_settings(
            settings, reference[0], reference[1], frame.width, frame.height
        )
        background = _background_for_frame(self._background, frame.width, frame.height)
        self._settings = scaled
        self._zone_frame = (frame.width, frame.height)
        self._background = background
        self._detector = create_lane_detector(scaled, background=background)
        self._note_detector("rescaled")
        # The next picture may have a different grid, so the crops are prepared again.
        self._regions_armed = False

    def _crossings(self, delivered: TimedFrame) -> tuple[LaneCrossing, ...]:
        crops = delivered.crops
        if crops is not None:
            detector = self._detector
            if detector is None:
                return ()
            if self._resync:
                detector.synchronize_crops(crops)
                self._resync = False
                return ()
            return detector.observe_crops(crops, delivered.timestamp_ns)
        frame = delivered.frame
        self._match_delivered_frame(frame)
        self._arm_regions()
        detector = self._detector
        if detector is None:
            return ()
        if self._resync:
            detector.synchronize(frame)
            self._resync = False
            return ()
        return detector.observe(frame, delivered.timestamp_ns)

    def _arm_regions(self) -> None:
        """Cut later frames to the zones of the picture that was just measured.

        The first frame is still the whole picture: its size is what the saved
        fractions are mapped onto. Only the frames after that skip the rest.
        """
        if self._regions_armed:
            return
        settings = self._settings
        if settings is None:
            self._regions_armed = True
            return
        self._publish_regions(tuple(zone.roi for zone in settings.zones))
        self._regions_armed = True

    def _publish_regions(self, regions: tuple[DetectionRoi, ...]) -> None:
        prepare = getattr(self._frames, "use_regions", None)
        if callable(prepare):
            prepare(regions)

    def _reset_detector(self) -> None:
        settings = self._settings
        self._detector = (
            None
            if settings is None
            else create_lane_detector(settings, background=self._background)
        )


class CameraTimingFactory(TimingSourceFactory):
    """Creates a camera timing source for one race.

    :meth:`availability` opens the configured device only for the check and
    closes it again. A missing camera is ``unavailable`` and does not crash the
    application. Passing ``frames`` builds a source around that source instead
    of a device, which is how tests run without hardware; that factory does not
    claim a camera is connected.

    ``frames``, ``settings`` and ``camera``, when given, replace the saved
    configuration for every source this factory creates. Without them, and when
    ``configurations`` is set, the saved document is read once per session and
    handed to the source. Nothing is read again while that source is running.
    """

    def __init__(
        self,
        frames: FrameSource | None = None,
        settings: DetectorSettings | None = None,
        background: GrayFrame | None = None,
        camera: CameraConfig | None = None,
        devices: DeviceFactory | None = None,
        configurations: CameraConfigurationSource | None = None,
        lease: CameraLease | None = None,
        session: CameraSession | None = None,
    ) -> None:
        if frames is not None and not isinstance(frames, FrameSource):
            raise TypeError("frames must be a FrameSource")
        if settings is not None and not isinstance(settings, DetectorSettings):
            raise TypeError("settings must be DetectorSettings")
        if camera is not None and not isinstance(camera, CameraConfig):
            raise TypeError("camera must be a CameraConfig")
        if devices is not None and not callable(devices):
            raise TypeError("devices must be a callable")
        if configurations is not None and not callable(getattr(configurations, "load", None)):
            raise TypeError("configurations must load a camera configuration")
        if lease is not None and not isinstance(lease, CameraLease):
            raise TypeError("lease must be a CameraLease")
        if session is not None and not isinstance(session, CameraSession):
            raise TypeError("session must be a CameraSession")
        self._frames = frames
        self._settings = settings
        self._background = background
        self._camera = camera
        self._devices = devices
        self._configurations = configurations
        self._lease = lease
        self._session = session
        self._probe_cache: (
            tuple[tuple[int, int, int, int, str], int, ProviderAvailability] | None
        ) = None

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_multiple_lanes=True, supports_test_mode=False)

    def availability(self) -> ProviderAvailability:
        """Probe the device, then close it. An injected frame source is not hardware.

        A successful probe is reused for a short moment so the race wizard and
        the following start do not open and close the camera three times.
        """
        if self._frames is not None:
            return ProviderAvailability.unavailable("error.timing_provider.camera_not_connected")
        try:
            camera = self._session_camera()
        except ProviderConfigurationError as error:
            return ProviderAvailability.unavailable(error.key)
        # Navigation asks this on every page. A session must not open the device
        # to answer. The device opens when a preview or a race actually starts.
        if self._session is not None:
            if self._session.hardware_state(camera) == "failed":
                return ProviderAvailability.unavailable(
                    "error.timing_provider.camera_not_connected"
                )
            return ProviderAvailability.ok()
        key = (camera.device_index, camera.width, camera.height, camera.fps, camera.backend)
        cached = self._probe_cache
        now = time.monotonic_ns()
        if cached is not None and cached[0] == key and now - cached[1] < _PROBE_TTL_NS:
            return cached[2]
        result = self._probe_open(camera)
        self._probe_cache = (key, now, result) if result.available else None
        return result

    def _probe_open(self, camera: CameraConfig) -> ProviderAvailability:
        if self._lease is not None and self._lease.holder() is not None:
            return ProviderAvailability.unavailable("error.timing_provider.camera_in_use")
        try:
            device = self._make_device(camera)
        except ProviderConfigurationError as error:
            return ProviderAvailability.unavailable(error.key)
        opened = False
        try:
            device.open()
            opened = True
        except Exception:
            opened = False
        finally:
            try:
                device.close()
            except Exception:
                opened = False
        if opened:
            return ProviderAvailability.ok()
        return ProviderAvailability.unavailable("error.timing_provider.camera_not_connected")

    def validate(self, spec: TimingSessionSpec) -> None:
        self._prepare(spec)

    def create_source(self, spec: TimingSessionSpec) -> TimingSource:
        """Build a source from the configuration resolved for this call.

        The returned source keeps that snapshot. Later reads of the saved
        document do not affect it, and the capture thread does not load it.
        """
        camera, settings, zone_frame = self._prepare(spec)
        if self._frames is not None:
            frames = self._frames
        elif self._session is not None:
            frames = self._session.race_consumer(camera)
        else:
            frames = CameraFrameSource(
                self._make_device(camera),
                lease=self._lease,
                lease_owner=CameraLease.RACE,
            )
        return CameraTimingProvider(spec, frames, settings, self._background, zone_frame=zone_frame)

    def _prepare(
        self, spec: TimingSessionSpec
    ) -> tuple[CameraConfig, DetectorSettings | None, tuple[int, int] | None]:
        if len(set(spec.lanes)) != len(spec.lanes):
            raise ProviderConfigurationError("error.timing_provider.lanes_duplicate")
        stored = self._stored_configuration()
        camera = self._camera if self._camera is not None else _camera_or_default(stored)
        zone_frame: tuple[int, int] | None = None
        if self._settings is not None:
            settings: DetectorSettings | None = self._settings
        elif stored is not None:
            settings = _zones_from_stored(stored)
            zone_frame = (stored.camera.width, stored.camera.height)
        else:
            settings = None
        _require_known_positions(
            settings,
            {sensor.position_id: sensor.id for sensor in spec.setup.sensors if sensor.active},
        )
        return camera, settings, zone_frame

    def _stored_configuration(self) -> CameraConfiguration | None:
        """The saved document when this factory still needs something from it."""
        if self._configurations is None:
            return None
        if self._camera is not None and self._settings is not None:
            return None
        try:
            return self._configurations.load()
        except CameraConfigurationError as error:
            raise ProviderConfigurationError(
                "error.timing_provider.camera_configuration_invalid"
            ) from error

    def _session_camera(self) -> CameraConfig:
        if self._camera is not None:
            return self._camera
        stored = self._stored_configuration()
        return _camera_or_default(stored)

    def _make_device(self, camera: CameraConfig) -> CaptureDevice:
        if self._devices is not None:
            return self._devices(camera)
        from slot_racing.modules.timing_camera.opencv_device import OpenCVCapture

        return OpenCVCapture(camera)


def _positive_frame(zone_frame: tuple[int, int]) -> bool:
    if not isinstance(zone_frame, tuple) or len(zone_frame) != 2:
        return False
    return all(
        isinstance(side, int) and not isinstance(side, bool) and side >= 1 for side in zone_frame
    )


def _background_for_frame(
    background: GrayFrame | None, width: int, height: int
) -> GrayFrame | None:
    """Keep an empty reference when the camera picture is a different size.

    A uniform background means "empty track" at the resolution it was built for.
    The same fill at the delivered size is that reference. A background that
    already contains a picture cannot be reinterpreted, so the size mismatch
    stays visible.
    """
    if background is None or (background.width, background.height) == (width, height):
        return background
    raw = background.to_bytes()
    fill = raw[0]
    if any(pixel != fill for pixel in raw):
        raise ValueError("frame size must match the background")
    return GrayFrame.blank(width, height, fill)


def _camera_or_default(stored: CameraConfiguration | None) -> CameraConfig:
    if stored is None:
        return CameraConfig()
    return to_camera_config(stored)


def _zones_from_stored(stored: CameraConfiguration) -> DetectorSettings:
    try:
        settings = to_detector_settings(stored)
    except ValueError as error:
        raise ProviderConfigurationError(
            "error.timing_provider.camera_configuration_invalid"
        ) from error
    if settings is None:
        raise ProviderConfigurationError("error.timing_provider.camera_zones_missing")
    return settings


def _require_known_positions(settings: DetectorSettings | None, sensors: dict[str, str]) -> None:
    if settings is None:
        return
    for zone in settings.zones:
        if zone.position_id not in sensors:
            raise ProviderConfigurationError(
                "error.timing_provider.camera_position_unknown",
                position=zone.position_id,
            )
