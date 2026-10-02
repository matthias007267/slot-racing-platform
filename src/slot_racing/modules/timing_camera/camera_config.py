"""How a camera device is opened. Detection zones are a separate configuration."""

from __future__ import annotations

from dataclasses import dataclass

from slot_racing.modules.timing_camera._checks import require_range


@dataclass(frozen=True, slots=True)
class CameraConfig:
    """Device hint for one camera. The driver may choose different actual values.

    ``device_index`` selects the camera (``0`` is the usual first device).
    ``width``, ``height`` and ``fps`` are requested, not guaranteed.
    """

    device_index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30

    def __post_init__(self) -> None:
        require_range("device_index", self.device_index, 0)
        require_range("width", self.width, 1)
        require_range("height", self.height, 1)
        require_range("fps", self.fps, 1)
