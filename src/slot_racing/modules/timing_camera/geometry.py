"""Image geometry for a detection zone.

The shapes are plain numbers. Image processing may later use OpenCV, but a zone
description must stay usable without it.
"""

from __future__ import annotations

from dataclasses import dataclass

from slot_racing.modules.timing_camera._checks import require_range


@dataclass(frozen=True, slots=True)
class DetectionRoi:
    """Axis-aligned rectangle in image coordinates.

    The origin is the top-left corner, ``x`` grows to the right and ``y`` grows
    downward. A detection line is a rectangle that is only a few pixels thick.
    """

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        require_range("x", self.x, 0)
        require_range("y", self.y, 0)
        require_range("width", self.width, 1)
        require_range("height", self.height, 1)

    @property
    def area(self) -> int:
        return self.width * self.height
