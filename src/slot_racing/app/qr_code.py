"""QR images drawn in-process. No image is uploaded and no web service is called."""

from __future__ import annotations

from collections.abc import Iterable

import segno
from PySide6.QtGui import QImage

_QUIET_SIDE = 232
_QUIET_MODULES = 4
# Format_RGB32 is stored little-endian as B, G, R, 0xFF.
_BLACK = b"\x00\x00\x00\xff"


def qr_image(payload: str, *, side: int = _QUIET_SIDE) -> QImage:
    """A black-on-white QR code for ``payload``, including the quiet zone.

    Each module is a solid block of pixels. Nothing resamples the image: the
    scale is an integer, modules are written into the buffer, and the device
    pixel ratio stays 1. The same payload always uses the same module layout.
    """
    if not payload or side < 21:
        raise ValueError("a QR code needs a payload and room for the quiet zone")
    code = segno.make(payload, error="m", micro=False)
    matrix = tuple(code.matrix_iter(scale=1, border=_QUIET_MODULES))
    modules = len(matrix)
    scale = max(1, side // modules)
    size = modules * scale
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    image.setDevicePixelRatio(1)
    _fill_modules(image, matrix, scale)
    return image


def _fill_modules(image: QImage, matrix: Iterable[Iterable[object]], scale: int) -> None:
    bits = image.bits()
    if bits is None:
        raise RuntimeError("the QR image has no pixel buffer")
    view = memoryview(bits).cast("B")
    stride = image.bytesPerLine()
    block = _BLACK * scale
    width = scale * 4
    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            if not cell:
                continue
            origin = x * width
            for dy in range(scale):
                start = (y * scale + dy) * stride + origin
                view[start : start + width] = block
