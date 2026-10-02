"""Camera timing provider.

Frames come from a :class:`~slot_racing.modules.timing_camera.frame_source.FrameSource`.
A hardware camera is read on a capture thread; :meth:`CameraTimingProvider.poll`
is the only place that runs detection and emits sensor events. The sensor id is
taken from the session setup.
"""

from __future__ import annotations

from collections.abc import Callable

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
    CaptureDevice,
)
from slot_racing.modules.timing_camera.configuration import (
    CameraConfiguration,
    to_camera_config,
    to_detector_settings,
)
from slot_racing.modules.timing_camera.detection import (
    DetectorSettings,
    LaneCrossingDetector,
)
from slot_racing.modules.timing_camera.frame_source import FrameSource
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.store import (
    CameraConfigurationError,
    CameraConfigurationSource,
)

DeviceFactory = Callable[[CameraConfig], CaptureDevice]

PROVIDER_ID = "camera"
_SOURCE_ID = "camera"


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
    ) -> None:
        if not isinstance(spec, TimingSessionSpec):
            raise TypeError("spec must be a TimingSessionSpec")
        if not isinstance(frames, FrameSource):
            raise TypeError("frames must be a FrameSource")
        if settings is not None and not isinstance(settings, DetectorSettings):
            raise TypeError("settings must be DetectorSettings")
        if not isinstance(source_id, str) or source_id.strip() == "":
            raise ValueError("source_id must be a non-empty string")
        self._spec = spec
        self._frames = frames
        self._settings = settings
        self._background = background
        self._source_id = source_id
        self._sensors = {
            sensor.position_id: sensor.id for sensor in spec.setup.sensors if sensor.active
        }
        _require_known_positions(settings, self._sensors)
        self._detector: LaneCrossingDetector | None = None
        self._sink: SensorSink | None = None
        self._paused = False
        self._resync = False
        self._reset_detector()

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def is_running(self) -> bool:
        return self._sink is not None

    def start(self, sink: SensorSink) -> None:
        if self._sink is not None:
            raise RuntimeError("camera timing source is already running")
        self._reset_detector()
        self._paused = False
        self._resync = False
        try:
            self._frames.start()
        except CameraOpenError as error:
            self._frames.stop()
            raise ProviderUnavailable("error.timing_provider.camera_not_connected") from error
        except Exception:
            self._frames.stop()
            raise
        self._sink = sink

    def stop(self) -> None:
        self._sink = None
        self._paused = False
        self._frames.stop()

    def pause(self) -> None:
        if self._sink is not None and not self._paused:
            self._paused = True
            self._frames.pause()

    def resume(self) -> None:
        if self._sink is None or not self._paused:
            return
        self._paused = False
        self._resync = self._frames.resume()

    def poll(self) -> None:
        """Turn due frames into sensor events, keeping each grab timestamp.

        At most :data:`~slot_racing.modules.timing_camera.capture.MAX_FRAMES_PER_POLL`
        frames are handled, so a backlog cannot block the host. The event
        timestamp is the frame's own ``timestamp_ns``, never the time of this call.
        """
        self._frames.check()
        if self._sink is None or self._paused:
            return
        detector = self._detector
        processed = 0
        while self._sink is not None and not self._paused and processed < MAX_FRAMES_PER_POLL:
            delivered = self._frames.poll_frame()
            if delivered is None:
                return
            processed += 1
            if detector is None:
                continue
            if self._resync:
                detector.synchronize(delivered.frame)
                self._resync = False
                continue
            for crossing in detector.observe(delivered.frame, delivered.timestamp_ns):
                sink = self._sink
                if sink is None:
                    return
                sink(
                    SensorTriggered(
                        timestamp_ns=crossing.timestamp_ns,
                        source_id=self._source_id,
                        sensor_id=self._sensors[crossing.position_id],
                        position_id=crossing.position_id,
                        lane=crossing.lane,
                    )
                )

    def _reset_detector(self) -> None:
        settings = self._settings
        self._detector = (
            None
            if settings is None
            else LaneCrossingDetector(settings, background=self._background)
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
        self._frames = frames
        self._settings = settings
        self._background = background
        self._camera = camera
        self._devices = devices
        self._configurations = configurations

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_multiple_lanes=True, supports_test_mode=False)

    def availability(self) -> ProviderAvailability:
        """Probe the device, then close it. An injected frame source is not hardware."""
        if self._frames is not None:
            return ProviderAvailability.unavailable("error.timing_provider.camera_not_connected")
        try:
            device = self._make_device(self._session_camera())
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
        camera, settings = self._prepare(spec)
        if self._frames is not None:
            frames = self._frames
        else:
            frames = CameraFrameSource(self._make_device(camera))
        return CameraTimingProvider(spec, frames, settings, self._background)

    def _prepare(self, spec: TimingSessionSpec) -> tuple[CameraConfig, DetectorSettings | None]:
        if len(set(spec.lanes)) != len(spec.lanes):
            raise ProviderConfigurationError("error.timing_provider.lanes_duplicate")
        stored = self._stored_configuration()
        camera = self._camera if self._camera is not None else _camera_or_default(stored)
        if self._settings is not None:
            settings: DetectorSettings | None = self._settings
        elif stored is not None:
            settings = _zones_from_stored(stored)
        else:
            settings = None
        _require_known_positions(
            settings,
            {sensor.position_id: sensor.id for sensor in spec.setup.sensors if sensor.active},
        )
        return camera, settings

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
