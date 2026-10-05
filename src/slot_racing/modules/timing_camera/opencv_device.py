"""OpenCV camera. This is the only module that imports ``cv2``.

Requested width, height and frame rate are hints. The values the driver reports
after opening are stored separately and are not treated as the request.
"""

from __future__ import annotations

from typing import Any

from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import CameraOpenError, CameraReadError
from slot_racing.modules.timing_camera.frames import GrayFrame


class OpenCVCapture:
    """One ``cv2.VideoCapture``. Open it, read grayscale frames, then release it."""

    def __init__(self, config: CameraConfig) -> None:
        if not isinstance(config, CameraConfig):
            raise TypeError("config must be a CameraConfig")
        self._config = config
        self._capture: Any = None
        self.actual_width: int | None = None
        self.actual_height: int | None = None
        self.actual_fps: float | None = None

    def open(self) -> None:
        cv2 = _cv2()
        capture = cv2.VideoCapture(self._config.device_index)
        try:
            if not capture.isOpened():
                raise CameraOpenError(
                    f"camera device {self._config.device_index} could not be opened"
                )
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._config.width))
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._config.height))
            capture.set(cv2.CAP_PROP_FPS, float(self._config.fps))
            # One buffered picture. A deeper driver queue would hand us stale frames.
            capture.set(getattr(cv2, "CAP_PROP_BUFFERSIZE", 38), 1)
            self.actual_width = _reported_size(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.actual_height = _reported_size(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.actual_fps = _reported_fps(capture.get(cv2.CAP_PROP_FPS))
            self._capture = capture
        except Exception:
            capture.release()
            raise

    def read(self) -> GrayFrame:
        capture = self._capture
        if capture is None:
            raise CameraReadError("camera is not open")
        cv2 = _cv2()
        ok, image = capture.read()
        if not ok or image is None:
            raise CameraReadError("camera frame is missing")
        return _as_gray_frame(cv2, image)

    def close(self) -> None:
        capture = self._capture
        self._capture = None
        if capture is not None:
            capture.release()


def probe_device_indices(limit: int = 5) -> tuple[int, ...]:
    """Open each index briefly and close it again. Nothing is left running.

    A failure to open one index is skipped. The probe itself must not take the
    application down when no camera is attached.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be >= 1")
    cv2 = _cv2()
    found: list[int] = []
    for index in range(limit):
        try:
            capture = cv2.VideoCapture(index)
        except Exception:
            continue
        try:
            if bool(capture.isOpened()):
                found.append(index)
        except Exception:
            continue
        finally:
            capture.release()
    return tuple(found)


def _cv2() -> Any:
    import cv2

    return cv2


def _reported_size(value: object) -> int | None:
    """The driver's reported side length, or ``None`` when it reports nothing usable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    size = int(value)
    return size if size > 0 else None


def _reported_fps(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    fps = float(value)
    return fps if fps > 0 else None


def _as_gray_frame(cv2: Any, image: Any) -> GrayFrame:
    if int(getattr(image, "ndim", 0)) == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = image.shape[:2]
    return GrayFrame(int(width), int(height), _pixel_bytes(image))


def _pixel_bytes(image: Any) -> bytes:
    """Packed grayscale bytes without building one Python int per pixel."""
    tobytes = getattr(image, "tobytes", None)
    if callable(tobytes):
        raw = tobytes()
        if isinstance(raw, bytes):
            return raw
    flat = image.reshape(-1)
    return bytes(int(value) & 0xFF for value in flat)
