"""Grayscale frames for the camera detector.

A frame is a dense row-major buffer of intensities from 0 to 255. Tests build
synthetic frames directly; a later capture step can fill the same buffer from a
camera without changing the detector.
"""

from __future__ import annotations

from dataclasses import dataclass

from slot_racing.modules.timing_camera._checks import require_range
from slot_racing.modules.timing_camera.geometry import DetectionRoi


@dataclass(frozen=True, slots=True)
class GrayFrame:
    """One immutable grayscale image."""

    width: int
    height: int
    pixels: tuple[int, ...] | bytes

    def __post_init__(self) -> None:
        width = require_range("width", self.width, 1)
        height = require_range("height", self.height, 1)
        if isinstance(self.pixels, bytes):
            if len(self.pixels) != width * height:
                raise ValueError("pixel count must equal width * height")
            return
        if not isinstance(self.pixels, tuple):
            raise TypeError("pixels must be a tuple or bytes")
        if len(self.pixels) != width * height:
            raise ValueError("pixel count must equal width * height")
        for pixel in self.pixels:
            require_range("pixel", pixel, 0, 255)

    def to_bytes(self) -> bytes:
        """Packed grayscale bytes. Camera frames already store this form."""
        if isinstance(self.pixels, bytes):
            return self.pixels
        return bytes(self.pixels)

    @classmethod
    def blank(cls, width: int, height: int, value: int = 0) -> GrayFrame:
        """A frame filled with one intensity."""
        require_range("value", value, 0, 255)
        return cls(width, height, (value,) * (width * height))

    def paint(self, roi: DetectionRoi, value: int) -> GrayFrame:
        """Return a copy with ``roi`` filled by ``value``."""
        require_range("value", value, 0, 255)
        if not isinstance(roi, DetectionRoi):
            raise TypeError("roi must be a DetectionRoi")
        if roi.x + roi.width > self.width or roi.y + roi.height > self.height:
            raise ValueError("roi extends outside the frame")
        pixels = list(self.pixels)
        for y in range(roi.y, roi.y + roi.height):
            row = y * self.width
            for x in range(roi.x, roi.x + roi.width):
                pixels[row + x] = value
        return GrayFrame(self.width, self.height, tuple(pixels))
