"""Diagnostic session over the same lane detector a race uses.

The session does not open a camera and does not score a crossing of its own.
It feeds frames into :func:`create_lane_detector` and records the block matrices
that detector already compared. A worker thread does that work. ``submit`` only
keeps the newest unread frame, the same latest-frame rule as capture.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
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
    sensitivity_profile,
)
from slot_racing.modules.timing_camera.frame_source import TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame

MAX_LOG_LINES = 12_000
_MAX_MAP_CELLS = 512
_MAX_MAP_ROWS = 40
_MAX_MAP_COLS = 64
_IDLE_REASONS = frozenset({"clear", "calibrated", "released", "synchronized_clear"})
Phase = Literal["initializing", "ready"]


class DiagnosticView(StrEnum):
    """Which real matrix the preview shows. The pixels are not reinterpreted."""

    ANALYSIS = "analysis"
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
    """Counters from the capture source, when that source publishes them."""

    captured: int | None = None
    dropped: int | None = None


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
        "Algorithm: directional block difference against a fixed reference",
        f"Requested block size: {config.block_size}",
        "Processing: per-zone crop, then mean of each full block; partial tiles dropped",
        f"Zone count: {len(config.zones)}",
        f"Sensitivity: {config.sensitivity}",
        f"Difference threshold: {config.difference_threshold:.2f}",
        f"Minimum connected blocks: {config.min_blocks}",
        f"Minimum shift: {config.min_shift:.2f}",
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
        f"Frames received: {performance.frames_submitted}",
        f"Frames analyzed: {performance.frames_analyzed}",
        f"Frames skipped: {performance.frames_skipped}",
        f"Capture dropped: {_or_waiting(performance.capture_dropped)}",
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
            ]
        )
    return "\n".join(lines)


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
    ) -> None:
        if not isinstance(settings, DetectorSettings):
            raise TypeError("settings must be DetectorSettings")
        if not isinstance(config, DiagnosticConfig):
            raise TypeError("config must be a DiagnosticConfig")
        if isinstance(max_lines, bool) or not isinstance(max_lines, int) or max_lines < 1:
            raise ValueError("max_lines must be at least 1")
        self._base_settings = settings
        self._settings = settings
        self._config = config
        self._expected = (config.requested_width, config.requested_height)
        self._max_lines = max_lines
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
            for line in format_header(self._config).split("\n"):
                self._append_locked(line)
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
        with self._cond:
            self._finish_open_bursts_locked()
            if self._lines and not self._footer_written:
                for line in self._footer_locked().split("\n"):
                    self._append_locked(line)
                self._footer_written = True

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
            self._cond.notify()

    def wait_idle(self, timeout: float = 2.0) -> bool:
        """True once the worker has finished every submitted frame."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cond:
                if self._pending is None and not self._in_flight:
                    return True
            time.sleep(0.005)
        return False

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
            if self._first_ns is None:
                self._first_ns = delivered.timestamp_ns
            dt = None if self._previous_ns is None else delivered.timestamp_ns - self._previous_ns
            self._previous_ns = delivered.timestamp_ns
            self._last_dt_ns = dt
            self._note_capture_locked(capture, delivered.timestamp_ns)
            self._actual = (frame.width, frame.height)
            if not self._actual_logged:
                self._append_locked(f"Actual resolution: {frame.width} x {frame.height}")
                self._actual_logged = True
            elapsed_ns = delivered.timestamp_ns - self._first_ns
            self._append_frame_locked(delivered, dt, elapsed, inspection, crossings, elapsed_ns)
            self._all_crossings.extend(crossings)
            if inspection is not None:
                self._follow_activity_locked(inspection, delivered.timestamp_ns, elapsed_ns)
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
        self, inspection: FrameInspection, timestamp_ns: int, elapsed_ns: int
    ) -> None:
        for index, zone in enumerate(inspection.zones, start=1):
            key = (zone.position_id, zone.lane)
            if zone.reason in _IDLE_REASONS:
                burst = self._bursts.pop(key, None)
                if burst is not None:
                    summary = _close_burst(zone, index, burst, timestamp_ns, zone.reason)
                    self._activities.append(summary)
                    self._append_summary_locked(summary, elapsed_ns, zone)
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
                )
                self._bursts[key] = burst
                self._append_activity_locked(index, zone, elapsed_ns, opened=True)
            else:
                burst.peak_changed_blocks = max(burst.peak_changed_blocks, zone.changed_blocks)
                burst.peak_changed_ratio = max(burst.peak_changed_ratio, zone.active_ratio)
                burst.peak_difference = max(burst.peak_difference, zone.max_difference)
                burst.direction_confirmed = burst.direction_confirmed or zone.accepted
                burst.detection_event = burst.detection_event or zone.accepted
                if zone.accepted:
                    burst.reason = "accepted"
                elif not burst.detection_event:
                    burst.reason = zone.reason
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
            summary = ActivitySummary(
                position_id=key[0],
                lane=key[1],
                zone_number=number,
                started_ns=burst.started_ns,
                ended_ns=stamp,
                duration_ns=max(0, stamp - burst.started_ns),
                peak_changed_blocks=burst.peak_changed_blocks,
                peak_changed_ratio=burst.peak_changed_ratio,
                peak_difference=burst.peak_difference,
                direction_confirmed=burst.direction_confirmed,
                detection_event=burst.detection_event,
                reason=burst.reason,
            )
            self._activities.append(summary)
            zone = _zone_by_key(inspection, key)
            self._append_summary_locked(summary, elapsed, zone)
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
                    f"duration_ms={summary.duration_ns / 1_000_000:.1f}",
                    f"peak_changed_blocks={summary.peak_changed_blocks}",
                    f"peak_changed_ratio={summary.peak_changed_ratio:.3f}",
                    f"peak_difference={summary.peak_difference:.2f}",
                    f"direction_confirmed={'true' if summary.direction_confirmed else 'false'}",
                    f"detection_event={'true' if summary.detection_event else 'false'}",
                    f"reason={summary.reason}",
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
        dropped = None if capture is None else capture.dropped
        captured = None if capture is None else capture.captured
        return DiagnosticPerformance(
            frames_submitted=self._submitted,
            frames_analyzed=self._analyzed,
            frames_skipped=self._skipped,
            frames_captured=captured,
            capture_dropped=dropped,
            camera_fps=camera_fps,
            analysis_fps=analysis_fps,
            average_analysis_ns=average,
            maximum_analysis_ns=self._analysis_ns_max,
            last_analysis_ns=self._last_analysis_ns,
            last_dt_ns=self._last_dt_ns,
        )

    def _footer_locked(self) -> str:
        performance = self._performance_locked()
        # The footer is built before the snapshot stores this frame's dt.
        # Recompute dt from the clocks already updated for this frame.
        return "\n".join(
            [
                "PERF",
                f"camera_fps={_fps(performance.camera_fps)}",
                f"analysis_fps={_fps(performance.analysis_fps)}",
                f"frames_received={performance.frames_submitted}",
                f"frames_analyzed={performance.frames_analyzed}",
                f"frames_skipped={performance.frames_skipped}",
                f"capture_dropped={_or_waiting(performance.capture_dropped)}",
                f"average_analysis_ms={_ms(performance.average_analysis_ns)}",
                f"maximum_analysis_ms={_ms(performance.maximum_analysis_ns)}",
            ]
        )


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
        ]
    )


def _close_burst(
    zone: ZoneInspection, number: int, burst: _Burst, timestamp_ns: int, reason: str
) -> ActivitySummary:
    closed_reason = burst.reason if burst.detection_event else reason
    if burst.detection_event:
        closed_reason = "accepted"
    elif burst.reason not in _IDLE_REASONS:
        closed_reason = burst.reason
    return ActivitySummary(
        position_id=zone.position_id,
        lane=zone.lane,
        zone_number=number,
        started_ns=burst.started_ns,
        ended_ns=timestamp_ns,
        duration_ns=max(0, timestamp_ns - burst.started_ns),
        peak_changed_blocks=burst.peak_changed_blocks,
        peak_changed_ratio=burst.peak_changed_ratio,
        peak_difference=burst.peak_difference,
        direction_confirmed=burst.direction_confirmed,
        detection_event=burst.detection_event,
        reason=closed_reason,
    )


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
