"""One calibration attempt: record, discard, or hand the tape to the analysis.

The attempt does not change the saved camera document. A restart drops the
previous tape before any new frame is accepted.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from slot_racing.modules.timing_camera.calibration_polygon import CalibrationPolygon
from slot_racing.modules.timing_camera.calibration_recording import (
    CalibrationTape,
    RecordingError,
    RecordingOverflowError,
    RecordingStats,
    ensure_space,
)
from slot_racing.modules.timing_camera.configuration import NormalizedRoi
from slot_racing.modules.timing_camera.detection import TravelDirection
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi

_RESERVE_BYTES = 64 * 1024 * 1024

LAP_DEFAULT = 100
LAP_STEP = 10
LAP_MINIMUM = 10
LAP_MAXIMUM = 500
# Fraction of the frame kept between the recommended rectangle and the polygon edge.
SAFETY_MARGIN = 0.01


@dataclass(frozen=True, slots=True)
class CalibrationProposal:
    """Settings the user can accept. Nothing here is written until that confirmation."""

    position_id: str
    lane: int
    roi: NormalizedRoi
    check_direction: bool
    sensitivity: int
    block_size: int
    direction: TravelDirection
    polygon: CalibrationPolygon
    apply_shared: bool


class CalibrationAttempt:
    """Recording state for one zone. Analysis is a separate step on :meth:`tape`."""

    def __init__(
        self,
        polygon: CalibrationPolygon,
        *,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        if not polygon.is_valid:
            raise ValueError("polygon is not valid")
        self.polygon = polygon
        self._max_bytes = max_bytes
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._tape: CalibrationTape | None = None
        self._recording = False
        self._failed: str | None = None

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def failed(self) -> str | None:
        return self._failed

    @property
    def frame_count(self) -> int:
        if self._tape is None:
            return 0
        return self._tape.frame_count

    @property
    def identity(self) -> str:
        """Changes when a restart opens a new tape, so old files cannot be reused."""
        if self._directory is None:
            return ""
        return self._directory.name

    def stats(self) -> RecordingStats | None:
        if self._tape is None:
            return None
        return self._tape.stats()

    def start(self) -> None:
        """Begin a fresh tape. A tape that is already recording is left as it is."""
        if self._recording:
            return
        self._open()

    def restart(self) -> None:
        """Delete this attempt and start another with the same polygon."""
        self._release()
        self._failed = None
        self._open()

    def note_frame(
        self,
        frame: GrayFrame,
        timestamp_ns: int,
        sequence: int,
        *,
        process_ns: int = 0,
    ) -> None:
        """Store the polygon's bounding box. Raises when the recording cannot continue."""
        tape = self._require_recording()
        crop = _crop_box(self.polygon, frame.width, frame.height)
        try:
            tape.append(
                frame,
                timestamp_ns,
                sequence,
                crop,
                process_ns=process_ns,
            )
        except RecordingOverflowError:
            self._failed = "overflow"
            self._recording = False
            raise
        except RecordingError as error:
            self._failed = error.code
            self._recording = False
            raise

    def note_error(self, code: str) -> None:
        tape = self._tape
        if tape is None or not self._recording:
            return
        tape.note_error(code)
        if code in {"camera_lost", "not_enough_disk"}:
            self._failed = code
            self._recording = False

    def finish(self) -> CalibrationTape:
        """Stop accepting frames and return the tape. The caller analyses it."""
        tape = self._tape
        if tape is None:
            raise RecordingError("the recording has not been started")
        self._recording = False
        tape.close()
        return tape

    def cancel(self) -> None:
        """Drop the tape. Saved camera settings are not part of this object."""
        self._release()
        self._failed = None

    def _open(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="slot-calibration-")
        path = Path(directory.name)
        try:
            ensure_space(path, min(_RESERVE_BYTES, self._max_bytes))
            tape = CalibrationTape(path / "tape", max_bytes=self._max_bytes)
        except RecordingError:
            directory.cleanup()
            self._failed = "not_enough_disk"
            raise
        self._directory = directory
        self._tape = tape
        self._recording = True
        self._failed = None

    def _require_recording(self) -> CalibrationTape:
        if not self._recording or self._tape is None:
            raise RecordingError("the recording is not running")
        return self._tape

    def _release(self) -> None:
        tape = self._tape
        self._tape = None
        self._recording = False
        if tape is not None:
            tape.release()
        directory = self._directory
        self._directory = None
        if directory is not None:
            directory.cleanup()


def crop_box(polygon: CalibrationPolygon, width: int, height: int) -> tuple[int, int, int, int]:
    """Pixel bounding box of a normalized polygon, clamped to the frame."""
    return _crop_box(polygon, width, height)


def _crop_box(polygon: CalibrationPolygon, width: int, height: int) -> tuple[int, int, int, int]:
    if width < 1 or height < 1:
        raise ValueError("frame size must be positive")
    xs = [point[0] for point in polygon.points]
    ys = [point[1] for point in polygon.points]
    left = max(0, int(min(xs) * width))
    top = max(0, int(min(ys) * height))
    right = min(width, int(max(xs) * width + 0.999))
    bottom = min(height, int(max(ys) * height + 0.999))
    box_width = max(1, right - left)
    box_height = max(1, bottom - top)
    if left + box_width > width:
        box_width = width - left
    if top + box_height > height:
        box_height = height - top
    DetectionRoi(left, top, box_width, box_height)
    return left, top, box_width, box_height
