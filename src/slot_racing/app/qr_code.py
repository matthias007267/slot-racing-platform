"""QR images drawn in-process. No image is uploaded and no web service is called."""

from __future__ import annotations

import segno
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

_QUIET_SIDE = 232


def qr_image(payload: str, *, side: int = _QUIET_SIDE) -> QImage:
    """A black-on-white QR code for ``payload``, including the quiet zone.

    The same payload always uses the same module layout. Callers should skip
    this when the payload has not changed.
    """
    if not payload or side < 21:
        raise ValueError("a QR code needs a payload and room for the quiet zone")
    code = segno.make(payload, error="m", micro=False)
    # ``matrix`` omits the quiet zone. Phone cameras need the four-module border.
    matrix = tuple(code.matrix_iter(scale=1, border=4))
    modules = len(matrix)
    scale = max(1, side // modules)
    size = modules * scale
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0))
    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            if cell:
                painter.drawRect(x * scale, y * scale, scale, scale)
    painter.end()
    return image
