"""Persistent camera setup: device hints and detection zones.

Zones are stored as fractions of the frame (0 to 1) so a later change of the
requested resolution still describes the same part of the picture. Detection
itself keeps using pixel rectangles. The conversion happens once, when a race
session is created, from the resolution stored next to the zones. The capture
thread never reads this document.

The document is the camera's own configuration. It has no ``track_id`` and is
not stored on a timing sensor.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from slot_racing.modules.timing_camera._checks import require_position_id, require_range
from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.detection import DetectionZone, DetectorSettings
from slot_racing.modules.timing_camera.geometry import DetectionRoi

SCHEMA_VERSION = 1


class NormalizedRoi(BaseModel):
    """Axis-aligned rectangle in fractions of the camera frame.

    ``x`` and ``y`` are the top-left corner. ``width`` and ``height`` extend
    right and down. All four values lie in the unit square, and the rectangle
    stays inside it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @field_validator("x", "y", "width", "height", mode="before")
    @classmethod
    def _reject_bool(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("roi values must be numbers")
        return value

    @model_validator(mode="after")
    def _inside_frame(self) -> NormalizedRoi:
        if not _within_unit(self.x, self.width) or not _within_unit(self.y, self.height):
            raise ValueError("roi must lie inside the frame")
        return self


class StoredDetectionZone(BaseModel):
    """One stored zone: which position and lane a region of the picture reports."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position_id: str
    lane: int
    roi: NormalizedRoi

    @field_validator("position_id", mode="before")
    @classmethod
    def _position(cls, value: object) -> str:
        try:
            return require_position_id(value)
        except (TypeError, ValueError) as error:
            raise ValueError(str(error)) from error

    @field_validator("lane", mode="before")
    @classmethod
    def _lane(cls, value: object) -> int:
        try:
            return require_range("lane", value, 1)
        except (TypeError, ValueError) as error:
            raise ValueError(str(error)) from error


class StoredDetection(BaseModel):
    """The zones the camera currently watches. Empty until someone configures them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    zones: tuple[StoredDetectionZone, ...] = ()

    @field_validator("zones", mode="before")
    @classmethod
    def _zones(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _unique(self) -> StoredDetection:
        seen: set[tuple[str, int]] = set()
        for zone in self.zones:
            key = (zone.position_id, zone.lane)
            if key in seen:
                raise ValueError(
                    f"duplicate detection zone for position {zone.position_id!r} lane {zone.lane}"
                )
            seen.add(key)
        return self


class StoredCamera(BaseModel):
    """Requested device. The driver may still deliver a different picture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30

    @field_validator("device_index", mode="before")
    @classmethod
    def _device_index(cls, value: object) -> int:
        return _strict_int("device_index", value, 0)

    @field_validator("width", "height", "fps", mode="before")
    @classmethod
    def _positive(cls, value: object, info: ValidationInfo) -> int:
        return _strict_int(info.field_name or "value", value, 1)


class CameraConfiguration(BaseModel):
    """The whole saved camera setup. ``version`` is the document format."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = SCHEMA_VERSION
    camera: StoredCamera = Field(default_factory=StoredCamera)
    detection: StoredDetection = Field(default_factory=StoredDetection)

    @field_validator("version", mode="before")
    @classmethod
    def _version(cls, value: object) -> int:
        version = _strict_int("version", value, 1)
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported camera configuration version {version}")
        return version


def to_camera_config(configuration: CameraConfiguration) -> CameraConfig:
    """The device hint a capture source can open. Zones are not part of it."""
    camera = configuration.camera
    return CameraConfig(
        device_index=camera.device_index,
        width=camera.width,
        height=camera.height,
        fps=camera.fps,
    )


def to_detector_settings(configuration: CameraConfiguration) -> DetectorSettings | None:
    """Pixel zones for the saved resolution, or ``None`` when nothing is configured.

    An empty zone list is a valid saved document. It is not a detector setup:
    the caller decides that a race cannot start without zones.
    """
    zones = configuration.detection.zones
    if not zones:
        return None
    width = configuration.camera.width
    height = configuration.camera.height
    return DetectorSettings(
        tuple(
            DetectionZone(
                zone.position_id,
                zone.lane,
                roi_to_pixels(zone.roi, width, height),
            )
            for zone in zones
        )
    )


def pixels_to_roi(
    x: int, y: int, width: int, height: int, frame_width: int, frame_height: int
) -> NormalizedRoi:
    """The normalized form of a pixel rectangle inside a frame.

    This is the inverse of :func:`roi_to_pixels` for rectangles that divide the
    frame evenly, which is what the setup page stores after a drag.
    """
    if frame_width < 1 or frame_height < 1:
        raise ValueError("frame size must be positive")
    if width < 1 or height < 1:
        raise ValueError("zone must cover a pixel")
    if x < 0 or y < 0 or x + width > frame_width or y + height > frame_height:
        raise ValueError("zone must lie inside the frame")
    return NormalizedRoi(
        x=x / frame_width,
        y=y / frame_height,
        width=width / frame_width,
        height=height / frame_height,
    )


def roi_to_pixels(roi: NormalizedRoi, width: int, height: int) -> DetectionRoi:
    """Map one normalized rectangle onto ``width`` by ``height`` pixels.

    The mapping is deterministic. The right and bottom edges are clamped to the
    frame so a zone that ends on 1 does not stick out by a rounding error.
    Values that land within a millionth of a pixel of an integer are that
    integer: ``0.1 + 0.2`` is not a clean binary fraction, and without this a
    saved ``0.2`` of a 640-wide frame would come back one pixel too wide.
    """
    if width < 1 or height < 1:
        raise ValueError("frame size must be positive")
    x = math.floor(_quantize(roi.x * width))
    y = math.floor(_quantize(roi.y * height))
    right = min(width, math.ceil(_quantize((roi.x + roi.width) * width)))
    bottom = min(height, math.ceil(_quantize((roi.y + roi.height) * height)))
    pixel_width = right - x
    pixel_height = bottom - y
    if pixel_width < 1 or pixel_height < 1:
        raise ValueError("detection zone does not cover a pixel of this frame")
    return DetectionRoi(x, y, pixel_width, pixel_height)


def _strict_int(name: str, value: object, low: int) -> int:
    try:
        return require_range(name, value, low)
    except (TypeError, ValueError) as error:
        raise ValueError(str(error)) from error


def _quantize(value: float) -> float:
    nearest = round(value)
    if math.isclose(value, nearest, abs_tol=1e-6):
        return float(nearest)
    return value


def _within_unit(start: float, size: float) -> bool:
    end = start + size
    return end <= 1 or math.isclose(end, 1, abs_tol=1e-9)
