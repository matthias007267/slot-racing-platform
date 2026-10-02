"""Camera timing provider.

Frames come from a :class:`~slot_racing.modules.timing_camera.frame_source.FrameSource`.
The detector from the recognition step turns each frame into lane crossings, and
this module translates those crossings into :class:`~slot_racing.core.events.SensorTriggered`
events. The sensor id is taken from the session setup. No camera device is opened.
"""

from __future__ import annotations

from slot_racing.core.errors import ProviderConfigurationError
from slot_racing.core.events import SensorTriggered
from slot_racing.core.timing import (
    ProviderAvailability,
    ProviderCapabilities,
    SensorSink,
    TimingSessionSpec,
    TimingSource,
    TimingSourceFactory,
)
from slot_racing.modules.timing_camera.detection import (
    DetectorSettings,
    LaneCrossingDetector,
)
from slot_racing.modules.timing_camera.frame_source import FrameSource, ManualFrameSource
from slot_racing.modules.timing_camera.frames import GrayFrame

PROVIDER_ID = "camera"
_SOURCE_ID = "camera"


class CameraTimingProvider(TimingSource):
    """Reports a car entering a configured zone as a standardized sensor event.

    The host calls :meth:`poll`. There is no capture thread. While paused, ``poll``
    delivers nothing. ``stop`` can be called any number of times.
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
        self._frames.start()
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
        self._frames.resume()

    def poll(self) -> None:
        """Turn every frame that is due into sensor events.

        Crossings keep the order and the timestamp produced by the detector.
        """
        if self._sink is None or self._paused:
            return
        detector = self._detector
        while self._sink is not None and not self._paused:
            delivered = self._frames.poll_frame()
            if delivered is None:
                return
            if detector is None:
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

    The provider is registered even though no camera is connected.
    :meth:`availability` stays unavailable until a later step can see real hardware.
    :meth:`create_source` still builds a source: tests pass a frame source and the
    detection zones. The plugin registers a factory without either, so it never
    opens a device.

    ``frames`` and ``settings``, when given, are shared by every source this factory
    creates. The plugin passes neither, and each race then gets its own idle frame source.
    """

    def __init__(
        self,
        frames: FrameSource | None = None,
        settings: DetectorSettings | None = None,
        background: GrayFrame | None = None,
    ) -> None:
        if frames is not None and not isinstance(frames, FrameSource):
            raise TypeError("frames must be a FrameSource")
        if settings is not None and not isinstance(settings, DetectorSettings):
            raise TypeError("settings must be DetectorSettings")
        self._frames = frames
        self._settings = settings
        self._background = background

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_multiple_lanes=True, supports_test_mode=False)

    def availability(self) -> ProviderAvailability:
        """No camera device is opened or claimed here. Hardware comes later."""
        return ProviderAvailability.unavailable("error.timing_provider.camera_not_connected")

    def validate(self, spec: TimingSessionSpec) -> None:
        if len(set(spec.lanes)) != len(spec.lanes):
            raise ProviderConfigurationError("error.timing_provider.lanes_duplicate")
        _require_known_positions(
            self._settings,
            {sensor.position_id: sensor.id for sensor in spec.setup.sensors if sensor.active},
        )

    def create_source(self, spec: TimingSessionSpec) -> TimingSource:
        self.validate(spec)
        frames = self._frames if self._frames is not None else ManualFrameSource()
        return CameraTimingProvider(spec, frames, self._settings, self._background)


def _require_known_positions(settings: DetectorSettings | None, sensors: dict[str, str]) -> None:
    if settings is None:
        return
    for zone in settings.zones:
        if zone.position_id not in sensors:
            raise ProviderConfigurationError(
                "error.timing_provider.camera_position_unknown",
                position=zone.position_id,
            )
