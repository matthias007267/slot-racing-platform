"""Camera preview for the setup page.

This opens the device and returns grayscale frames. It does not detect cars,
publish sensor events or talk to the race engine. The capture thread is the
same one a race uses, and it shares that race's camera lease so the two cannot
open the device together.
"""

from __future__ import annotations

from collections.abc import Callable

from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import CameraFrameSource, CaptureDevice
from slot_racing.modules.timing_camera.lease import CameraLease

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
    ) -> None:
        if not isinstance(lease, CameraLease):
            raise TypeError("lease must be a CameraLease")
        if devices is not None and not callable(devices):
            raise TypeError("devices must be a callable")
        if probe is not None and not callable(probe):
            raise TypeError("probe must be a callable")
        self._lease = lease
        self._devices = devices
        self._probe = probe

    def device_indices(self) -> tuple[int, ...]:
        """Indices that open right now. An occupied camera is not probed."""
        if self._lease.holder() is not None:
            return ()
        if self._probe is not None:
            return self._probe()
        from slot_racing.modules.timing_camera.opencv_device import probe_device_indices

        return probe_device_indices()

    def open(self, config: CameraConfig) -> CameraFrameSource:
        """Start a capture source. Raises when the device is missing or already open."""
        if not isinstance(config, CameraConfig):
            raise TypeError("config must be a CameraConfig")
        device = self._device(config)
        source = CameraFrameSource(device, lease=self._lease, lease_owner=CameraLease.PREVIEW)
        source.start()
        return source

    def _device(self, config: CameraConfig) -> CaptureDevice:
        if self._devices is not None:
            return self._devices(config)
        from slot_racing.modules.timing_camera.opencv_device import OpenCVCapture

        return OpenCVCapture(config)
