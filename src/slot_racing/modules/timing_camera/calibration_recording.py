"""Disk-backed recording of one calibration drive.

Only the bounding box of the search polygon is stored, and each crop is
compressed before it is written. The process keeps an index of timestamps and
byte offsets, not a list of pictures. A full recording raises
:class:`RecordingOverflowError` instead of dropping a frame quietly. Closing the
tape deletes the temporary files.
"""

from __future__ import annotations

import json
import os
import shutil
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi

_DEFAULT_LIMIT = 512 * 1024 * 1024


class RecordingOverflowError(Exception):
    """The byte budget is full. The frame that did not fit was not stored."""


class RecordingError(Exception):
    """The recording could not be written or read."""

    def __init__(self, message: str, *, code: str = "recording") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RecordedFrame:
    """One stored crop and the capture clock that belongs to it."""

    sequence: int
    timestamp_ns: int
    width: int
    height: int
    crop_x: int
    crop_y: int
    crop_width: int
    crop_height: int
    pixels: bytes
    gap_before: int


@dataclass(frozen=True, slots=True)
class RecordingStats:
    """What the drive actually captured. Figures come from the index, not a guess."""

    frames: int
    dropped: int
    width: int
    height: int
    first_timestamp_ns: int | None
    last_timestamp_ns: int | None
    bytes_used: int
    errors: tuple[str, ...]
    mean_process_ns: int

    @property
    def fps(self) -> float | None:
        if (
            self.frames < 2
            or self.first_timestamp_ns is None
            or self.last_timestamp_ns is None
            or self.last_timestamp_ns <= self.first_timestamp_ns
        ):
            return None
        elapsed = self.last_timestamp_ns - self.first_timestamp_ns
        return (self.frames - 1) * 1_000_000_000 / elapsed


class CalibrationTape:
    """One attempt. A restart builds a new tape; this object is not reused."""

    def __init__(self, directory: Path, *, max_bytes: int = _DEFAULT_LIMIT) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self._directory = directory
        self._max_bytes = max_bytes
        self._bytes = 0
        self._dropped = 0
        self._errors: list[str] = []
        self._process_ns = 0
        self._process_samples = 0
        self._last_sequence: int | None = None
        self._width = 0
        self._height = 0
        self._first_ns: int | None = None
        self._last_ns: int | None = None
        self._overflow = False
        self._closed = False
        self._index: list[dict[str, int]] = []
        directory.mkdir(parents=True, exist_ok=True)
        self._handle = (directory / "frames.bin").open("wb")

    @property
    def overflow(self) -> bool:
        return self._overflow

    @property
    def frame_count(self) -> int:
        return len(self._index)

    def stats(self) -> RecordingStats:
        mean = 0 if self._process_samples == 0 else self._process_ns // self._process_samples
        return RecordingStats(
            frames=len(self._index),
            dropped=self._dropped,
            width=self._width,
            height=self._height,
            first_timestamp_ns=self._first_ns,
            last_timestamp_ns=self._last_ns,
            bytes_used=self._bytes,
            errors=tuple(self._errors),
            mean_process_ns=mean,
        )

    def append(
        self,
        frame: GrayFrame,
        timestamp_ns: int,
        sequence: int,
        crop: tuple[int, int, int, int],
        *,
        process_ns: int = 0,
    ) -> None:
        """Store one crop. A sequence gap is counted as dropped frames."""
        if self._closed or self._overflow:
            raise RecordingError("the recording is closed")
        if not isinstance(frame, GrayFrame):
            raise TypeError("frame must be a GrayFrame")
        if timestamp_ns < 0 or sequence < 0 or process_ns < 0:
            raise ValueError("timestamps and durations must not be negative")
        x_value, y_value, width, height = crop
        if (
            width < 1
            or height < 1
            or x_value < 0
            or y_value < 0
            or x_value + width > frame.width
            or y_value + height > frame.height
        ):
            raise ValueError("crop must lie inside the frame")
        if self._width and (frame.width, frame.height) != (self._width, self._height):
            self._errors.append("resolution_changed")
            raise RecordingError(
                "the camera changed resolution during the recording",
                code="resolution_changed",
            )
        gap = 0
        if sequence and self._last_sequence is not None and sequence > self._last_sequence + 1:
            gap = sequence - self._last_sequence - 1
            self._dropped += gap
        packed = zlib.compress(
            frame.crop(DetectionRoi(x_value, y_value, width, height)).to_bytes(),
            level=1,
        )
        if self._bytes + len(packed) > self._max_bytes:
            self._overflow = True
            raise RecordingOverflowError("the recording reached its storage limit")
        offset = self._handle.tell()
        self._handle.write(len(packed).to_bytes(4, "little"))
        self._handle.write(packed)
        self._handle.flush()
        self._bytes += 4 + len(packed)
        self._index.append(
            {
                "sequence": sequence,
                "timestamp_ns": timestamp_ns,
                "width": frame.width,
                "height": frame.height,
                "x": x_value,
                "y": y_value,
                "crop_width": width,
                "crop_height": height,
                "offset": offset,
                "nbytes": len(packed),
                "gap": gap,
            }
        )
        self._width = frame.width
        self._height = frame.height
        if self._first_ns is None:
            self._first_ns = timestamp_ns
        self._last_ns = timestamp_ns
        if sequence:
            self._last_sequence = sequence
        if process_ns:
            self._process_ns += process_ns
            self._process_samples += 1

    def note_error(self, code: str) -> None:
        """Remember a camera or thread failure without inventing a frame."""
        if self._closed:
            raise RecordingError("the recording is closed")
        if not code:
            raise ValueError("error code is required")
        self._errors.append(code)

    def frames(self) -> Iterator[RecordedFrame]:
        """Replay in capture order. Each crop is decompressed on its own."""
        if not self._closed:
            self._handle.flush()
        path = self._directory / "frames.bin"
        with path.open("rb") as handle:
            for row in self._index:
                handle.seek(row["offset"])
                size = int.from_bytes(handle.read(4), "little")
                if size != row["nbytes"]:
                    raise RecordingError("the recording index does not match the file")
                pixels = zlib.decompress(handle.read(size))
                expected = row["crop_width"] * row["crop_height"]
                if len(pixels) != expected:
                    raise RecordingError("a stored crop has the wrong size")
                yield RecordedFrame(
                    sequence=row["sequence"],
                    timestamp_ns=row["timestamp_ns"],
                    width=row["width"],
                    height=row["height"],
                    crop_x=row["x"],
                    crop_y=row["y"],
                    crop_width=row["crop_width"],
                    crop_height=row["crop_height"],
                    pixels=pixels,
                    gap_before=row["gap"],
                )

    def close(self) -> None:
        """Flush and stop accepting frames. The files stay until :meth:`release`."""
        if self._closed:
            return
        self._closed = True
        self._handle.close()
        (self._directory / "index.json").write_text(
            json.dumps(self._index),
            encoding="utf-8",
        )

    def release(self) -> None:
        """Close the tape and delete its files."""
        self.close()
        shutil.rmtree(self._directory, ignore_errors=True)
        if self._directory.exists():
            raise RecordingError("temporary calibration files could not be removed")


def directory_free_bytes(path: Path) -> int | None:
    """Free space on the volume that holds ``path``, or ``None`` when it cannot be read."""
    try:
        return shutil.disk_usage(path if path.exists() else path.parent).free
    except OSError:
        return None


def ensure_space(path: Path, required: int) -> None:
    """Raise :class:`RecordingError` when the volume cannot hold ``required`` bytes."""
    free = directory_free_bytes(path)
    if free is not None and free < required:
        raise RecordingError("not_enough_disk", code="not_enough_disk")
    # Touch the directory so a missing parent fails here, not during the first frame.
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".space"
    try:
        probe.write_bytes(b"ok")
        os.remove(probe)
    except OSError as error:
        raise RecordingError("not_enough_disk", code="not_enough_disk") from error
