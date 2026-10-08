"""How a camera device is opened.

Detection zones are not part of this object. The saved document that holds
both lives in ``configuration`` and is read before a session starts.
"""

from __future__ import annotations

from dataclasses import dataclass

from slot_racing.modules.timing_camera._checks import require_range

_BACKENDS = ("", "any", "dshow", "msmf")


@dataclass(frozen=True, slots=True)
class CameraChoice:
    """One camera the setup page can offer.

    ``name`` is the driver label when one is available, otherwise empty.
    ``backend`` is ``dshow`` or ``msmf`` on Windows, ``any`` for the platform
    default, and empty for a saved index that was not probed.
    """

    index: int
    name: str = ""
    backend: str = ""

    def __post_init__(self) -> None:
        require_range("index", self.index, 0)
        if not isinstance(self.name, str):
            raise TypeError("name must be a str")
        if self.backend not in _BACKENDS:
            raise ValueError("backend must be empty, any, dshow or msmf")


@dataclass(frozen=True, slots=True)
class CameraConfig:
    """Device hint for one camera. The driver may choose different actual values.

    ``device_index`` selects the camera (``0`` is the usual first device).
    ``width``, ``height`` and ``fps`` are requested, not guaranteed.
    ``backend`` selects DirectShow or Media Foundation on Windows. Empty lets
    the capture try DirectShow first, which is where virtual cameras appear.
    """

    device_index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30
    backend: str = ""

    def __post_init__(self) -> None:
        require_range("device_index", self.device_index, 0)
        require_range("width", self.width, 1)
        require_range("height", self.height, 1)
        require_range("fps", self.fps, 1)
        if self.backend not in _BACKENDS:
            raise ValueError("backend must be empty, any, dshow or msmf")
