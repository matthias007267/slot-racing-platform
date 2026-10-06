"""Camera preview for the setup page.

With a :class:`~slot_racing.modules.timing_camera.session.CameraSession` the
preview is only a consumer. Hiding the page detaches that consumer and leaves
the device open. Without a session, ``open`` still starts its own capture and
``stop`` releases it; tests use that path.
"""

from __future__ import annotations

from collections.abc import Callable

from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import (
    CameraFrameSource,
    CaptureDevice,
    LatestFrameBuffer,
)
from slot_racing.modules.timing_camera.lease import CameraLease
from slot_racing.modules.timing_camera.session import (
    CameraSession,
    PipelineSnapshot,
    SessionConsumer,
)

DeviceFactory = Callable[[CameraConfig], CaptureDevice]
Probe = Callable[[], tuple[int, ...]]


class CameraPreview:
    """Opens a preview source. Tests replace this with a fake that has no device."""

    def __init__(
        self,
        lease: CameraLease,
        *,
        devices: DeviceFactory | None = None,
        probe: Probe | None = None,
        session: CameraSession | None = None,
    ) -> None:
        if not isinstance(lease, CameraLease):
            raise TypeError("lease must be a CameraLease")
        if devices is not None and not callable(devices):
            raise TypeError("devices must be a callable")
        if probe is not None and not callable(probe):
            raise TypeError("probe must be a callable")
        if session is not None and not isinstance(session, CameraSession):
            raise TypeError("session must be a CameraSession")
        self._lease = lease
        self._devices = devices
        self._probe = probe
        self._session = session

    def device_indices(self) -> tuple[int, ...]:
        """Indices that can be shown without opening a device.

        A session reports only the camera it already holds. Listing devices
        must not open and close every index while the user is navigating.
        """
        if self._session is not None:
            return self._session.known_indices()
        if self._lease.holder() is not None:
            return ()
        return self._probe_now()

    def probe_indices(self) -> tuple[int, ...]:
        """Enumerate devices. Skipped while a session is already capturing."""
        if self._session is not None and self._session.is_open:
            return self._session.known_indices()
        if self._lease.holder() is not None:
            return ()
        return self._probe_now()

    def pipeline(self) -> PipelineSnapshot | None:
        if self._session is None:
            return None
        return self._session.pipeline()

    def attach_consumer(self, name: str, *, reason: str) -> LatestFrameBuffer:
        """Subscribe to the open capture. Does not open a second device."""
        if self._session is None or not self._session.is_open:
            raise RuntimeError("camera capture is not active")
        return self._session.attach(name, reason=reason)

    def detach_consumer(self, name: str, *, reason: str) -> None:
        if self._session is not None:
            self._session.detach(name, reason=reason)

    def open(self, config: CameraConfig) -> CameraFrameSource | SessionConsumer:
        """Start a preview. A session reuses the camera that is already open."""
        if not isinstance(config, CameraConfig):
            raise TypeError("config must be a CameraConfig")
        if self._session is not None:
            return self._session.open_preview(config)
        device = self._device(config)
        source = CameraFrameSource(device, lease=self._lease, lease_owner=CameraLease.PREVIEW)
        source.start()
        return source

    def _probe_now(self) -> tuple[int, ...]:
        if self._probe is not None:
            return self._probe()
        from slot_racing.modules.timing_camera.opencv_device import probe_device_indices

        return probe_device_indices()

    def _device(self, config: CameraConfig) -> CaptureDevice:
        if self._devices is not None:
            return self._devices(config)
        from slot_racing.modules.timing_camera.opencv_device import OpenCVCapture

        return OpenCVCapture(config)
