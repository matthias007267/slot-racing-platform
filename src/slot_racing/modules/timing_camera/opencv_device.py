"""OpenCV camera. This is the only module that imports ``cv2``.

Requested width, height and frame rate are hints. The values the driver reports
after opening are stored separately and are not treated as the request.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

from slot_racing.core.diagnostics import record
from slot_racing.modules.timing_camera.camera_config import CameraChoice, CameraConfig
from slot_racing.modules.timing_camera.capture import CameraOpenError, CameraReadError
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi

logger = logging.getLogger(__name__)

_BACKEND_CONST = {"dshow": "CAP_DSHOW", "msmf": "CAP_MSMF", "any": "CAP_ANY"}
_BACKEND_FALLBACK = {"dshow": 700, "msmf": 1400, "any": 0}


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
        self.backend: str = ""
        self.device_name: str = ""
        self._regions: tuple[DetectionRoi, ...] = ()
        self._closed = False
        self._native = False

    def set_regions(self, regions: tuple[DetectionRoi, ...]) -> None:
        """Later reads convert only these rectangles. Empty restores the full frame."""
        if not isinstance(regions, tuple) or any(
            not isinstance(region, DetectionRoi) for region in regions
        ):
            raise TypeError("regions must be a tuple of DetectionRoi")
        self._regions = regions

    def read_zoned(self) -> tuple[int, int, tuple[GrayFrame, ...]] | None:
        """Zone crops of the next picture, or ``None`` when the full frame is still required.

        ``None`` does not read the camera. The caller then uses :meth:`read`.
        A zone that sticks out of the delivered picture is rejected and not clipped.
        """
        regions = self._regions
        if not regions:
            return None
        capture = self._capture
        if capture is None:
            raise CameraReadError("camera is not open")
        cv2 = _cv2()
        ok, image = capture.read()
        if not ok or image is None:
            raise CameraReadError("camera frame is missing")
        height, width = image.shape[:2]
        return (
            int(width),
            int(height),
            tuple(_crop_gray(cv2, image, int(width), int(height), roi) for roi in regions),
        )

    def open(self) -> None:
        cv2 = _cv2()
        self._closed = False
        self._native = False
        # Names use COM on this thread. Read them before the capture graph exists,
        # then release COM. Doing it afterwards can tear that graph down.
        names = _capture_names()
        opened: Any = None
        chosen = ""
        for backend_id, token in _open_backends(cv2, self._config.backend):
            capture = _open_capture(cv2, self._config.device_index, backend_id)
            if capture is None:
                _note_open(self._config, token, result="skipped", reason="constructor_failed")
                continue
            try:
                if not bool(capture.isOpened()):
                    capture.release()
                    _note_open(self._config, token, result="skipped", reason="not_opened")
                    continue
            except Exception:
                _release(capture)
                _note_open(self._config, token, result="skipped", reason="open_error")
                continue
            opened = capture
            chosen = token
            break
        if opened is None:
            _note_open(
                self._config,
                self._config.backend or "auto",
                result="failed",
                reason="not_opened",
            )
            raise CameraOpenError(f"camera device {self._config.device_index} could not be opened")
        if not self._bind(cv2, opened, chosen, names, apply_hints=True):
            raise CameraOpenError(f"camera device {self._config.device_index} could not be opened")

    def reopen_native(self) -> bool:
        """Open the same backend again without size, rate or buffer hints.

        DirectShow applies those hints by rebuilding the filter graph. iVCam and
        other virtual cameras can accept the request and then deliver no samples,
        so ``isOpened`` stays true while every ``read`` fails. The selected
        backend is kept. This does not run again once the native graph is open.
        """
        if (
            self._closed
            or self._native
            or self._capture is None
            or self.backend not in _BACKEND_CONST
        ):
            return False
        cv2 = _cv2()
        token = self.backend
        index = self._config.device_index
        name = self.device_name
        backend_id = _backend_id(cv2, _BACKEND_CONST[token], _BACKEND_FALLBACK[token])
        self._release_current()
        if self._closed:
            return False
        capture = _open_capture(cv2, index, backend_id)
        if capture is None:
            _note_open(self._config, token, result="native_failed", reason="constructor_failed")
            return False
        try:
            opened = bool(capture.isOpened())
        except Exception:
            _release(capture)
            _note_open(self._config, token, result="native_failed", reason="open_error")
            return False
        if not opened:
            _release(capture)
            _note_open(self._config, token, result="native_failed", reason="not_opened")
            return False
        names = {(token, index): name} if name else {}
        if not self._bind(cv2, capture, token, names, apply_hints=False):
            _note_open(self._config, token, result="native_failed", reason="closed")
            return False
        return True

    def _bind(
        self,
        cv2: Any,
        opened: Any,
        chosen: str,
        names: dict[tuple[str, int], str],
        *,
        apply_hints: bool,
    ) -> bool:
        try:
            if apply_hints:
                # A virtual camera may reject a size or rate. That must not close it.
                _request(opened, cv2.CAP_PROP_FRAME_WIDTH, float(self._config.width))
                _request(opened, cv2.CAP_PROP_FRAME_HEIGHT, float(self._config.height))
                _request(opened, cv2.CAP_PROP_FPS, float(self._config.fps))
                # One buffered picture. A deeper driver queue would hand us stale frames.
                _request(opened, getattr(cv2, "CAP_PROP_BUFFERSIZE", 38), 1)
            self.actual_width = _reported_size(opened.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.actual_height = _reported_size(opened.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.actual_fps = _reported_fps(opened.get(cv2.CAP_PROP_FPS))
            self.backend = chosen
            self.device_name = names.get((chosen, self._config.device_index), "")
            self._native = not apply_hints
            if self._closed:
                _release(opened)
                return False
            self._capture = opened
        except Exception:
            _release(opened)
            raise
        _note_open(
            self._config,
            chosen,
            result="opened" if apply_hints else "opened_native",
            reason="hints" if apply_hints else "native",
            actual_width=self.actual_width,
            actual_height=self.actual_height,
            actual_fps=self.actual_fps,
            name=self.device_name,
        )
        return True

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
        self._closed = True
        self._release_current()

    def _release_current(self) -> None:
        capture = self._capture
        self._capture = None
        if capture is not None:
            _release(capture)


def probe_device_indices(limit: int = 5) -> tuple[int, ...]:
    """Indices that opened on any backend. The first successful backend wins."""
    seen: list[int] = []
    for choice in probe_cameras(limit):
        if choice.index not in seen:
            seen.append(choice.index)
    return tuple(seen)


def probe_cameras(limit: int = 5) -> tuple[CameraChoice, ...]:
    """Open each index on each relevant backend and close it again.

    On Windows, DirectShow is listed first because iVCam, Camo and DroidCam
    register there. Media Foundation is listed as well when it opens the same
    index: the two backends can point at different devices. A failure to open
    one index is skipped.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be >= 1")
    cv2 = _cv2()
    names = _capture_names()
    found: list[CameraChoice] = []
    seen: set[tuple[str, int]] = set()
    for backend_id, token in _probe_backends(cv2):
        for index in range(limit):
            capture = _open_capture(cv2, index, backend_id)
            if capture is None:
                continue
            try:
                if not bool(capture.isOpened()):
                    continue
            except Exception:
                continue
            finally:
                _release(capture)
            key = (token, index)
            if key in seen:
                continue
            seen.add(key)
            found.append(CameraChoice(index, names.get(key, ""), token))
    return tuple(found)


def _open_backends(cv2: Any, backend: str) -> tuple[tuple[int, str], ...]:
    """Backends to try, in order. An explicit choice is not replaced."""
    if backend == "dshow":
        return ((_backend_id(cv2, "CAP_DSHOW", 700), "dshow"),)
    if backend == "msmf":
        return ((_backend_id(cv2, "CAP_MSMF", 1400), "msmf"),)
    if backend == "any":
        return ((_backend_id(cv2, "CAP_ANY", 0), "any"),)
    if _windows():
        return (
            (_backend_id(cv2, "CAP_DSHOW", 700), "dshow"),
            (_backend_id(cv2, "CAP_MSMF", 1400), "msmf"),
            (_backend_id(cv2, "CAP_ANY", 0), "any"),
        )
    return ((_backend_id(cv2, "CAP_ANY", 0), "any"),)


def _probe_backends(cv2: Any) -> tuple[tuple[int, str], ...]:
    if _windows():
        return (
            (_backend_id(cv2, "CAP_DSHOW", 700), "dshow"),
            (_backend_id(cv2, "CAP_MSMF", 1400), "msmf"),
        )
    return ((_backend_id(cv2, "CAP_ANY", 0), "any"),)


def _backend_id(cv2: Any, name: str, fallback: int) -> int:
    value = getattr(cv2, name, fallback)
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return value


def _windows() -> bool:
    return sys.platform == "win32"


def _open_capture(cv2: Any, index: int, backend: int) -> Any | None:
    try:
        return cv2.VideoCapture(index, backend)
    except Exception:
        return None


def _request(capture: Any, prop: int, value: float) -> None:
    """Ask for one setting. A refusal or an exception leaves the camera open."""
    try:
        capture.set(prop, value)
    except Exception:
        return


def _release(capture: Any) -> None:
    try:
        capture.release()
    except Exception:
        return


def _capture_names() -> dict[tuple[str, int], str]:
    """DirectShow friendly names, keyed by backend token and index."""
    if not _windows():
        return {}
    try:
        listed = _directshow_names()
    except Exception:
        return {}
    return {("dshow", index): name for index, name in enumerate(listed) if name.strip()}


def _directshow_names() -> tuple[str, ...]:
    """Friendly names in the same order OpenCV's DirectShow backend uses.

    Returns nothing when the platform is not Windows or the enumerator fails.
    """
    if sys.platform != "win32":
        return ()
    from ctypes import POINTER, byref, c_long, c_ulong, c_void_p

    hresult = c_long

    ole32 = ctypes.WinDLL("ole32")
    oleaut32 = ctypes.WinDLL("oleaut32")
    coinit = ole32.CoInitializeEx(None, 2)
    # 0x80010106: COM is already initialized on this thread.
    if coinit not in (0, 1, -2147417850):
        return ()
    dev_enum = c_void_p()
    enumerator = c_void_p()
    try:
        system = _guid(ole32, "{62BE5D10-60EB-11d0-BD3B-00A0C911CE86}")
        create_id = _guid(ole32, "{29840822-5B84-11D0-BD3B-00A0C911CE86}")
        category = _guid(ole32, "{860BB310-5D01-11d0-BD3B-00A0C911CE86}")
        bag_id = _guid(ole32, "{55272A00-42CB-11CE-8135-00AA004BB851}")
        hr = ole32.CoCreateInstance(byref(system), None, 1, byref(create_id), byref(dev_enum))
        if hr != 0 or not dev_enum:
            return ()
        create = _method(dev_enum, 3, hresult, POINTER(_GUID), POINTER(c_void_p), c_ulong)
        if create(dev_enum, byref(category), byref(enumerator), 0) != 0 or not enumerator:
            return ()
        names: list[str] = []
        nxt = _method(enumerator, 3, hresult, c_ulong, POINTER(c_void_p), POINTER(c_ulong))
        while True:
            moniker = c_void_p()
            fetched = c_ulong()
            if nxt(enumerator, 1, byref(moniker), byref(fetched)) != 0 or not moniker:
                break
            bag = c_void_p()
            try:
                bind = _method(
                    moniker, 9, hresult, c_void_p, c_void_p, POINTER(_GUID), POINTER(c_void_p)
                )
                if bind(moniker, None, None, byref(bag_id), byref(bag)) != 0 or not bag:
                    names.append("")
                    continue
                names.append(_friendly_name(oleaut32, bag))
            finally:
                _release_com(bag)
                _release_com(moniker)
        return tuple(names)
    finally:
        _release_com(enumerator)
        _release_com(dev_enum)
        if coinit in (0, 1):
            ole32.CoUninitialize()


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    )


class _Variant(ctypes.Structure):
    class _Value(ctypes.Union):
        _fields_ = (("bstr", ctypes.c_void_p),)

    _fields_ = (
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("value", _Value),
    )


def _guid(ole32: Any, text: str) -> _GUID:
    import ctypes

    value = _GUID()
    if ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(value)) != 0:
        raise OSError("could not parse a COM identifier")
    return value


def _method(unknown: Any, index: int, restype: Any, *argtypes: Any) -> Any:
    table = ctypes.cast(unknown, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    return function(restype, ctypes.c_void_p, *argtypes)(table[index])


def _release_com(unknown: Any) -> None:
    if not unknown:
        return
    function = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    release = function(ctypes.c_ulong, ctypes.c_void_p)(
        ctypes.cast(unknown, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[2]
    )
    release(unknown)


def _friendly_name(oleaut32: Any, bag: Any) -> str:
    import ctypes

    variant = _Variant()
    read = _method(
        bag, 3, ctypes.c_long, ctypes.c_wchar_p, ctypes.POINTER(_Variant), ctypes.c_void_p
    )
    if read(bag, "FriendlyName", ctypes.byref(variant), None) != 0 or variant.vt != 8:
        oleaut32.VariantClear(ctypes.byref(variant))
        return ""
    text = ctypes.wstring_at(variant.value.bstr) if variant.value.bstr else ""
    oleaut32.VariantClear(ctypes.byref(variant))
    return text


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
    image = _to_gray(cv2, image)
    height, width = image.shape[:2]
    return GrayFrame(int(width), int(height), _pixel_bytes(image))


def _crop_gray(cv2: Any, image: Any, width: int, height: int, roi: DetectionRoi) -> GrayFrame:
    if roi.x + roi.width > width or roi.y + roi.height > height:
        raise ValueError("zone extends outside the frame")
    view = image[roi.y : roi.y + roi.height, roi.x : roi.x + roi.width]
    view = _to_gray(cv2, view)
    crop_height, crop_width = view.shape[:2]
    return GrayFrame(int(crop_width), int(crop_height), _pixel_bytes(view))


def _to_gray(cv2: Any, image: Any) -> Any:
    """BGR and BGRA become one channel. A grayscale picture is returned unchanged.

    Virtual cameras often deliver RGB32. Treating those four channels as BGR
    raises inside OpenCV, and three such reads used to close the device.
    """
    channels = _channel_count(image)
    if channels == 1:
        return image
    if channels == 4:
        code = getattr(cv2, "COLOR_BGRA2GRAY", None)
    elif channels == 3:
        code = getattr(cv2, "COLOR_BGR2GRAY", None)
    else:
        raise CameraReadError("camera frame is missing")
    if isinstance(code, bool) or not isinstance(code, int):
        raise CameraReadError("camera frame is missing")
    return cv2.cvtColor(image, code)


def _channel_count(image: Any) -> int:
    shape = getattr(image, "shape", None)
    if isinstance(shape, tuple) and len(shape) >= 3 and isinstance(shape[2], int):
        return shape[2]
    return 1


def _note_open(
    config: CameraConfig,
    backend: str,
    *,
    result: str,
    reason: str,
    actual_width: int | None = None,
    actual_height: int | None = None,
    actual_fps: float | None = None,
    name: str = "",
) -> None:
    logger.info(
        "CAMERA_OPEN index=%s backend=%s result=%s reason=%s requested=%sx%s@%s "
        "actual=%sx%s fps=%s name=%s",
        config.device_index,
        backend,
        result,
        reason,
        config.width,
        config.height,
        config.fps,
        "" if actual_width is None else actual_width,
        "" if actual_height is None else actual_height,
        "" if actual_fps is None else actual_fps,
        name,
    )
    record(
        "CAMERA_OPEN",
        module="timing_camera",
        page="camera",
        result=result,
        index=config.device_index,
        backend=backend,
        reason=reason,
        requested_width=config.width,
        requested_height=config.height,
        requested_fps=config.fps,
        actual_width="" if actual_width is None else actual_width,
        actual_height="" if actual_height is None else actual_height,
        actual_fps="" if actual_fps is None else actual_fps,
        name=name,
    )


def _pixel_bytes(image: Any) -> bytes:
    """Packed grayscale bytes without building one Python int per pixel."""
    tobytes = getattr(image, "tobytes", None)
    if callable(tobytes):
        raw = tobytes()
        if isinstance(raw, bytes):
            return raw
    flat = image.reshape(-1)
    return bytes(int(value) & 0xFF for value in flat)
