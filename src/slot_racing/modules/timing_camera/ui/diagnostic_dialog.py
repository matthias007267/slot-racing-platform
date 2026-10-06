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
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.i18n import Translator
from slot_racing.modules.timing_camera.configuration import (
    CameraConfiguration,
    to_detector_settings,
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
    render_picture,
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
        self.resize(980, 720)
        self._translator = translator
        self._host = host
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
        self.preview = QLabel(self._tr("camera.diagnostic.preview"))
        self.preview.setObjectName("camera-diagnostic-preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 180)
        self.preview.setStyleSheet(f"background: {COLORS.background}; color: {COLORS.text_muted};")
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
        set_role(self.start_button, "primary")
        set_role(self.stop_button, "secondary")
        set_role(self.copy_button, "ghost")
        set_role(self.save_button, "ghost")
        set_role(self.snapshot_button, "ghost")
        self.log = QPlainTextEdit()
        self.log.setObjectName("camera-diagnostic-log")
        self.log.setReadOnly(True)
        self.log.setFont(font)
        self.log.setMinimumHeight(200)
        self.log.setPlaceholderText(self._tr("camera.diagnostic.log"))
        self.status = QLabel()
        self.status.setObjectName("camera-diagnostic-status")

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
        exports.addWidget(self.snapshot_button)
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
        self.save_button.clicked.connect(self.save_log)
        self.snapshot_button.clicked.connect(self.save_snapshot)

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
            self._set_status("camera.diagnostic.stopped", error=False)
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

    def save_log(self, path: Path | None = None) -> Path | None:
        """Write the full diagnostic text as UTF-8. ``path`` skips the file dialog."""
        target = path if path is not None else self._ask_path(_export_name("txt"), "Text (*.txt)")
        if target is None:
            return None
        target.write_text(self.log_text(), encoding="utf-8")
        self._set_status("camera.diagnostic.saved", error=False)
        return target

    def save_snapshot(self, path: Path | None = None) -> Path | None:
        """Write the current calculation view, including overlays, as a PNG."""
        image = self._image
        if image is None or image.isNull():
            self._set_status("camera.diagnostic.snapshot_missing", error=True)
            return None
        target = path if path is not None else self._ask_path(_export_name("png"), "PNG (*.png)")
        if target is None:
            return None
        if not image.save(str(target)):
            self._set_status("camera.diagnostic.snapshot_missing", error=True)
            return None
        self._set_status("camera.diagnostic.snapshot_saved", error=False)
        return target

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
        return "\n".join(
            [
                f"Kamera: {config.device_index}",
                f"Angefordert: {config.requested_width} x {config.requested_height}",
                f"Angefordert FPS: {config.requested_fps}",
                f"Block: {config.block_size}",
                f"Zonen: {len(config.zones)}",
                format_live(snapshot),
            ]
        )

    def _show_picture(self, snapshot: DiagnosticSnapshot) -> None:
        inspection = snapshot.inspection
        if inspection is None:
            return
        picture = render_picture(inspection, self._view, scale=1)
        image = _display_image(
            picture,
            self.preview.size(),
            self._flash_zones,
            time_now() < self._flash_until,
        )
        self._image = image
        self.preview.setPixmap(QPixmap.fromImage(image))

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


def _display_image(
    picture: DiagnosticPicture,
    target: QSize,
    flash_zones: tuple[int, ...],
    flashing: bool,
) -> QImage:
    """Enlarge blocks with hard edges, then draw overlays in display pixels.

    The overlay is painted after scaling, so it is not a block value and it is
    not fed back into detection.
    """
    source = QImage(
        picture.pixels,
        picture.width,
        picture.height,
        picture.width,
        QImage.Format.Format_Grayscale8,
    )
    color = source.copy().convertToFormat(QImage.Format.Format_RGB32)
    fitted = color
    if target.width() > picture.width and target.height() > picture.height:
        fitted = color.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
    scale_x = fitted.width() / picture.width
    scale_y = fitted.height() / picture.height
    painter = QPainter(fitted)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    font = QFont()
    font.setPixelSize(16)
    painter.setFont(font)
    for zone in picture.zones:
        active = flashing and (zone.number in flash_zones or zone.accepted)
        left = round(zone.x * scale_x)
        top = round(zone.y * scale_y)
        width = max(1, round(zone.width * scale_x) - 1)
        height = max(1, round(zone.height * scale_y) - 1)
        tone = QColor(COLORS.accent if active else COLORS.info)
        painter.setPen(QPen(tone, 3 if active else 2))
        painter.drawRect(left, top, width, height)
        painter.setPen(QColor(COLORS.text))
        painter.drawText(left + 8, top + 22, f"Z{zone.number} L{zone.lane} {zone.direction}")
        if active:
            painter.setPen(QColor(COLORS.accent))
            painter.drawText(left + 8, top + 44, f"ZONE {zone.number} - DETECTION")
    painter.end()
    return fitted
