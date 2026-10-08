"""Detection diagnosis window.

The window does not open a camera. Frames arrive from the setup page, which
already owns the preview source. Detection runs in :class:`DiagnosticSession`
on that session's worker, using :func:`create_lane_detector`.
"""

from __future__ import annotations

import datetime as dt
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.i18n import Translator
from slot_racing.modules.timing_camera.configuration import (
    CameraConfiguration,
    to_detector_settings,
)
from slot_racing.modules.timing_camera.detection import (
    ZoneInspection,
    evaluate_detection_zone,
    resolution_preset_name,
)
from slot_racing.modules.timing_camera.diagnostic import (
    MAX_LOG_LINES,
    CaptureCounters,
    DiagnosticConfig,
    DiagnosticListener,
    DiagnosticPicture,
    DiagnosticSession,
    DiagnosticSnapshot,
    DiagnosticView,
    diagnostic_config,
    format_live,
    render_zone,
)
from slot_racing.modules.timing_camera.frame_source import TimedFrame
from slot_racing.uikit.theme import COLORS, configure_page, set_role, set_tone

_REFRESH_MS = 50
_FLASH_S = 0.8
_LOG_TAIL = 80


class DiagnosticHost(Protocol):
    """The setup page. Diagnosis never asks it to open a device."""

    def diagnostic_document(self) -> CameraConfiguration:
        """Current camera document, including zones that are not saved yet."""

    def attach_diagnostic(self, listener: DiagnosticListener) -> None:
        """Deliver each preview frame to ``listener``."""

    def detach_diagnostic(self, listener: DiagnosticListener) -> None:
        """Stop delivering frames to ``listener``."""


class DetectionDiagnosticDialog(QDialog):
    """Live block view, measurements and an exportable detection log."""

    def __init__(
        self, translator: Translator, host: DiagnosticHost, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("camera-diagnostic-dialog")
        self.setWindowTitle(translator.translate("camera.diagnostic.title"))
        self.resize(1180, 760)
        self._cards: list[_ZoneCard] = []
        self._translator = translator
        self._host = host
        self._lane_references: dict[int, int] = {}
        self._session: DiagnosticSession | None = None
        self._config: DiagnosticConfig | None = None
        self._view = DiagnosticView.ANALYSIS
        self._image: QImage | None = None
        self._flash_until = 0.0
        self._flash_zones: tuple[int, ...] = ()
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        self.view = QComboBox()
        self.view.setObjectName("camera-diagnostic-view")
        for item in DiagnosticView:
            self.view.addItem(self._tr(f"camera.diagnostic.view.{item.value}"), item.value)
        self._zone_host = QWidget()
        self._zone_host.setObjectName("camera-diagnostic-zones")
        self._zone_grid = QGridLayout(self._zone_host)
        self._zone_grid.setContentsMargins(0, 0, 0, 0)
        self._zone_grid.setSpacing(12)
        self.preview = QScrollArea()
        self.preview.setObjectName("camera-diagnostic-preview")
        self.preview.setWidgetResizable(True)
        self.preview.setFrameShape(QFrame.Shape.NoFrame)
        self.preview.setWidget(self._zone_host)
        self.preview.setMinimumSize(520, 220)
        self.preview.setStyleSheet(f"background: {COLORS.background};")
        self.metrics = QLabel(self._tr("camera.diagnostic.idle"))
        self.metrics.setObjectName("camera-diagnostic-metrics")
        self.metrics.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.metrics.setWordWrap(True)
        self.metrics.setMinimumWidth(280)
        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.metrics.setFont(font)
        self.start_button = QPushButton(self._tr("camera.diagnostic.start"))
        self.start_button.setObjectName("camera-diagnostic-start")
        self.stop_button = QPushButton(self._tr("camera.diagnostic.stop"))
        self.stop_button.setObjectName("camera-diagnostic-stop")
        self.copy_button = QPushButton(self._tr("camera.diagnostic.copy"))
        self.copy_button.setObjectName("camera-diagnostic-copy")
        self.save_button = QPushButton(self._tr("camera.diagnostic.save"))
        self.save_button.setObjectName("camera-diagnostic-save")
        self.snapshot_button = QPushButton(self._tr("camera.diagnostic.snapshot"))
        self.snapshot_button.setObjectName("camera-diagnostic-snapshot")
        self.reference_lane = QSpinBox()
        self.reference_lane.setObjectName("camera-diagnostic-reference-lane")
        self.reference_lane.setRange(1, 8)
        self.reference_laps = QSpinBox()
        self.reference_laps.setObjectName("camera-diagnostic-reference-laps")
        self.reference_laps.setRange(0, 100_000)
        self.reference_button = QPushButton(self._tr("camera.diagnostic.reference_apply"))
        self.reference_button.setObjectName("camera-diagnostic-reference-apply")
        self.include_frames = QCheckBox(self._tr("camera.diagnostic.include_frames"))
        self.include_frames.setObjectName("camera-diagnostic-include-frames")
        set_role(self.start_button, "primary")
        set_role(self.stop_button, "secondary")
        set_role(self.copy_button, "ghost")
        set_role(self.save_button, "ghost")
        set_role(self.snapshot_button, "ghost")
        set_role(self.reference_button, "ghost")
        self.log = QPlainTextEdit()
        self.log.setObjectName("camera-diagnostic-log")
        self.log.setReadOnly(True)
        self.log.setFont(font)
        self.log.setMinimumHeight(200)
        self.log.setPlaceholderText(self._tr("camera.diagnostic.log"))
        self.status = QLabel()
        self.status.setObjectName("camera-diagnostic-status")
        self.status.setWordWrap(True)

        heading = QLabel(self._tr("camera.diagnostic.title"))
        set_role(heading, "section")
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel(self._tr("camera.diagnostic.view")))
        view_row.addWidget(self.view, 1)
        metrics_scroll = QScrollArea()
        metrics_scroll.setObjectName("camera-diagnostic-metrics-scroll")
        metrics_scroll.setWidget(self.metrics)
        metrics_scroll.setWidgetResizable(True)
        metrics_scroll.setFrameShape(QFrame.Shape.NoFrame)
        metrics_scroll.setMinimumWidth(300)
        metrics_scroll.setMaximumWidth(380)
        body = QHBoxLayout()
        body.addWidget(self.preview, 1)
        body.addWidget(metrics_scroll)
        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addStretch(1)
        exports = QHBoxLayout()
        exports.addWidget(self.copy_button)
        exports.addWidget(self.save_button)
        exports.addWidget(self.include_frames)
        exports.addWidget(self.snapshot_button)
        exports.addWidget(QLabel(self._tr("camera.diagnostic.reference_lane")))
        exports.addWidget(self.reference_lane)
        exports.addWidget(QLabel(self._tr("camera.diagnostic.reference_laps")))
        exports.addWidget(self.reference_laps)
        exports.addWidget(self.reference_button)
        exports.addStretch(1)
        root = QVBoxLayout(self)
        configure_page(root)
        root.addWidget(heading)
        root.addLayout(view_row)
        root.addLayout(body, 1)
        root.addLayout(controls)
        root.addWidget(QLabel(self._tr("camera.diagnostic.log")))
        root.addWidget(self.log, 1)
        root.addLayout(exports)
        root.addWidget(self.status)

        self.view.currentIndexChanged.connect(self._on_view)
        self.start_button.clicked.connect(self.start_diagnosis)
        self.stop_button.clicked.connect(self.stop_diagnosis)
        self.copy_button.clicked.connect(self.copy_log)
        self.save_button.clicked.connect(self.save_diagnosis)
        self.snapshot_button.clicked.connect(self.save_snapshot)
        self.reference_button.clicked.connect(self.apply_reference)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)

    def start_diagnosis(self) -> None:
        """Begin a new session on the race detector. Old log lines are dropped."""
        try:
            document = self._host.diagnostic_document()
        except Exception:
            self._set_status("camera.diagnostic.invalid", error=True)
            return
        settings = to_detector_settings(document)
        if settings is None:
            self._set_status("camera.diagnostic.no_zones", error=True)
            return
        self.stop_diagnosis()
        config = diagnostic_config(
            app_version=_app_version(),
            started_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            device_index=document.camera.device_index,
            requested_width=document.camera.width,
            requested_height=document.camera.height,
            requested_fps=document.camera.fps,
            settings=settings,
        )
        session = DiagnosticSession(settings, config, max_lines=MAX_LOG_LINES)
        session.start()
        for lane, laps in self._lane_references.items():
            session.set_reference_laps(lane, laps)
        self._session = session
        self._config = config
        self._host.attach_diagnostic(self._on_frame)
        self._set_status("camera.diagnostic.running", error=False)
        self.refresh()

    def stop_diagnosis(self) -> None:
        """Stop collecting. The log and the last picture stay on screen."""
        self._host.detach_diagnostic(self._on_frame)
        session = self._session
        if session is not None and session.running:
            session.stop()
            key = (
                "camera.diagnostic.stopped_truncated"
                if session.truncated()
                else "camera.diagnostic.stopped"
            )
            self._set_status(key, error=False)
        self.refresh()

    def shutdown(self) -> None:
        """Detach from the preview and stop the worker. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._host.detach_diagnostic(self._on_frame)
        session = self._session
        if session is not None:
            session.close()

    def copy_log(self) -> None:
        text = self.log_text()
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self._set_status("camera.diagnostic.copied", error=False)

    def apply_reference(self) -> None:
        """Remember a hand-counted lap total for one lane. Detection ignores it."""
        lane = self.reference_lane.value()
        laps = self.reference_laps.value()
        self._lane_references[lane] = laps
        session = self._session
        if session is not None:
            session.set_reference_laps(lane, laps)
        self._set_status("camera.diagnostic.reference_saved", error=False)

    def save_diagnosis(self, path: str | Path | None = None) -> tuple[Path, Path, Path] | None:
        """Write the summary, its JSON form and the event JSONL."""
        target = self._chosen_export(path, _export_name("txt"), "Text (*.txt)")
        if target is None:
            return None
        session = self._session
        if session is None:
            self._set_status("camera.diagnostic.reference_missing", error=True)
            return None
        try:
            written = session.write_export(target, include_frames=self.include_frames.isChecked())
        except (OSError, ValueError, TypeError) as error:
            self._show_export_error(session, error)
            return None
        self._set_status("camera.diagnostic.saved", error=False)
        return written

    def save_log(self, path: str | Path | None = None) -> Path | None:
        """Write the summary and the structured attempts. Frame rows are optional."""
        target = self._chosen_export(path, _export_name("txt"), "Text (*.txt)")
        if target is None:
            return None
        session = self._session
        if session is None:
            body = ""
        else:
            body = session.export_text(include_frames=self.include_frames.isChecked())
        try:
            target.write_text(body, encoding="utf-8")
        except OSError as error:
            if session is None:
                self.status.setText(
                    self._translator.format("camera.diagnostic.export_failed", detail=error)
                )
                set_tone(self.status, "error")
            else:
                self._show_export_error(session, error)
            return None
        self._set_status("camera.diagnostic.saved", error=False)
        return target

    def save_snapshot(self, path: str | Path | None = None) -> Path | None:
        """Write the current calculation view, including overlays, as a PNG."""
        image = self._image
        if image is None or image.isNull():
            self._set_status("camera.diagnostic.snapshot_missing", error=True)
            return None
        target = self._chosen_export(path, _export_name("png"), "PNG (*.png)")
        if target is None:
            return None
        if not image.save(str(target)):
            self._set_status("camera.diagnostic.snapshot_missing", error=True)
            return None
        self._set_status("camera.diagnostic.snapshot_saved", error=False)
        return target

    def _chosen_export(self, path: object, name: str, file_filter: str) -> Path | None:
        """Turn a caller path into ``Path``. A button click passes ``False``."""
        if isinstance(path, bool) or path is None:
            return self._ask_path(name, file_filter)
        if isinstance(path, Path):
            return path
        if isinstance(path, str):
            return Path(path)
        return self._ask_path(name, file_filter)

    def _show_export_error(self, session: DiagnosticSession, error: BaseException) -> None:
        """Keep the session. Point at the journal when it is already on disk."""
        journal = session.journal_path()
        if journal is not None and journal.is_file():
            message = self._translator.format(
                "camera.diagnostic.export_failed_kept",
                detail=error,
                journal=journal,
            )
        else:
            message = self._translator.format("camera.diagnostic.export_failed", detail=error)
        self.status.setText(message)
        set_tone(self.status, "error")

    def log_text(self) -> str:
        session = self._session
        if session is None:
            return ""
        return session.text()

    def refresh(self) -> None:
        """Read the latest immutable snapshot. This does not detect."""
        session = self._session
        if session is None:
            self.metrics.setText(self._tr("camera.diagnostic.idle"))
            return
        snapshot = session.snapshot()
        self._note_flash(snapshot)
        self.metrics.setText(self._metrics_text(snapshot))
        self._show_picture(snapshot)
        self._show_log(session.text())

    def _on_frame(self, frame: TimedFrame, capture: CaptureCounters | None) -> None:
        session = self._session
        if session is not None and not self._closed:
            session.submit(frame, capture)

    def _on_view(self, _index: int = 0) -> None:
        value = self.view.currentData()
        if isinstance(value, str):
            self._view = DiagnosticView(value)
        self.refresh()

    def _metrics_text(self, snapshot: DiagnosticSnapshot) -> str:
        config = self._config
        if config is None:
            return format_live(snapshot)
        preset = resolution_preset_name(config.block_size)
        name = preset if preset == "custom" else self._tr(f"camera.resolution.{preset}")
        return "\n".join(
            [
                f"Kamera: {config.device_index}",
                f"Angefordert: {config.requested_width} x {config.requested_height}",
                f"Angefordert FPS: {config.requested_fps}",
                self._translator.format("camera.diagnostic.resolution", name=name),
                self._translator.format("camera.diagnostic.block", size=config.block_size),
                f"Zonen: {len(config.zones)}",
                format_live(snapshot),
            ]
        )

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._reflow_cards()

    def _show_picture(self, snapshot: DiagnosticSnapshot) -> None:
        inspection = snapshot.inspection
        if inspection is None:
            return
        flashing = time_now() < self._flash_until
        self._ensure_cards(len(inspection.zones))
        fitted: list[tuple[str, QImage]] = []
        for index, zone in enumerate(inspection.zones):
            card = self._cards[index]
            card.setVisible(True)
            caption = self._translator.format(
                "camera.diagnostic.zone_caption",
                number=index + 1,
                lane=zone.lane,
                position=zone.position_id,
            )
            card.caption.setText(caption)
            picture = render_zone(zone, self._view, scale=max(1, _zone_scale(zone)))
            active = flashing and (index + 1 in self._flash_zones or zone.accepted)
            image = _fit_zone(picture, card.image.size(), active=active)
            card.image.setPixmap(QPixmap.fromImage(image))
            card.meta.setText(self._zone_meta(zone))
            fitted.append((caption, image))
        for card in self._cards[len(inspection.zones) :]:
            card.setVisible(False)
        self._reflow_cards()
        self._image = _compose_zones(fitted)

    def _ensure_cards(self, count: int) -> None:
        while len(self._cards) < count:
            card = _ZoneCard()
            self._cards.append(card)
            self._zone_grid.addWidget(card, 0, len(self._cards) - 1)

    def _reflow_cards(self) -> None:
        visible = [card for card in self._cards if card.isVisible()]
        if not visible:
            return
        width = max(self.preview.viewport().width(), 1)
        columns = 1 if len(visible) == 1 else max(1, min(len(visible), width // 280))
        for card in visible:
            self._zone_grid.removeWidget(card)
        for index, card in enumerate(visible):
            self._zone_grid.addWidget(card, index // columns, index % columns)

    def _zone_meta(self, zone: ZoneInspection) -> str:
        rows = len(zone.analysis)
        cols = len(zone.analysis[0]) if rows else 0
        if rows < 1 or cols < 1:
            return ""
        assessed = evaluate_detection_zone(
            cols * zone.block_size,
            rows * zone.block_size,
            zone.block_size,
            zone.direction,
        )
        rating = self._tr(f"camera.diagnostic.quality.{assessed.rating.value}")
        lines = [
            self._translator.format("camera.diagnostic.grid", cols=cols, rows=rows),
            self._translator.format("camera.diagnostic.quality", rating=rating),
        ]
        if zone.reason == "too_short":
            samples = max(zone.sample_count, 1)
            if samples <= 1 and zone.samples_expired > 0:
                key = "camera.diagnostic.direction_window_expired"
            elif samples <= 1:
                key = "camera.diagnostic.single_sample_pending"
            else:
                key = "camera.diagnostic.too_few_samples_many"
            lines.append(self._translator.format(key, samples=samples))
        return "\n".join(lines)

    def _show_log(self, text: str) -> None:
        lines = text.splitlines()
        visible = "\n".join(lines[-_LOG_TAIL:])
        if self.log.toPlainText() != visible:
            self.log.setPlainText(visible)
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _note_flash(self, snapshot: DiagnosticSnapshot) -> None:
        fired = tuple(
            index
            for index, zone in enumerate(
                () if snapshot.inspection is None else snapshot.inspection.zones, start=1
            )
            if zone.accepted
        )
        if fired:
            self._flash_zones = fired
            self._flash_until = time_now() + _FLASH_S

    def _ask_path(self, name: str, file_filter: str) -> Path | None:
        chosen, _selected = QFileDialog.getSaveFileName(self, self.windowTitle(), name, file_filter)
        if chosen == "":
            return None
        return Path(chosen)

    def _set_status(self, key: str, *, error: bool) -> None:
        self.status.setText(self._tr(key))
        set_tone(self.status, "error" if error else "ok")

    def _tr(self, key: str) -> str:
        return self._translator.translate(key)


def time_now() -> float:
    return time.monotonic()


def _app_version() -> str:
    try:
        return version("slot-racing-platform")
    except PackageNotFoundError:
        return "unknown"


def _export_name(suffix: str) -> str:
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    return f"camera-diagnostic-{stamp}.{suffix}"


class _ZoneCard(QWidget):
    """Caption above one zone preview. The caption is not painted into the pixels."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.caption = QLabel()
        self.caption.setObjectName("camera-diagnostic-zone-caption")
        self.caption.setWordWrap(True)
        self.caption.setMinimumWidth(260)
        self.image = QLabel()
        self.image.setObjectName("camera-diagnostic-zone-image")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setFixedSize(300, 170)
        self.image.setStyleSheet(f"background: {COLORS.background};")
        self.meta = QLabel()
        self.meta.setObjectName("camera-diagnostic-zone-meta")
        self.meta.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.caption)
        layout.addWidget(self.image)
        layout.addWidget(self.meta)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)


def _zone_scale(zone: ZoneInspection) -> int:
    rows = len(zone.analysis)
    cols = len(zone.analysis[0]) if rows else 1
    longest = max(rows, cols, 1)
    return max(1, min(12, 160 // longest))


def _fit_zone(picture: DiagnosticPicture, target: QSize, *, active: bool) -> QImage:
    """Scale one zone into ``target`` without cropping or stretching it."""
    source = QImage(
        picture.pixels,
        picture.width,
        picture.height,
        picture.width,
        QImage.Format.Format_Grayscale8,
    ).copy()
    color = source.convertToFormat(QImage.Format.Format_RGB32)
    canvas = QImage(target, QImage.Format.Format_RGB32)
    canvas.fill(QColor(COLORS.background))
    fitted = color.scaled(
        target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    painter = QPainter(canvas)
    left = (target.width() - fitted.width()) // 2
    top = (target.height() - fitted.height()) // 2
    painter.drawImage(left, top, fitted)
    tone = QColor(COLORS.accent if active else COLORS.info)
    painter.setPen(QPen(tone, 3 if active else 2))
    painter.drawRect(left, top, max(1, fitted.width() - 1), max(1, fitted.height() - 1))
    painter.end()
    return canvas


def _compose_zones(zones: list[tuple[str, QImage]]) -> QImage:
    """Side-by-side snapshot. Captions sit above each preview and can wrap."""
    if not zones:
        image = QImage(1, 1, QImage.Format.Format_RGB32)
        image.fill(QColor(COLORS.background))
        return image
    columns = 1 if len(zones) == 1 else 2
    card_width = 320
    caption_height = 48
    gap = 16
    rows = (len(zones) + columns - 1) // columns
    sample = zones[0][1]
    row_height = caption_height + sample.height() + 8
    width = columns * card_width + (columns + 1) * gap
    height = rows * row_height + (rows + 1) * gap
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(COLORS.background))
    painter = QPainter(image)
    font = QFont()
    font.setPixelSize(14)
    painter.setFont(font)
    painter.setPen(QColor(COLORS.text))
    for index, (caption, preview) in enumerate(zones):
        column = index % columns
        row = index // columns
        x = gap + column * (card_width + gap)
        y = gap + row * (row_height + gap)
        painter.drawText(
            x,
            y,
            card_width,
            caption_height,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            caption,
        )
        painter.drawImage(x, y + caption_height, preview)
    painter.end()
    return image
