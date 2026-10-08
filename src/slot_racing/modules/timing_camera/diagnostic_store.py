"""Append-only diagnostic journal.

The camera thread never calls this. A session enqueues small records and a
writer thread owns the file. Frame detail can stop. Attempts and the closing
summary do not.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

# One line stays small. Matrices and camera pixels are not stored.
_QUEUE_LIMIT = 2048
_FLUSH_EVERY = 64

REASON_TEXT: dict[str, str] = {
    "accepted": "Durchfahrt bestätigt.",
    "single_sample_one_frame": (
        "Nur ein verwertbares Bewegungssample: das Objekt war nur in einem Frame groß genug."
    ),
    "single_sample_below_size": (
        "Nur ein verwertbares Bewegungssample. Weitere Frames dieser Bewegung "
        "blieben unter der verbundenen Mindestgröße."
    ),
    "direction_window_expired": (
        "Nur ein verwertbares Bewegungssample, weil ältere Samples außerhalb "
        "des Richtungszeitfensters lagen."
    ),
    "single_sample": (
        "Nur ein verwertbares Bewegungssample. Ein zweites Sample wurde nicht erzeugt."
    ),
    "below_shift": "Mehrere Samples, die Verschiebung bleibt unter dem Schwellwert.",
    "wrong_direction": "Die Verschiebung läuft entgegen der eingestellten Fahrtrichtung.",
    "invalid_direction": "Mehrere Samples, die Richtung wurde nicht bestätigt.",
    "below_component_size": "Die Änderung blieb unter der verbundenen Mindestgröße.",
    "too_few_direction_samples": "Zu wenige Bewegungssamples für eine Richtung.",
}


def direction_reason_code(
    *,
    detected: bool,
    max_samples: int,
    samples_expired: int,
    below_size_frames: int,
    frames: int,
    algorithm_reason: str,
    wrong_direction_frames: int = 0,
) -> str:
    """Name why a closed attempt did or did not get a direction. No new guess."""
    if detected:
        return "accepted"
    # A rejected opposite shift clears the sample list back to one entry.
    if algorithm_reason == "wrong_direction" or wrong_direction_frames > 0:
        return "wrong_direction"
    if max_samples < 2:
        if samples_expired > 0:
            return "direction_window_expired"
        if frames <= 1:
            return "single_sample_one_frame"
        if below_size_frames > 0:
            return "single_sample_below_size"
        return "single_sample"
    if algorithm_reason in {"below_shift", "insufficient_motion"}:
        return "below_shift"
    if algorithm_reason == "below_component_size":
        return "below_component_size"
    return "invalid_direction"


def reason_text(code: str) -> str:
    return REASON_TEXT.get(code, code)


def percentile_nearest(values: list[int], percent: float) -> int | None:
    """Nearest-rank percentile. An empty list stays unset."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, round(percent / 100 * len(ordered)))
    return ordered[min(len(ordered) - 1, rank - 1)]


@dataclass(frozen=True, slots=True)
class LaneReference:
    """A hand-entered lap count. It is not a detection result."""

    lane: int
    reference_laps: int
    confirmed_events: int

    @property
    def difference(self) -> int:
        return self.confirmed_events - self.reference_laps

    @property
    def ratio(self) -> float | None:
        if self.reference_laps <= 0:
            return None
        return self.confirmed_events / self.reference_laps


@dataclass(frozen=True, slots=True)
class ZoneReport:
    lane: int
    position_id: str
    confirmed: int
    rejected: int
    reasons: tuple[tuple[str, int], ...]
    single_sample: int
    invalid_direction: int
    confirmed_gaps_ns: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Closing figures. Built from counters, not from the truncated text log."""

    started_at: str
    ended_at: str
    duration_ns: int | None
    requested_width: int
    requested_height: int
    actual_width: int | None
    actual_height: int | None
    requested_fps: int
    camera_fps: float | None
    analysis_fps: float | None
    block_size: int
    sensitivity: int
    difference_threshold: float
    min_blocks: int
    min_shift: float
    window_ns: int
    direction: str
    frames_submitted: int
    frames_analyzed: int
    frames_skipped: int
    capture_dropped: int | None
    read_failures: int | None
    dt_min_ns: int | None
    dt_max_ns: int | None
    dt_average_ns: int | None
    analysis_min_ns: int | None
    analysis_max_ns: int
    analysis_average_ns: int | None
    analysis_p95_ns: int | None
    events_lost: int
    write_errors: int
    journal_opened: bool
    journal_error: str
    frame_records_kept: int
    frame_detail_limited: bool
    text_log_truncated: bool
    zones: tuple[ZoneReport, ...]
    references: tuple[LaneReference, ...]

    @property
    def events_complete(self) -> bool:
        return self.journal_opened and self.events_lost == 0 and self.write_errors == 0

    @property
    def frame_detail_complete(self) -> bool:
        return self.events_complete and not self.frame_detail_limited


def format_summary(report: DiagnosticReport) -> str:
    """Operator text. Keys stay stable so a later export can be checked."""
    lines = [
        "=== Diagnoseabschluss ===",
        f"events_complete={'true' if report.events_complete else 'false'}",
        f"frame_detail_complete={'true' if report.frame_detail_complete else 'false'}",
        f"text_log_truncated={'true' if report.text_log_truncated else 'false'}",
        f"events_lost={report.events_lost}",
        f"write_errors={report.write_errors}",
        "",
        "Allgemein",
        f"Beginn: {report.started_at}",
        f"Ende: {report.ended_at}",
        f"Dauer ms: {_ms(report.duration_ns)}",
        f"Angeforderte Auflösung: {report.requested_width} x {report.requested_height}",
        f"Tatsächliche Auflösung: {_resolution(report.actual_width, report.actual_height)}",
        f"Angeforderte Bildrate: {report.requested_fps}",
        f"Tatsächliche Bildrate: {_fps(report.camera_fps)}",
        f"Analyse-Bildrate: {_fps(report.analysis_fps)}",
        f"Erkennungsauflösung: Block {report.block_size} px",
        f"Empfindlichkeit: {report.sensitivity}",
        f"Differenzschwellwert: {report.difference_threshold:.2f}",
        f"Mindestblöcke: {report.min_blocks}",
        f"Mindestverschiebung: {report.min_shift:.2f}",
        f"Richtungszeitfenster ms: {report.window_ns / 1_000_000:.1f}",
        f"Fahrtrichtung: {report.direction}",
        "",
        "Performance",
        f"frames_submitted={report.frames_submitted}",
        f"frames_analyzed={report.frames_analyzed}",
        f"frames_skipped={report.frames_skipped}",
        f"capture_dropped={_or_waiting(report.capture_dropped)}",
        f"read_failures={_or_waiting(report.read_failures)}",
        f"dt_min_ms={_ms(report.dt_min_ns)}",
        f"dt_average_ms={_ms(report.dt_average_ns)}",
        f"dt_max_ms={_ms(report.dt_max_ns)}",
        f"analysis_min_ms={_ms(report.analysis_min_ns)}",
        f"analysis_average_ms={_ms(report.analysis_average_ns)}",
        f"analysis_p95_ms={_ms(report.analysis_p95_ns)}",
        f"analysis_max_ms={_ms(report.analysis_max_ns)}",
        f"frame_records_kept={report.frame_records_kept}",
        "",
        "Erkennung",
    ]
    if not report.zones:
        lines.append("Zonen: keine abgeschlossenen Bewegungsversuche")
    for zone in report.zones:
        lines.append(
            " ".join(
                [
                    f"lane={zone.lane}",
                    f"position={zone.position_id}",
                    f"confirmed={zone.confirmed}",
                    f"rejected={zone.rejected}",
                    f"single_sample={zone.single_sample}",
                    f"invalid_direction={zone.invalid_direction}",
                ]
            )
        )
        for code, count in zone.reasons:
            lines.append(f"reason {code}={count} ({reason_text(code)})")
        if zone.confirmed_gaps_ns:
            shown = ", ".join(f"{gap / 1_000_000:.1f}" for gap in zone.confirmed_gaps_ns)
            lines.append(f"confirmed_gaps_ms lane={zone.lane}: {shown}")
    lines.extend(["", "Referenzrunden"])
    if not report.references:
        lines.append("Keine Referenzrunden eingetragen.")
    else:
        lines.append(
            "Das Verhältnis ist keine Erkennungsquote. Fehlende Durchfahrten "
            "und Fehlauslösungen können sich ausgleichen."
        )
    for item in report.references:
        ratio = "n/a" if item.ratio is None else f"{item.ratio:.3f}"
        lines.append(
            " ".join(
                [
                    f"lane={item.lane}",
                    f"reference_laps={item.reference_laps}",
                    f"confirmed_events={item.confirmed_events}",
                    f"difference={item.difference}",
                    f"ratio={ratio}",
                ]
            )
        )
    if report.text_log_truncated:
        lines.extend(
            [
                "",
                "Das Textprotokoll wurde gekürzt und deckt nicht den ganzen Zeitraum ab.",
            ]
        )
    if report.frame_detail_limited:
        lines.append(
            f"Detaillierte Frame-Daten wurden begrenzt. Behalten: {report.frame_records_kept}."
        )
    if not report.events_complete:
        detail = report.journal_error or "Schreibfehler oder verworfene Ereignisse"
        lines.append(f"Ereignisaufzeichnung unvollständig: {detail}")
    else:
        lines.append("Ereignisse und Abschlussstatistik decken die ganze Aufzeichnung ab.")
    return "\n".join(lines)


class DiagnosticJournal:
    """JSON Lines on disk. ``offer`` does not wait for the disk."""

    def __init__(self, path: Path, *, max_frame_records: int) -> None:
        if isinstance(max_frame_records, bool) or not isinstance(max_frame_records, int):
            raise TypeError("max_frame_records must be an int")
        if max_frame_records < 0:
            raise ValueError("max_frame_records must not be negative")
        self.path = path
        self.max_frame_records = max_frame_records
        self.events_lost = 0
        self.write_errors = 0
        self.frames_written = 0
        self.frame_detail_limited = False
        self.opened = False
        self.error = ""
        self._lock = threading.Lock()
        self._queue: queue.Queue[dict[str, object] | None] = queue.Queue(maxsize=_QUEUE_LIMIT)
        self._seq = 0
        self._frames_accepted = 0
        self._limit_marked = False
        self._sealed = False
        self._stopping = threading.Event()
        self._file: TextIO | None = None
        self._thread: threading.Thread | None = None

    def open(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("w", encoding="utf-8")
        except OSError as error:
            self.write_errors += 1
            self.error = str(error)
            self.opened = False
            return
        self.opened = True
        self._thread = threading.Thread(
            target=self._run, name="slot-racing-diagnostic-journal", daemon=True
        )
        self._thread.start()

    def offer(self, kind: str, fields: Mapping[str, object]) -> None:
        """Queue one record. A full queue drops frame detail before attempts."""
        with self._lock:
            if self._sealed:
                if kind != "frame":
                    self.events_lost += 1
                return
            if kind == "frame" and (self.frame_detail_limited or not self.opened):
                return
            if not self.opened:
                if kind not in {"frame", "frame_detail_limited"}:
                    self.events_lost += 1
                return
            if kind == "frame" and self._frames_accepted >= self.max_frame_records:
                self._mark_frame_limit()
                return
            if not self._enqueue(kind, fields):
                return
            if kind == "frame":
                self._frames_accepted += 1

    def finish(self, kind: str, fields: Mapping[str, object]) -> None:
        """Stop the writer, append one last record, and close the file."""
        with self._lock:
            if self._sealed:
                return
            self._sealed = True
        self._stop_writer()
        if self.opened and self._file is not None and self._thread is None:
            self.write_sync(kind, fields)
            return
        if self.opened and self._thread is not None:
            self.write_errors += 1
            self.error = self.error or "journal thread did not stop"
        self._close_file()

    def close(self) -> None:
        """Flush and close. A second call does nothing."""
        self.finish("closed", {})

    def write_sync(self, kind: str, fields: Mapping[str, object]) -> None:
        """One last record after the writer has stopped."""
        if not self.opened or self._file is None:
            if kind != "frame":
                self.events_lost += 1
            return
        with self._lock:
            self._seq += 1
            record: dict[str, object] = {"seq": self._seq, "kind": kind, **dict(fields)}
        self._write(record)
        self._close_file()

    def records(self) -> list[dict[str, object]]:
        if not self.path.is_file():
            return []
        found: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                loaded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(loaded, dict):
                found.append(loaded)
        return found

    def _mark_frame_limit(self) -> None:
        self.frame_detail_limited = True
        if self._limit_marked:
            return
        self._limit_marked = True
        self._enqueue(
            "frame_detail_limited",
            {"kept": self._frames_accepted, "limit": self.max_frame_records},
        )

    def _enqueue(self, kind: str, fields: Mapping[str, object]) -> bool:
        self._seq += 1
        record: dict[str, object] = {"seq": self._seq, "kind": kind, **dict(fields)}
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            if kind == "frame":
                self._mark_frame_limit()
            else:
                self.events_lost += 1
            return False
        return True

    def _stop_writer(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stopping.set()
        if thread.is_alive():
            try:
                self._queue.put(None, timeout=2)
            except queue.Full:
                self.error = self.error or "journal queue did not accept the stop signal"
            thread.join(timeout=5)
            if thread.is_alive():
                self.write_errors += 1
                self.error = self.error or "journal thread did not stop"
                return
        self._thread = None

    def _run(self) -> None:
        handle = self._file
        if handle is None:
            return
        pending = 0
        while True:
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                if pending:
                    self._flush(handle)
                    pending = 0
                if self._stopping.is_set():
                    break
                continue
            if item is None:
                break
            if item.get("kind") == "frame":
                self.frames_written += 1
            if self._write(item):
                pending += 1
            if pending >= _FLUSH_EVERY:
                self._flush(handle)
                pending = 0
        while True:
            try:
                leftover = self._queue.get_nowait()
            except queue.Empty:
                break
            if leftover is None:
                continue
            if leftover.get("kind") == "frame":
                self.frames_written += 1
            self._write(leftover)
        self._flush(handle)

    def _write(self, record: Mapping[str, object]) -> bool:
        handle = self._file
        if handle is None:
            self.write_errors += 1
            return False
        try:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError as error:
            self.write_errors += 1
            self.error = str(error)
            return False
        return True

    def _flush(self, handle: TextIO) -> None:
        try:
            handle.flush()
        except OSError as error:
            self.write_errors += 1
            self.error = str(error)

    def _close_file(self) -> None:
        handle = self._file
        self._file = None
        if handle is None:
            return
        try:
            handle.flush()
            handle.close()
        except OSError as error:
            self.write_errors += 1
            self.error = str(error)


def export_document(
    report: DiagnosticReport,
    records: list[dict[str, object]],
    *,
    include_frames: bool,
) -> str:
    """Summary, then attempts. Frame rows stay out unless requested."""
    parts = [format_summary(report), "", "=== Ereignisse ==="]
    for record in records:
        kind = record.get("kind")
        if kind == "frame" and not include_frames:
            continue
        parts.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    if report.frame_detail_limited and not include_frames:
        parts.append("Frame-Details sind unvollständig und wurden in diesem Export weggelassen.")
    return "\n".join(parts) + "\n"


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


def _resolution(width: int | None, height: int | None) -> str:
    if width is None or height is None:
        return "waiting"
    return f"{width} x {height}"
