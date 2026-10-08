"""Diagnostic session over the same lane detector a race uses.

The session does not open a camera and does not score a crossing of its own.
It feeds frames into :func:`create_lane_detector` and records the block matrices
that detector already compared. A worker thread does that work. ``submit`` only
keeps the newest unread frame, the same latest-frame rule as capture.
"""

from __future__ import annotations

import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

import numpy as np

from slot_racing.modules.timing_camera.configuration import scale_detector_settings
from slot_racing.modules.timing_camera.detection import (
    DetectorSettings,
    FrameInspection,
    LaneCrossing,
    LaneCrossingDetector,
    ZoneInspection,
    create_lane_detector,
    evaluate_detection_zone,
    minimum_component_area_px,
    resolution_preset_name,
    sensitivity_profile,
)
from slot_racing.modules.timing_camera.diagnostic_store import (
    DiagnosticJournal,
    DiagnosticReport,
    LaneReference,
    ZoneReport,
    direction_reason_code,
    export_document,
    percentile_nearest,
)
from slot_racing.modules.timing_camera.frame_source import TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame

MAX_LOG_LINES = 12_000
DEFAULT_MAX_FRAME_RECORDS = 50_000
_ANALYSIS_SAMPLE_CAP = 50_000
_SAMPLE_HISTORY = 32
_MAX_MAP_CELLS = 512
_MAX_MAP_ROWS = 40
_MAX_MAP_COLS = 64
_IDLE_REASONS = frozenset(
    {
        "clear",
        "calibrated",
        "released",
        "released_to_background",
        "background_adapting",
        "synchronized_clear",
    }
)
Phase = Literal["initializing", "ready"]


class DiagnosticView(StrEnum):
    """Which real matrix the preview shows. The pixels are not reinterpreted."""

    ANALYSIS = "analysis"
    REFERENCE = "reference"
    DIFFERENCE = "difference"
    THRESHOLD = "threshold"
    ZONES = "zones"


@dataclass(frozen=True, slots=True)
class ZoneFacts:
    """One configured zone, in detector order, as pixel geometry."""

    number: int
    position_id: str
    lane: int
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class DiagnosticConfig:
    """Facts known before the first frame. Measured camera values stay absent."""

    app_version: str
    started_at: str
    device_index: int
    requested_width: int
    requested_height: int
    requested_fps: int
    block_size: int
    sensitivity: int
    direction: str
    difference_threshold: float
    min_blocks: int
    min_shift: float
    window_ns: int
    zones: tuple[ZoneFacts, ...]


@dataclass(frozen=True, slots=True)
class CaptureCounters:
    """Counters sampled with one analyzed frame.

    ``dropped`` and ``source_overwrites`` count unread frames replaced in the
    consumer's latest-frame slot. They are not failed camera reads.
    ``read_failures`` counts failed reads. ``sequence`` is the capture sequence
    of this frame.
    """

    captured: int | None = None
    dropped: int | None = None
    read_attempts: int | None = None
    read_failures: int | None = None
    source_overwrites: int | None = None
    sequence: int | None = None
    capture_dt_ns: int | None = None
    preview_frames: int | None = None
    preview_replaced: int | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticPerformance:
    """How many frames arrived and how long analysis took. Missing clocks stay unset."""

    frames_submitted: int
    frames_analyzed: int
    frames_skipped: int
    frames_captured: int | None
    capture_dropped: int | None
    camera_fps: float | None
    analysis_fps: float | None
    average_analysis_ns: int | None
    maximum_analysis_ns: int
    last_analysis_ns: int
    last_dt_ns: int | None
    read_attempts: int | None = None
    read_failures: int | None = None
    source_overwrites: int | None = None
    capture_sequence: int | None = None
    last_capture_dt_ns: int | None = None
    preview_frames: int | None = None
    preview_replaced: int | None = None


@dataclass(frozen=True, slots=True)
class ActivitySummary:
    """Peaks for one burst of zone activity, closed when the zone goes idle."""

    position_id: str
    lane: int
    zone_number: int
    started_ns: int
    ended_ns: int
    duration_ns: int
    peak_changed_blocks: int
    peak_changed_ratio: float
    peak_difference: float
    direction_confirmed: bool
    detection_event: bool
    reason: str
    activity_frames: int
    direction_samples: int
    direction_reason: str = ""


@dataclass(frozen=True, slots=True)
class DiagnosticSnapshot:
    """One immutable view of the session. The UI reads this and does not detect."""

    phase: Phase
    reference_ready: bool
    frame_index: int
    timestamp_ns: int | None
    elapsed_ns: int | None
    width: int | None
    height: int | None
    inspection: FrameInspection | None
    crossings: tuple[LaneCrossing, ...]
    performance: DiagnosticPerformance
    activities: tuple[ActivitySummary, ...]


@dataclass(frozen=True, slots=True)
class ZoneBox:
    """Where one zone sits inside a rendered preview. Overlay only."""

    number: int
    position_id: str
    lane: int
    x: int
    y: int
    width: int
    height: int
    accepted: bool
    direction: str
    reason: str


@dataclass(frozen=True, slots=True)
class DiagnosticPicture:
    """Nearest-neighbor enlargement of the selected matrix. Values are not rescaled."""

    width: int
    height: int
    pixels: bytes
    zones: tuple[ZoneBox, ...]
    view: DiagnosticView


@dataclass
class _Burst:
    started_ns: int
    peak_changed_blocks: int
    peak_changed_ratio: float
    peak_difference: float
    direction_confirmed: bool
    detection_event: bool
    reason: str
    last_reason: str
    frames: int
    direction_samples: int
    samples_created: int = 0
    samples_discarded: int = 0
    below_size_frames: int = 0
    too_short_frames: int = 0
    insufficient_frames: int = 0
    wrong_direction_frames: int = 0
    component_sum: int = 0
    component_peak: int = 0
    component_frames: int = 0
    state_changes: int = 0
    last_state: str = ""
    first_frame: int = 0
    last_frame: int = 0
    capture_seq_start: int | None = None
    capture_seq_end: int | None = None
    centroids_omitted: int = 0
    centroids: list[tuple[int, float, float]] | None = None
    shifts: list[float] | None = None


def diagnostic_config(
    *,
    app_version: str,
    started_at: str,
    device_index: int,
    requested_width: int,
    requested_height: int,
    requested_fps: int,
    settings: DetectorSettings,
) -> DiagnosticConfig:
    """Header fields taken from the detector settings a race would build."""
    profile = sensitivity_profile(settings.sensitivity)
    zones = tuple(
        ZoneFacts(
            number=index + 1,
            position_id=zone.position_id,
            lane=zone.lane,
            x=zone.roi.x,
            y=zone.roi.y,
            width=zone.roi.width,
            height=zone.roi.height,
        )
        for index, zone in enumerate(settings.zones)
    )
    return DiagnosticConfig(
        app_version=app_version,
        started_at=started_at,
        device_index=device_index,
        requested_width=requested_width,
        requested_height=requested_height,
        requested_fps=requested_fps,
        block_size=settings.block_size,
        sensitivity=settings.sensitivity,
        direction=settings.direction.value,
        difference_threshold=profile.difference,
        min_blocks=profile.min_blocks,
        min_shift=profile.min_shift,
        window_ns=profile.window_ns,
        zones=zones,
    )


def format_header(config: DiagnosticConfig) -> str:
    """The session header. Actual camera measurements are not filled in here."""
    lines = [
        "=== Slot-Racing Camera Detection Diagnostic ===",
        "",
        f"App version: {config.app_version}",
        f"Timestamp: {config.started_at}",
        "",
        "Camera:",
        f"Device index: {config.device_index}",
        f"Requested resolution: {config.requested_width} x {config.requested_height}",
        "Actual resolution: waiting",
        f"Requested FPS: {config.requested_fps}",
        "Actual FPS: waiting",
        "",
        "Detection:",
        "Algorithm: directional block difference against an adaptive background reference",
        f"Requested block size: {config.block_size}",
        "Processing: per-zone crop, then mean of each full block; partial tiles dropped",
        f"Zone count: {len(config.zones)}",
        f"Sensitivity: {config.sensitivity}",
        f"Detection resolution: {resolution_preset_name(config.block_size)}",
        f"Block size: {config.block_size} x {config.block_size}",
        f"Difference threshold: {config.difference_threshold:.2f}",
        f"Minimum connected blocks: {config.min_blocks}",
        (f"Normalized minimum component area: {minimum_component_area_px(config.min_blocks)} px"),
        f"Minimum shift: {config.min_shift:.2f}",
        f"Minimum shift px: {config.min_shift * 20:.2f}",
        f"Direction window ms: {config.window_ns / 1_000_000:.1f}",
        f"Travel direction: {config.direction}",
        "",
    ]
    if not config.zones:
        lines.append("Zones: none")
    for zone in config.zones:
        lines.extend(
            [
                f"Zone {zone.number}:",
                f"Position: {zone.position_id}",
                f"Lane: {zone.lane}",
                f"Coordinates: x={zone.x} y={zone.y} width={zone.width} height={zone.height}",
                f"Direction: {config.direction}",
                "",
            ]
        )
    return "\n".join(lines).rstrip("\n")


def format_live(snapshot: DiagnosticSnapshot) -> str:
    """The same figures the log stores, as the text beside the preview."""
    performance = snapshot.performance
    lines = [
        f"Detector state: {snapshot.phase.upper()}",
        f"Reference frame: {'available' if snapshot.reference_ready else 'waiting'}",
        f"Frame: {snapshot.frame_index}",
        f"Timestamp ns: {_or_waiting(snapshot.timestamp_ns)}",
        f"Frame dt ms: {_ms(snapshot.performance.last_dt_ns)}",
        f"Analysis ms: {_ms(performance.last_analysis_ns)}",
        f"Resolution: {_resolution(snapshot)}",
        f"Camera FPS: {_fps(performance.camera_fps)}",
        f"Analysis FPS: {_fps(performance.analysis_fps)}",
        f"Capture frames: {_or_waiting(performance.frames_captured)}",
        f"Capture read failures: {_or_waiting(performance.read_failures)}",
        f"Source overwrites: {_or_waiting(performance.source_overwrites)}",
        f"Preview frames: {_or_waiting(performance.preview_frames)}",
        f"Preview replaced: {_or_waiting(performance.preview_replaced)}",
        f"Capture sequence: {_or_waiting(performance.capture_sequence)}",
        f"Capture dt ms: {_ms(performance.last_capture_dt_ns)}",
        f"Frames received: {performance.frames_submitted}",
        f"Frames analyzed: {performance.frames_analyzed}",
        f"Frames skipped: {performance.frames_skipped}",
        f"Average analysis ms: {_ms(performance.average_analysis_ns)}",
        f"Maximum analysis ms: {_ms(performance.maximum_analysis_ns)}",
    ]
    inspection = snapshot.inspection
    if inspection is None:
        lines.append("Zones: waiting")
        return "\n".join(lines)
    for index, zone in enumerate(inspection.zones, start=1):
        shift = "n/a" if zone.shift is None else f"{zone.shift:.2f}"
        if zone.centroid is None:
            centroid = "n/a"
        else:
            centroid = f"{zone.centroid[0]:.2f},{zone.centroid[1]:.2f}"
        rows = len(zone.analysis)
        cols = len(zone.analysis[0]) if rows else 0
        lines.extend(
            [
                "",
                f"Zone {index} lane {zone.lane} {zone.position_id}",
                f"Grid: {cols} x {rows} block {zone.block_size}",
                f"Mean diff: {zone.mean_difference:.2f}",
                f"Max diff: {zone.max_difference:.2f}",
                f"Active blocks: {zone.changed_blocks}",
                f"Active ratio: {zone.active_ratio:.3f}",
                f"Threshold: {zone.difference_threshold:.2f}",
                f"Required blocks: {zone.required_blocks}",
                f"Analyseraster: {cols} x {rows}",
                f"Zone quality: {_zone_quality_name(zone)}",
                f"Component blocks: {zone.component_blocks}",
                f"Centroid: {centroid}",
                f"Shift: {shift}",
                f"Minimum shift: {zone.min_shift:.2f}",
                f"Samples: {zone.sample_count}",
                f"Samples expired: {zone.samples_expired}",
                f"Window ms: {zone.window_ns / 1_000_000:.1f}",
                f"State: {zone.state.value}",
                f"Direction: {zone.direction.value}",
                f"Event: {'yes' if zone.accepted else 'no'}",
                f"Reason: {zone.reason}",
                f"Reference frozen: {'yes' if zone.reference_frozen else 'no'}",
                f"Reference updates: {zone.reference_updates}",
                f"Background stable: {'yes' if zone.background_stable else 'no'}",
                f"Release candidate: {'yes' if zone.release_candidate else 'no'}",
            ]
        )
    return "\n".join(lines)


def render_zone(zone: ZoneInspection, view: DiagnosticView, *, scale: int = 1) -> DiagnosticPicture:
    """One zone's analysis grid, nearest-neighbor enlarged. No caption, no crop."""
    if not isinstance(view, DiagnosticView):
        raise TypeError("view must be a DiagnosticView")
    if isinstance(scale, bool) or not isinstance(scale, int) or scale < 1:
        raise ValueError("scale must be at least 1")
    grid = _view_grid(zone, view)
    enlarged = np.repeat(np.repeat(grid, scale, axis=0), scale, axis=1)
    height = int(enlarged.shape[0])
    width = int(enlarged.shape[1])
    if height < 1 or width < 1:
        return DiagnosticPicture(1, 1, b"\x00", (), view)
    box = ZoneBox(
        number=1,
        position_id=zone.position_id,
        lane=zone.lane,
        x=0,
        y=0,
        width=width,
        height=height,
        accepted=zone.accepted,
        direction=zone.direction.value,
        reason=zone.reason,
    )
    return DiagnosticPicture(width, height, enlarged.tobytes(), (box,), view)


def render_picture(
    inspection: FrameInspection, view: DiagnosticView, *, scale: int = 0
) -> DiagnosticPicture:
    """Enlarge the selected block matrix. Each block stays one flat color."""
    if not isinstance(view, DiagnosticView):
        raise TypeError("view must be a DiagnosticView")
    grids = [_view_grid(zone, view) for zone in inspection.zones]
    if not grids:
        return DiagnosticPicture(1, 1, b"\x00", (), view)
    chosen = scale if scale > 0 else _display_scale(grids)
    gap = chosen
    width = max(int(grid.shape[1]) for grid in grids) * chosen
    chunks: list[np.ndarray] = []
    boxes: list[ZoneBox] = []
    y = 0
    for index, (zone, grid) in enumerate(zip(inspection.zones, grids, strict=True)):
        enlarged = np.repeat(np.repeat(grid, chosen, axis=0), chosen, axis=1)
        if int(enlarged.shape[1]) < width:
            enlarged = np.pad(enlarged, ((0, 0), (0, width - int(enlarged.shape[1]))))
        chunks.append(enlarged)
        boxes.append(
            ZoneBox(
                number=index + 1,
                position_id=zone.position_id,
                lane=zone.lane,
                x=0,
                y=y,
                width=int(grid.shape[1]) * chosen,
                height=int(grid.shape[0]) * chosen,
                accepted=zone.accepted,
                direction=zone.direction.value,
                reason=zone.reason,
            )
        )
        y += int(enlarged.shape[0])
        if index + 1 < len(grids):
            chunks.append(np.zeros((gap, width), dtype=np.uint8))
            y += gap
    stacked = np.vstack(chunks) if len(chunks) > 1 else chunks[0]
    height = int(stacked.shape[0])
    return DiagnosticPicture(width, height, stacked.tobytes(), tuple(boxes), view)


class DiagnosticSession:
    """Runs :func:`create_lane_detector` off the caller thread and keeps a log."""

    def __init__(
        self,
        settings: DetectorSettings,
        config: DiagnosticConfig,
        *,
        max_lines: int = MAX_LOG_LINES,
        journal_path: Path | None = None,
        max_frame_records: int = DEFAULT_MAX_FRAME_RECORDS,
    ) -> None:
        if not isinstance(settings, DetectorSettings):
            raise TypeError("settings must be DetectorSettings")
        if not isinstance(config, DiagnosticConfig):
            raise TypeError("config must be a DiagnosticConfig")
        if isinstance(max_lines, bool) or not isinstance(max_lines, int) or max_lines < 1:
            raise ValueError("max_lines must be at least 1")
        if journal_path is not None and not isinstance(journal_path, Path):
            raise TypeError("journal_path must be a Path")
        if isinstance(max_frame_records, bool) or not isinstance(max_frame_records, int):
            raise TypeError("max_frame_records must be an int")
        if max_frame_records < 0:
            raise ValueError("max_frame_records must not be negative")
        self._base_settings = settings
        self._settings = settings
        self._config = config
        self._expected = (config.requested_width, config.requested_height)
        self._max_lines = max_lines
        self._journal_path = journal_path
        self._max_frame_records = max_frame_records
        self._cond = threading.Condition()
        self._pending: TimedFrame | None = None
        self._pending_capture: CaptureCounters | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._in_flight = False
        self._matched = False
        self._footer_written = False
        self._truncated = False
        self._lines: list[str] = []
        self._detector: LaneCrossingDetector | None = None
        self._submitted = 0
        self._analyzed = 0
        self._skipped = 0
        self._analysis_ns_total = 0
        self._analysis_ns_max = 0
        self._last_analysis_ns = 0
        self._previous_ns: int | None = None
        self._last_dt_ns: int | None = None
        self._first_ns: int | None = None
        self._capture_origin: tuple[int, int] | None = None
        self._last_capture: CaptureCounters | None = None
        self._latest: DiagnosticSnapshot | None = None
        self._bursts: dict[tuple[str, int], _Burst] = {}
        self._activities: list[ActivitySummary] = []
        self._all_crossings: list[LaneCrossing] = []
        self._zone_numbers = {(zone.position_id, zone.lane): zone.number for zone in config.zones}
        self._actual: tuple[int, int] | None = None
        self._actual_logged = False
        self._journal: DiagnosticJournal | None = None
        self._journal_dir: Path | None = None
        self._journal_closed = False
        self._report: DiagnosticReport | None = None
        self._references: dict[int, int] = {}
        self._analysis_samples: list[int] = []
        self._analysis_ns_min: int | None = None
        self._dt_min: int | None = None
        self._dt_max: int | None = None
        self._dt_sum = 0
        self._dt_count = 0
        self._ended_at = ""

    @property
    def running(self) -> bool:
        return self._running

    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Reset the log and the detector, then accept frames."""
        if self._running or self.alive():
            self.stop()
        with self._cond:
            self._reset_locked()
            self._settings = self._base_settings
            self._running = True
            self._stop.clear()
            self._open_journal_locked()
            for line in format_header(self._config).split("\n"):
                self._append_locked(line)
            self._offer_session_locked()
            self._thread = threading.Thread(
                target=self._run, name="slot-racing-detection-diagnostic", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop collecting. The log and the last snapshot stay readable."""
        with self._cond:
            self._running = False
            self._stop.set()
            thread = self._thread
            self._thread = None
            self._cond.notify_all()
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2)
            if thread.is_alive():
                raise RuntimeError("camera diagnostic thread did not stop")
        journal: DiagnosticJournal | None = None
        with self._cond:
            self._finish_open_bursts_locked()
            if self._lines and not self._footer_written:
                for line in self._footer_locked().split("\n"):
                    self._append_locked(line)
                self._footer_written = True
            if self._ended_at == "":
                self._ended_at = datetime.now().astimezone().isoformat(timespec="seconds")
            if self._journal is not None and not self._journal_closed:
                journal = self._journal
                self._journal_closed = True
                payload = self._summary_payload_locked(journal)
            else:
                payload = None
        if journal is not None and payload is not None:
            if journal.opened:
                journal.finish("summary", payload)
            else:
                journal.finish("closed", {})
        with self._cond:
            if self._report is None:
                self._report = self._build_report_locked(journal)

    def close(self) -> None:
        self.stop()

    def submit(self, frame: TimedFrame, capture: CaptureCounters | None = None) -> None:
        """Keep the newest frame. A frame the worker has not taken yet is skipped."""
        if not isinstance(frame, TimedFrame):
            raise TypeError("frame must be a TimedFrame")
        if capture is not None and not isinstance(capture, CaptureCounters):
            raise TypeError("capture must be CaptureCounters")
        with self._cond:
            if not self._running:
                return
            self._submitted += 1
            if self._pending is not None:
                self._skipped += 1
            self._pending = frame
            self._pending_capture = capture
            self._cond.notify_all()

    def wait_idle(self, timeout: float = 2.0) -> bool:
        """True once the worker has finished every submitted frame."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._pending is not None or self._in_flight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def set_reference_laps(self, lane: int, laps: int) -> None:
        """Store a hand-counted lap total. Detection does not read it."""
        if isinstance(lane, bool) or not isinstance(lane, int) or lane < 1:
            raise ValueError("lane must be at least 1")
        if isinstance(laps, bool) or not isinstance(laps, int) or laps < 0:
            raise ValueError("laps must not be negative")
        with self._cond:
            self._references[lane] = laps
            report = self._report
            if report is not None:
                self._report = replace(report, references=self._reference_rows_locked(report.zones))

    def report(self) -> DiagnosticReport | None:
        """Closing figures. Present after :meth:`stop`."""
        with self._cond:
            return self._report

    def export_text(self, *, include_frames: bool = False) -> str:
        """Human summary plus structured attempts. Frame rows stay optional."""
        with self._cond:
            report = self._report
            journal = self._journal
        if report is None or journal is None:
            return self.text()
        return export_document(report, journal.records(), include_frames=include_frames)

    def text(self) -> str:
        with self._cond:
            if not self._lines:
                return ""
            return "\n".join(self._lines) + "\n"

    def truncated(self) -> bool:
        with self._cond:
            return self._truncated

    def snapshot(self) -> DiagnosticSnapshot:
        with self._cond:
            if self._latest is not None:
                return self._latest
            return _empty_snapshot(self._performance_locked())

    def activities(self) -> tuple[ActivitySummary, ...]:
        with self._cond:
            return tuple(self._activities)

    def crossings(self) -> tuple[LaneCrossing, ...]:
        """Every crossing this session's detector emitted, in order."""
        with self._cond:
            return tuple(self._all_crossings)

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                while self._pending is None and not self._stop.is_set():
                    self._cond.wait()
                if self._stop.is_set():
                    return
                frame = self._pending
                capture = self._pending_capture
                self._pending = None
                self._pending_capture = None
                self._in_flight = True
            if frame is None:
                continue
            try:
                self._analyze(frame, capture)
            finally:
                with self._cond:
                    self._in_flight = False
                    self._cond.notify_all()

    def _analyze(self, delivered: TimedFrame, capture: CaptureCounters | None) -> None:
        started = time.perf_counter_ns()
        try:
            detector = self._detector_for(delivered)
            crops = delivered.crops
            if crops is not None:
                crossings = detector.observe_crops(crops, delivered.timestamp_ns)
            else:
                crossings = detector.observe(delivered.frame, delivered.timestamp_ns)
            inspection = detector.last_inspection()
        except Exception as error:
            self._note_failure(delivered, capture, error)
            return
        elapsed = time.perf_counter_ns() - started
        self._publish(delivered, capture, crossings, inspection, elapsed)

    def _detector_for(self, delivered: TimedFrame) -> LaneCrossingDetector:
        """Return the shared detector, retargeted once if the full picture size differs."""
        with self._cond:
            detector = self._detector
            if detector is not None and self._matched:
                return detector
            settings = self._settings
            if delivered.crops is None:
                frame = delivered.frame
                if (frame.width, frame.height) != self._expected:
                    settings = scale_detector_settings(
                        settings,
                        self._expected[0],
                        self._expected[1],
                        frame.width,
                        frame.height,
                    )
                    self._settings = settings
                    self._append_locked(
                        "Scaled zones: "
                        f"{self._expected[0]}x{self._expected[1]} -> {frame.width}x{frame.height}"
                    )
                self._actual = (frame.width, frame.height)
            detector = create_lane_detector(settings)
            detector.set_inspection(True)
            self._detector = detector
            self._matched = True
            return detector

    def _publish(
        self,
        delivered: TimedFrame,
        capture: CaptureCounters | None,
        crossings: tuple[LaneCrossing, ...],
        inspection: FrameInspection | None,
        elapsed: int,
    ) -> None:
        frame = _measured_frame(delivered)
        with self._cond:
            self._analyzed += 1
            self._analysis_ns_total += elapsed
            self._last_analysis_ns = elapsed
            self._analysis_ns_max = max(self._analysis_ns_max, elapsed)
            if self._analysis_ns_min is None or elapsed < self._analysis_ns_min:
                self._analysis_ns_min = elapsed
            if len(self._analysis_samples) < _ANALYSIS_SAMPLE_CAP:
                self._analysis_samples.append(elapsed)
            if self._first_ns is None:
                self._first_ns = delivered.timestamp_ns
            dt = None if self._previous_ns is None else delivered.timestamp_ns - self._previous_ns
            self._previous_ns = delivered.timestamp_ns
            self._last_dt_ns = dt
            if dt is not None and dt >= 0:
                self._dt_count += 1
                self._dt_sum += dt
                if self._dt_min is None or dt < self._dt_min:
                    self._dt_min = dt
                if self._dt_max is None or dt > self._dt_max:
                    self._dt_max = dt
            self._note_capture_locked(capture, delivered.timestamp_ns)
            self._actual = (frame.width, frame.height)
            if not self._actual_logged:
                self._append_locked(f"Actual resolution: {frame.width} x {frame.height}")
                for line in _scaled_zone_lines(self._settings, self._config.requested_fps):
                    self._append_locked(line)
                self._actual_logged = True
            elapsed_ns = delivered.timestamp_ns - self._first_ns
            self._append_frame_locked(delivered, dt, elapsed, inspection, crossings, elapsed_ns)
            self._offer_frame_locked(delivered, dt, elapsed, inspection)
            self._all_crossings.extend(crossings)
            if inspection is not None:
                self._follow_activity_locked(
                    inspection, delivered.timestamp_ns, elapsed_ns, delivered.sequence
                )
            performance = self._performance_locked()
            ready = inspection is not None and inspection.reference_ready
            self._latest = DiagnosticSnapshot(
                phase="ready" if ready else "initializing",
                reference_ready=ready,
                frame_index=self._analyzed,
                timestamp_ns=delivered.timestamp_ns,
                elapsed_ns=elapsed_ns,
                width=frame.width,
                height=frame.height,
                inspection=inspection,
                crossings=crossings,
                performance=performance,
                activities=tuple(self._activities),
            )

    def _note_failure(
        self, delivered: TimedFrame, capture: CaptureCounters | None, error: Exception
    ) -> None:
        with self._cond:
            self._analyzed += 1
            self._note_capture_locked(capture, delivered.timestamp_ns)
            self._append_locked(
                f"FRAME {self._analyzed} rejected reason=frame_rejected detail={error}"
            )
            self._latest = DiagnosticSnapshot(
                phase="initializing" if self._latest is None else self._latest.phase,
                reference_ready=False if self._latest is None else self._latest.reference_ready,
                frame_index=self._analyzed,
                timestamp_ns=delivered.timestamp_ns,
                elapsed_ns=self._elapsed_locked(delivered.timestamp_ns),
                width=None,
                height=None,
                inspection=None if self._latest is None else self._latest.inspection,
                crossings=(),
                performance=self._performance_locked(),
                activities=tuple(self._activities),
            )

    def _elapsed_locked(self, timestamp_ns: int) -> int | None:
        if self._first_ns is None:
            return None
        return timestamp_ns - self._first_ns

    def _reset_locked(self) -> None:
        self._pending = None
        self._pending_capture = None
        self._detector = None
        self._matched = False
        self._footer_written = False
        self._truncated = False
        self._lines = []
        self._submitted = 0
        self._analyzed = 0
        self._skipped = 0
        self._analysis_ns_total = 0
        self._analysis_ns_max = 0
        self._last_analysis_ns = 0
        self._previous_ns = None
        self._last_dt_ns = None
        self._first_ns = None
        self._capture_origin = None
        self._last_capture = None
        self._latest = None
        self._bursts = {}
        self._activities = []
        self._all_crossings = []
        self._actual = None
        self._actual_logged = False
        self._in_flight = False
        self._journal = None
        self._journal_closed = False
        self._report = None
        self._analysis_samples = []
        self._analysis_ns_min = None
        self._dt_min = None
        self._dt_max = None
        self._dt_sum = 0
        self._dt_count = 0
        self._ended_at = ""

    def _append_locked(self, line: str) -> None:
        if self._truncated:
            return
        if len(self._lines) >= self._max_lines:
            self._lines.append("[diagnostic log truncated]")
            self._truncated = True
            return
        self._lines.append(line)

    def _append_frame_locked(
        self,
        delivered: TimedFrame,
        dt: int | None,
        analysis_ns: int,
        inspection: FrameInspection | None,
        crossings: tuple[LaneCrossing, ...],
        elapsed_ns: int,
    ) -> None:
        self._append_locked(
            " ".join(
                [
                    f"FRAME {self._analyzed}",
                    f"capture_seq={delivered.sequence}",
                    f"t_ns={delivered.timestamp_ns}",
                    f"t_s={elapsed_ns / 1_000_000_000:.3f}",
                    f"dt_ms={_ms(dt)}",
                    f"analysis_ms={analysis_ns / 1_000_000:.3f}",
                    f"phase={'ready' if inspection is not None else 'initializing'}",
                    f"events={len(crossings)}",
                ]
            )
        )
        if inspection is None:
            return
        for index, zone in enumerate(inspection.zones, start=1):
            self._append_locked(_zone_line(index, zone))
        if self._analyzed % 30 == 0:
            for line in self._footer_locked().split("\n"):
                self._append_locked(line)

    def _follow_activity_locked(
        self,
        inspection: FrameInspection,
        timestamp_ns: int,
        elapsed_ns: int,
        capture_seq: int,
    ) -> None:
        for index, zone in enumerate(inspection.zones, start=1):
            key = (zone.position_id, zone.lane)
            if zone.reason in _IDLE_REASONS:
                burst = self._bursts.pop(key, None)
                if burst is not None:
                    burst.samples_discarded += zone.samples_expired
                    summary = _close_burst(zone, index, burst, timestamp_ns, zone.reason)
                    self._activities.append(summary)
                    self._append_summary_locked(summary, elapsed_ns, zone)
                    self._record_attempt_locked(summary, burst)
                continue
            burst = self._bursts.get(key)
            if burst is None:
                burst = _Burst(
                    started_ns=timestamp_ns,
                    peak_changed_blocks=zone.changed_blocks,
                    peak_changed_ratio=zone.active_ratio,
                    peak_difference=zone.max_difference,
                    direction_confirmed=zone.accepted,
                    detection_event=zone.accepted,
                    reason=zone.reason,
                    last_reason=zone.reason,
                    frames=1,
                    direction_samples=zone.sample_count,
                    first_frame=self._analyzed,
                    last_frame=self._analyzed,
                    capture_seq_start=capture_seq,
                    capture_seq_end=capture_seq,
                    centroids=[],
                    shifts=[],
                )
                self._bursts[key] = burst
                self._note_sample_locked(burst, zone, timestamp_ns)
                self._append_activity_locked(index, zone, elapsed_ns, opened=True)
            else:
                burst.peak_changed_blocks = max(burst.peak_changed_blocks, zone.changed_blocks)
                burst.peak_changed_ratio = max(burst.peak_changed_ratio, zone.active_ratio)
                burst.peak_difference = max(burst.peak_difference, zone.max_difference)
                burst.direction_confirmed = burst.direction_confirmed or zone.accepted
                burst.detection_event = burst.detection_event or zone.accepted
                burst.frames += 1
                burst.direction_samples = max(burst.direction_samples, zone.sample_count)
                burst.last_frame = self._analyzed
                burst.capture_seq_end = capture_seq
                if zone.accepted:
                    burst.reason = "accepted"
                elif not burst.detection_event:
                    burst.reason = zone.reason
                self._note_sample_locked(burst, zone, timestamp_ns)
                if zone.reason != burst.last_reason:
                    burst.last_reason = zone.reason
                    self._append_activity_locked(index, zone, elapsed_ns, opened=False)
                else:
                    burst.last_reason = zone.reason

    def _finish_open_bursts_locked(self) -> None:
        latest = self._latest
        stamp = 0 if latest is None or latest.timestamp_ns is None else latest.timestamp_ns
        elapsed = 0 if latest is None or latest.elapsed_ns is None else latest.elapsed_ns
        inspection = None if latest is None else latest.inspection
        for key, burst in list(self._bursts.items()):
            number = self._zone_numbers.get(key, 0)
            summary = _summary_from_burst(
                burst,
                position_id=key[0],
                lane=key[1],
                zone_number=number,
                ended_ns=stamp,
                closing_reason=burst.reason,
            )
            self._activities.append(summary)
            zone = _zone_by_key(inspection, key)
            self._append_summary_locked(summary, elapsed, zone)
            self._record_attempt_locked(summary, burst)
        self._bursts.clear()

    def _append_activity_locked(
        self, number: int, zone: ZoneInspection, elapsed_ns: int, *, opened: bool
    ) -> None:
        title = "ACTIVITY" if opened else zone.reason.upper()
        if zone.accepted:
            title = "DETECTION"
        shift = "n/a" if zone.shift is None else f"{zone.shift:.2f}"
        self._append_locked(
            " ".join(
                [
                    f"[{elapsed_ns / 1_000_000_000:.3f}]",
                    f"ZONE {number} {title}",
                    f"lane={zone.lane}",
                    f"position={zone.position_id}",
                    f"active_blocks={zone.changed_blocks}",
                    f"active_ratio={zone.active_ratio:.3f}",
                    f"threshold={zone.difference_threshold:.2f}",
                    f"required_blocks={zone.required_blocks}",
                    f"shift={shift}",
                    f"min_shift={zone.min_shift:.2f}",
                    f"samples={zone.sample_count}",
                    f"samples_expired={zone.samples_expired}",
                    f"direction={zone.direction.value}",
                    f"event={'true' if zone.accepted else 'false'}",
                    f"reason={zone.reason}",
                    f"reference_frozen={'true' if zone.reference_frozen else 'false'}",
                    f"reference_updates={zone.reference_updates}",
                    f"background_stable={'true' if zone.background_stable else 'false'}",
                    f"release_candidate={'true' if zone.release_candidate else 'false'}",
                ]
            )
        )
        drawn = _activity_map(zone)
        if drawn is not None and (opened or zone.accepted):
            self._append_locked(f"Zone {number} activity map")
            for row in drawn.split("\n"):
                self._append_locked(row)

    def _append_summary_locked(
        self, summary: ActivitySummary, elapsed_ns: int, zone: ZoneInspection | None
    ) -> None:
        self._append_locked(
            " ".join(
                [
                    f"[{elapsed_ns / 1_000_000_000:.3f}]",
                    f"ZONE {summary.zone_number} ACTIVITY_END",
                    f"lane={summary.lane}",
                    f"position={summary.position_id}",
                    f"activity_frames={summary.activity_frames}",
                    f"activity_duration_ms={summary.duration_ns / 1_000_000:.1f}",
                    f"duration_ms={summary.duration_ns / 1_000_000:.1f}",
                    f"direction_samples={summary.direction_samples}",
                    f"peak_changed_blocks={summary.peak_changed_blocks}",
                    f"peak_changed_ratio={summary.peak_changed_ratio:.3f}",
                    f"peak_difference={summary.peak_difference:.2f}",
                    f"direction_confirmed={'true' if summary.direction_confirmed else 'false'}",
                    f"detection_event={'true' if summary.detection_event else 'false'}",
                    f"reason={summary.reason}",
                    f"direction_reason={summary.direction_reason}",
                ]
            )
        )
        if zone is not None:
            drawn = _activity_map(zone)
            if drawn is not None:
                self._append_locked(f"Zone {summary.zone_number} activity map")
                for row in drawn.split("\n"):
                    self._append_locked(row)

    def _note_capture_locked(self, capture: CaptureCounters | None, timestamp_ns: int) -> None:
        if capture is None:
            return
        self._last_capture = capture
        if capture.captured is None:
            return
        if self._capture_origin is None:
            self._capture_origin = (capture.captured, timestamp_ns)

    def _performance_locked(self) -> DiagnosticPerformance:
        camera_fps = None
        origin = self._capture_origin
        capture = self._last_capture
        latest_ns = None if self._latest is None else self._latest.timestamp_ns
        if (
            origin is not None
            and capture is not None
            and capture.captured is not None
            and latest_ns is not None
            and self._previous_ns is not None
        ):
            delta_frames = capture.captured - origin[0]
            delta_ns = self._previous_ns - origin[1]
            if delta_frames > 0 and delta_ns > 0:
                camera_fps = delta_frames / (delta_ns / 1_000_000_000)
        analysis_fps = None
        if self._analyzed >= 2 and self._first_ns is not None and self._previous_ns is not None:
            span = self._previous_ns - self._first_ns
            if span > 0:
                analysis_fps = (self._analyzed - 1) / (span / 1_000_000_000)
        average = None
        if self._analyzed > 0:
            average = self._analysis_ns_total // self._analyzed
        overwrites = _overwrites(capture)
        captured = None if capture is None else capture.captured
        return DiagnosticPerformance(
            frames_submitted=self._submitted,
            frames_analyzed=self._analyzed,
            frames_skipped=self._skipped,
            frames_captured=captured,
            capture_dropped=overwrites,
            camera_fps=camera_fps,
            analysis_fps=analysis_fps,
            average_analysis_ns=average,
            maximum_analysis_ns=self._analysis_ns_max,
            last_analysis_ns=self._last_analysis_ns,
            last_dt_ns=self._last_dt_ns,
            read_attempts=None if capture is None else capture.read_attempts,
            read_failures=None if capture is None else capture.read_failures,
            source_overwrites=overwrites,
            capture_sequence=None if capture is None else capture.sequence,
            last_capture_dt_ns=None if capture is None else capture.capture_dt_ns,
            preview_frames=None if capture is None else capture.preview_frames,
            preview_replaced=None if capture is None else capture.preview_replaced,
        )

    def _footer_locked(self) -> str:
        performance = self._performance_locked()
        # The footer is built before the snapshot stores this frame's dt.
        # Recompute dt from the clocks already updated for this frame.
        return "\n".join(
            [
                "PERF",
                "CAPTURE",
                f"camera_fps={_fps(performance.camera_fps)}",
                f"capture_frames={_or_waiting(performance.frames_captured)}",
                f"capture_read_attempts={_or_waiting(performance.read_attempts)}",
                f"capture_read_failures={_or_waiting(performance.read_failures)}",
                f"capture_dt_ms={_ms(performance.last_capture_dt_ns)}",
                "PIPELINE",
                f"source_overwrites={_or_waiting(performance.source_overwrites)}",
                f"preview_frames={_or_waiting(performance.preview_frames)}",
                f"preview_replaced={_or_waiting(performance.preview_replaced)}",
                "DETECTOR",
                f"analysis_fps={_fps(performance.analysis_fps)}",
                f"detector_frames_received={performance.frames_submitted}",
                f"detector_frames_analyzed={performance.frames_analyzed}",
                f"detector_frames_skipped={performance.frames_skipped}",
                f"capture_seq={_or_waiting(performance.capture_sequence)}",
                f"average_analysis_ms={_ms(performance.average_analysis_ns)}",
                f"maximum_analysis_ms={_ms(performance.maximum_analysis_ns)}",
            ]
        )

    def _open_journal_locked(self) -> None:
        path = self._journal_path
        if path is None:
            if self._journal_dir is None:
                self._journal_dir = Path(tempfile.mkdtemp(prefix="slot-racing-diagnostic-"))
            path = self._journal_dir / "diagnostic.jsonl"
        journal = DiagnosticJournal(path, max_frame_records=self._max_frame_records)
        journal.open()
        self._journal = journal
        self._journal_closed = False

    def _offer_session_locked(self) -> None:
        journal = self._journal
        if journal is None:
            return
        config = self._config
        journal.offer(
            "session",
            {
                "started_at": config.started_at,
                "requested_width": config.requested_width,
                "requested_height": config.requested_height,
                "requested_fps": config.requested_fps,
                "block_size": config.block_size,
                "sensitivity": config.sensitivity,
                "difference_threshold": config.difference_threshold,
                "min_blocks": config.min_blocks,
                "min_shift": config.min_shift,
                "window_ns": config.window_ns,
                "direction": config.direction,
                "max_frame_records": self._max_frame_records,
            },
        )

    def _offer_frame_locked(
        self,
        delivered: TimedFrame,
        dt: int | None,
        analysis_ns: int,
        inspection: FrameInspection | None,
    ) -> None:
        journal = self._journal
        if journal is None or journal.frame_detail_limited:
            return
        zones: list[dict[str, object]] = []
        if inspection is not None:
            for zone in inspection.zones:
                centroid: list[float] | None
                if zone.centroid is None:
                    centroid = None
                else:
                    centroid = [round(zone.centroid[0], 2), round(zone.centroid[1], 2)]
                shift = None if zone.shift is None else round(zone.shift, 3)
                zones.append(
                    {
                        "lane": zone.lane,
                        "position": zone.position_id,
                        "reason": zone.reason,
                        "state": zone.state.value,
                        "samples": zone.sample_count,
                        "samples_expired": zone.samples_expired,
                        "component_blocks": zone.component_blocks,
                        "changed_blocks": zone.changed_blocks,
                        "shift": shift,
                        "accepted": zone.accepted,
                        "centroid": centroid,
                        "window_ns": zone.window_ns,
                        "reference_frozen": zone.reference_frozen,
                        "reference_updates": zone.reference_updates,
                    }
                )
        journal.offer(
            "frame",
            {
                "frame": self._analyzed,
                "capture_seq": delivered.sequence,
                "t_ns": delivered.timestamp_ns,
                "dt_ns": dt,
                "analysis_ns": analysis_ns,
                "zones": zones,
            },
        )

    def _note_sample_locked(self, burst: _Burst, zone: ZoneInspection, timestamp_ns: int) -> None:
        burst.samples_discarded += zone.samples_expired
        if zone.reason == "below_size":
            burst.below_size_frames += 1
        elif zone.reason == "too_short":
            burst.too_short_frames += 1
        elif zone.reason in {"insufficient_motion", "below_shift"}:
            burst.insufficient_frames += 1
        elif zone.reason == "wrong_direction":
            burst.wrong_direction_frames += 1
        if zone.component_blocks > 0:
            burst.component_sum += zone.component_blocks
            burst.component_peak = max(burst.component_peak, zone.component_blocks)
            burst.component_frames += 1
        keeps_sample = zone.centroid is not None and zone.reason not in {
            "occupied",
            "released",
            "released_to_background",
        }
        if keeps_sample and zone.centroid is not None:
            burst.samples_created += 1
            history = burst.centroids
            if history is None:
                history = []
                burst.centroids = history
            if len(history) < _SAMPLE_HISTORY:
                history.append(
                    (timestamp_ns, round(zone.centroid[0], 2), round(zone.centroid[1], 2))
                )
            else:
                burst.centroids_omitted += 1
        if zone.shift is not None:
            shifts = burst.shifts
            if shifts is None:
                shifts = []
                burst.shifts = shifts
            if len(shifts) < _SAMPLE_HISTORY:
                shifts.append(round(zone.shift, 3))
        state = zone.state.value
        if burst.last_state and state != burst.last_state:
            burst.state_changes += 1
        burst.last_state = state

    def _record_attempt_locked(self, summary: ActivitySummary, burst: _Burst) -> None:
        journal = self._journal
        if journal is None:
            return
        centroids = [] if burst.centroids is None else burst.centroids
        gaps: list[int] = []
        previous: int | None = None
        for stamp, _x, _y in centroids:
            if previous is not None:
                gaps.append(stamp - previous)
            previous = stamp
        average: float | None = None
        if burst.component_frames > 0:
            average = burst.component_sum / burst.component_frames
        if summary.detection_event:
            direction = "confirmed"
        elif summary.direction_reason == "wrong_direction":
            direction = "wrong_direction"
        else:
            direction = "undetermined"
        journal.offer(
            "attempt",
            {
                "lane": summary.lane,
                "position": summary.position_id,
                "zone": summary.zone_number,
                "started_ns": summary.started_ns,
                "ended_ns": summary.ended_ns,
                "samples_created": burst.samples_created,
                "samples_valid": summary.direction_samples,
                "samples_discarded": burst.samples_discarded,
                "sample_gaps_ns": gaps,
                "centroids": [[stamp, x, y] for stamp, x, y in centroids],
                "centroids_omitted": burst.centroids_omitted,
                "shifts": [] if burst.shifts is None else list(burst.shifts),
                "component_peak": burst.component_peak,
                "component_average": average,
                "changed_blocks_peak": summary.peak_changed_blocks,
                "state_changes": burst.state_changes,
                "last_state": burst.last_state,
                "below_size_frames": burst.below_size_frames,
                "too_short_frames": burst.too_short_frames,
                "insufficient_frames": burst.insufficient_frames,
                "wrong_direction_frames": burst.wrong_direction_frames,
                "direction": direction,
                "result": "accepted" if summary.detection_event else "rejected",
                "reason": summary.reason,
                "direction_reason": summary.direction_reason,
                "frame_start": burst.first_frame,
                "frame_end": burst.last_frame,
                "capture_seq_start": burst.capture_seq_start,
                "capture_seq_end": burst.capture_seq_end,
                "activity_frames": summary.activity_frames,
            },
        )

    def _summary_payload_locked(self, journal: DiagnosticJournal) -> dict[str, object]:
        zones = self._zone_reports_locked()
        return {
            "started_at": self._config.started_at,
            "ended_at": self._ended_at,
            "frames_submitted": self._submitted,
            "frames_analyzed": self._analyzed,
            "frames_skipped": self._skipped,
            "events_lost": journal.events_lost,
            "write_errors": journal.write_errors,
            "frame_records_kept": journal.frames_written,
            "frame_detail_limited": journal.frame_detail_limited,
            "text_log_truncated": self._truncated,
            "zones": [
                {
                    "lane": zone.lane,
                    "position": zone.position_id,
                    "confirmed": zone.confirmed,
                    "rejected": zone.rejected,
                    "single_sample": zone.single_sample,
                    "invalid_direction": zone.invalid_direction,
                    "reasons": dict(zone.reasons),
                }
                for zone in zones
            ],
        }

    def _build_report_locked(self, journal: DiagnosticJournal | None) -> DiagnosticReport:
        performance = self._performance_locked()
        zones = self._zone_reports_locked()
        duration = None
        if self._first_ns is not None and self._previous_ns is not None:
            duration = self._previous_ns - self._first_ns
        dt_average = None if self._dt_count == 0 else self._dt_sum // self._dt_count
        opened = False
        error = ""
        lost = 0
        write_errors = 0
        kept = 0
        limited = False
        if journal is not None:
            opened = journal.opened
            error = journal.error
            lost = journal.events_lost
            write_errors = journal.write_errors
            kept = journal.frames_written
            limited = journal.frame_detail_limited
        actual_width = None if self._actual is None else self._actual[0]
        actual_height = None if self._actual is None else self._actual[1]
        config = self._config
        return DiagnosticReport(
            started_at=config.started_at,
            ended_at=self._ended_at,
            duration_ns=duration,
            requested_width=config.requested_width,
            requested_height=config.requested_height,
            actual_width=actual_width,
            actual_height=actual_height,
            requested_fps=config.requested_fps,
            camera_fps=performance.camera_fps,
            analysis_fps=performance.analysis_fps,
            block_size=config.block_size,
            sensitivity=config.sensitivity,
            difference_threshold=config.difference_threshold,
            min_blocks=config.min_blocks,
            min_shift=config.min_shift,
            window_ns=config.window_ns,
            direction=config.direction,
            frames_submitted=self._submitted,
            frames_analyzed=self._analyzed,
            frames_skipped=self._skipped,
            capture_dropped=performance.capture_dropped,
            read_failures=performance.read_failures,
            dt_min_ns=self._dt_min,
            dt_max_ns=self._dt_max,
            dt_average_ns=dt_average,
            analysis_min_ns=self._analysis_ns_min,
            analysis_max_ns=self._analysis_ns_max,
            analysis_average_ns=performance.average_analysis_ns,
            analysis_p95_ns=percentile_nearest(self._analysis_samples, 95),
            events_lost=lost,
            write_errors=write_errors,
            journal_opened=opened,
            journal_error=error,
            frame_records_kept=kept,
            frame_detail_limited=limited,
            text_log_truncated=self._truncated,
            zones=zones,
            references=self._reference_rows_locked(zones),
        )

    def _zone_reports_locked(self) -> tuple[ZoneReport, ...]:
        grouped: dict[tuple[str, int], list[ActivitySummary]] = {}
        for activity in self._activities:
            grouped.setdefault((activity.position_id, activity.lane), []).append(activity)
        reports: list[ZoneReport] = []
        single_codes = {
            "single_sample",
            "single_sample_one_frame",
            "single_sample_below_size",
            "direction_window_expired",
        }
        for (position_id, lane), activities in grouped.items():
            confirmed = [item for item in activities if item.detection_event]
            rejected = [item for item in activities if not item.detection_event]
            reasons: Counter[str] = Counter(
                item.direction_reason or item.reason for item in rejected
            )
            ordered = tuple(sorted(reasons.items()))
            confirmed.sort(key=lambda item: item.ended_ns)
            gaps: list[int] = []
            previous: int | None = None
            for item in confirmed:
                if previous is not None:
                    gaps.append(item.ended_ns - previous)
                previous = item.ended_ns
            reports.append(
                ZoneReport(
                    lane=lane,
                    position_id=position_id,
                    confirmed=len(confirmed),
                    rejected=len(rejected),
                    reasons=ordered,
                    single_sample=sum(
                        1 for item in rejected if item.direction_reason in single_codes
                    ),
                    invalid_direction=sum(
                        1 for item in rejected if item.direction_reason == "invalid_direction"
                    ),
                    confirmed_gaps_ns=tuple(gaps),
                )
            )
        reports.sort(key=lambda item: (item.lane, item.position_id))
        return tuple(reports)

    def _reference_rows_locked(self, zones: tuple[ZoneReport, ...]) -> tuple[LaneReference, ...]:
        confirmed: dict[int, int] = {}
        for zone in zones:
            confirmed[zone.lane] = confirmed.get(zone.lane, 0) + zone.confirmed
        rows = [
            LaneReference(
                lane=lane,
                reference_laps=laps,
                confirmed_events=confirmed.get(lane, 0),
            )
            for lane, laps in sorted(self._references.items())
        ]
        return tuple(rows)


def _overwrites(capture: CaptureCounters | None) -> int | None:
    """Latest-frame replacements seen with this frame. Not a failed read."""
    if capture is None:
        return None
    if capture.source_overwrites is not None:
        return capture.source_overwrites
    return capture.dropped


def _measured_frame(delivered: TimedFrame) -> GrayFrame:
    crops = delivered.crops
    if crops:
        return delivered.frame
    return delivered.frame


def _empty_snapshot(performance: DiagnosticPerformance) -> DiagnosticSnapshot:
    return DiagnosticSnapshot(
        phase="initializing",
        reference_ready=False,
        frame_index=0,
        timestamp_ns=None,
        elapsed_ns=None,
        width=None,
        height=None,
        inspection=None,
        crossings=(),
        performance=performance,
        activities=(),
    )


def _zone_line(number: int, zone: ZoneInspection) -> str:
    rows = len(zone.analysis)
    cols = len(zone.analysis[0]) if rows else 0
    shift = "n/a" if zone.shift is None else f"{zone.shift:.2f}"
    return " ".join(
        [
            f"Z{number}",
            f"lane={zone.lane}",
            f"position={zone.position_id}",
            f"grid={cols}x{rows}",
            f"block={zone.block_size}",
            f"mean_diff={zone.mean_difference:.2f}",
            f"max_diff={zone.max_difference:.2f}",
            f"active_blocks={zone.changed_blocks}",
            f"active_ratio={zone.active_ratio:.3f}",
            f"threshold={zone.difference_threshold:.2f}",
            f"required_blocks={zone.required_blocks}",
            f"component_blocks={zone.component_blocks}",
            f"shift={shift}",
            f"min_shift={zone.min_shift:.2f}",
            f"samples={zone.sample_count}",
            f"samples_expired={zone.samples_expired}",
            f"window_ms={zone.window_ns / 1_000_000:.1f}",
            f"state={zone.state.value}",
            f"direction={zone.direction.value}",
            f"event={'true' if zone.accepted else 'false'}",
            f"reason={zone.reason}",
            f"reference_frozen={'true' if zone.reference_frozen else 'false'}",
            f"reference_updates={zone.reference_updates}",
            f"background_stable={'true' if zone.background_stable else 'false'}",
            f"release_candidate={'true' if zone.release_candidate else 'false'}",
        ]
    )


def _close_burst(
    zone: ZoneInspection, number: int, burst: _Burst, timestamp_ns: int, reason: str
) -> ActivitySummary:
    closing = burst.reason if burst.reason not in _IDLE_REASONS else reason
    return _summary_from_burst(
        burst,
        position_id=zone.position_id,
        lane=zone.lane,
        zone_number=number,
        ended_ns=timestamp_ns,
        closing_reason=closing,
    )


def _summary_from_burst(
    burst: _Burst,
    *,
    position_id: str,
    lane: int,
    zone_number: int,
    ended_ns: int,
    closing_reason: str,
) -> ActivitySummary:
    detected = burst.detection_event
    raw = "accepted" if detected else closing_reason
    reason = _activity_reason(raw, detected=detected)
    code = direction_reason_code(
        detected=detected,
        max_samples=burst.direction_samples,
        samples_expired=burst.samples_discarded,
        below_size_frames=burst.below_size_frames,
        frames=burst.frames,
        algorithm_reason=reason,
        wrong_direction_frames=burst.wrong_direction_frames,
    )
    return ActivitySummary(
        position_id=position_id,
        lane=lane,
        zone_number=zone_number,
        started_ns=burst.started_ns,
        ended_ns=ended_ns,
        duration_ns=max(0, ended_ns - burst.started_ns),
        peak_changed_blocks=burst.peak_changed_blocks,
        peak_changed_ratio=burst.peak_changed_ratio,
        peak_difference=burst.peak_difference,
        direction_confirmed=burst.direction_confirmed,
        detection_event=detected,
        reason=reason,
        activity_frames=burst.frames,
        direction_samples=burst.direction_samples,
        direction_reason=code,
    )


def _activity_reason(reason: str, *, detected: bool) -> str:
    """Name the algorithm fact that stopped a burst. Not a guess about speed."""
    if detected:
        return "accepted"
    return {
        "too_short": "too_few_direction_samples",
        "below_size": "below_component_size",
        "insufficient_motion": "below_shift",
        "wrong_direction": "wrong_direction",
    }.get(reason, reason)


def _zone_quality_name(zone: ZoneInspection) -> str:
    rows = len(zone.analysis)
    cols = len(zone.analysis[0]) if rows else 0
    if rows < 1 or cols < 1:
        return "n/a"
    assessed = evaluate_detection_zone(
        cols * zone.block_size,
        rows * zone.block_size,
        zone.block_size,
        zone.direction,
    )
    return assessed.rating.value


def _scaled_zone_lines(settings: DetectorSettings, fps: int) -> tuple[str, ...]:
    lines: list[str] = []
    for index, zone in enumerate(settings.zones, start=1):
        assessed = evaluate_detection_zone(
            zone.roi.width,
            zone.roi.height,
            settings.block_size,
            settings.direction,
            fps,
        )
        lines.append(
            " ".join(
                [
                    f"Zone {index} scaled:",
                    f"width={zone.roi.width}",
                    f"height={zone.roi.height}",
                    f"grid={assessed.usable_blocks_x}x{assessed.usable_blocks_y}",
                    f"travel_blocks={assessed.blocks_in_travel_direction}",
                    f"cross_blocks={assessed.blocks_cross_direction}",
                    f"quality={assessed.rating.value}",
                    f"block={assessed.block_size}",
                ]
            )
        )
    return tuple(lines)


def _zone_by_key(inspection: FrameInspection | None, key: tuple[str, int]) -> ZoneInspection | None:
    if inspection is None:
        return None
    for zone in inspection.zones:
        if (zone.position_id, zone.lane) == key:
            return zone
    return None


def _activity_map(zone: ZoneInspection) -> str | None:
    rows = len(zone.difference)
    cols = len(zone.difference[0]) if rows else 0
    if rows == 0 or cols == 0 or rows > _MAX_MAP_ROWS or cols > _MAX_MAP_COLS:
        return None
    if rows * cols > _MAX_MAP_CELLS:
        return None
    threshold = zone.difference_threshold
    drawn: list[str] = []
    for row in range(rows):
        chars: list[str] = []
        for col in range(cols):
            diff = zone.difference[row][col]
            if threshold > 0 and diff >= threshold:
                chars.append("#")
            elif threshold > 0 and diff >= threshold * 0.5:
                chars.append(":")
            elif diff > 0:
                chars.append(".")
            else:
                chars.append(".")
        drawn.append("".join(chars))
    return "\n".join(drawn)


def _view_grid(zone: ZoneInspection, view: DiagnosticView) -> np.ndarray:
    rows = len(zone.analysis)
    cols = len(zone.analysis[0]) if rows else 0
    grid = np.zeros((rows, cols), dtype=np.uint8)
    for row in range(rows):
        for col in range(cols):
            if view is DiagnosticView.THRESHOLD:
                grid[row, col] = 255 if zone.active[row][col] else 0
            elif view is DiagnosticView.DIFFERENCE:
                grid[row, col] = _clip_u8(zone.difference[row][col])
            elif view is DiagnosticView.REFERENCE:
                grid[row, col] = _clip_u8(zone.reference[row][col])
            else:
                grid[row, col] = _clip_u8(zone.analysis[row][col])
    return grid


def _display_scale(grids: list[np.ndarray]) -> int:
    longest = max(max(int(grid.shape[0]), int(grid.shape[1]), 1) for grid in grids)
    return max(4, min(16, 480 // longest))


def _clip_u8(value: float) -> int:
    rounded = round(value)
    if rounded < 0:
        return 0
    if rounded > 255:
        return 255
    return rounded


def _ms(value: int | None) -> str:
    if value is None:
        return "waiting"
    return f"{value / 1_000_000:.3f}"


def _fps(value: float | None) -> str:
    if value is None:
        return "waiting"
    return f"{value:.2f}"


def _or_waiting(value: int | None) -> str:
    if value is None:
        return "waiting"
    return str(value)


def _resolution(snapshot: DiagnosticSnapshot) -> str:
    if snapshot.width is None or snapshot.height is None:
        return "waiting"
    return f"{snapshot.width} x {snapshot.height}"


# The worker publishes snapshots. Callers that only need the type of hook stay explicit.
DiagnosticListener = Callable[[TimedFrame, CaptureCounters | None], None]
