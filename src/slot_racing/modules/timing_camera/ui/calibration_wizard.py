"""Calibration assistant on top of the existing camera preview.

Recording and analysis stay off the widgets. The assistant only collects the
zone, the lap count and the polygon, then shows what the analysis measured.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.i18n import Translator
from slot_racing.modules.timing_camera.calibration import (
    LAP_DEFAULT,
    LAP_MAXIMUM,
    LAP_MINIMUM,
    LAP_STEP,
    SAFETY_MARGIN,
    CalibrationAttempt,
    CalibrationProposal,
)
from slot_racing.modules.timing_camera.calibration_analysis import (
    CalibrationCancelledError,
    CalibrationReport,
    analyze_recording,
)
from slot_racing.modules.timing_camera.calibration_polygon import CalibrationPolygon
from slot_racing.modules.timing_camera.calibration_recording import (
    RecordingError,
    RecordingOverflowError,
)
from slot_racing.modules.timing_camera.configuration import NormalizedRoi
from slot_racing.modules.timing_camera.detection import TravelDirection
from slot_racing.modules.timing_camera.frame_source import TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.ui.polygon_stage import PolygonStage
from slot_racing.uikit.theme import configure_page, set_role

Poll = Callable[[], TimedFrame | None]


class _AnalysisThread(QThread):
    """Runs the search away from the window. The tape is only read here."""

    progressed = Signal(int, int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, attempt_tape: object, arguments: dict[str, object]) -> None:
        super().__init__()
        self._tape = attempt_tape
        self._arguments = arguments
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            report = analyze_recording(
                self._tape,  # type: ignore[arg-type]
                progress=self.progressed.emit,
                cancelled=lambda: self._cancelled,
                **self._arguments,  # type: ignore[arg-type]
            )
        except CalibrationCancelledError:
            self.failed.emit("cancelled")
        except Exception as error:
            self.failed.emit(type(error).__name__)
            return
        self.succeeded.emit(report)


class CalibrationWizard(QDialog):
    """Six steps, one zone: choose, draw, record, watch, analyse, accept or discard."""

    def __init__(
        self,
        translator: Translator,
        zones: Sequence[tuple[str, int, NormalizedRoi, bool]],
        polygons: Mapping[tuple[str, int], CalibrationPolygon],
        *,
        direction: TravelDirection,
        sensitivity: int,
        block_size: int,
        saved_width: int,
        saved_height: int,
        poll: Poll | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("calibration-wizard")
        self.setWindowTitle(translator.translate("calibration.title"))
        self.resize(980, 700)
        self._translator = translator
        self._zones = list(zones)
        self._polygons = dict(polygons)
        self._direction = direction
        self._sensitivity = sensitivity
        self._block_size = block_size
        self._saved_size = (saved_width, saved_height)
        self._poll = poll
        self._attempt: CalibrationAttempt | None = None
        self._worker: _AnalysisThread | None = None
        self._report: CalibrationReport | None = None
        self._proposal: CalibrationProposal | None = None
        self._last_sequence = -1
        self._last_preview = 0.0
        self._camera_lost = False
        self.stage = PolygonStage()
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._tick)
        self._show_setup()

    @property
    def proposal(self) -> CalibrationProposal | None:
        return self._proposal

    def feed(self, frame: GrayFrame, timestamp_ns: int, sequence: int) -> None:
        """Accept one frame. Tests use this instead of a live camera."""
        self._accept(TimedFrame(frame, timestamp_ns, sequence=sequence))

    def _build(self) -> None:
        self._stack = QStackedWidget()
        self._stack.addWidget(self._setup_page())
        self._stack.addWidget(self._polygon_page())
        self._stack.addWidget(self._drive_page())
        self._stack.addWidget(self._progress_page())
        self._stack.addWidget(self._result_page())
        root = QVBoxLayout(self)
        configure_page(root)
        # One preview for every step. A widget can only live in one layout.
        root.addWidget(self.stage, 1)
        root.addWidget(self._stack, 1)
        self.stage.hide()

    def _setup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.zone_combo = QComboBox()
        self.zone_combo.setObjectName("calibration-zone")
        for position_id, lane, _roi, _check in self._zones:
            self.zone_combo.addItem(f"{position_id} · Bahn {lane}", (position_id, lane))
        self.laps = QSpinBox()
        self.laps.setObjectName("calibration-laps")
        self.laps.setRange(LAP_MINIMUM, LAP_MAXIMUM)
        self.laps.setSingleStep(LAP_STEP)
        self.laps.setValue(LAP_DEFAULT)
        self.laps.valueChanged.connect(self._snap_laps)
        self._next = QPushButton(self._tr("calibration.next"))
        self._next.setObjectName("calibration-next")
        set_role(self._next, "primary")
        self._next.clicked.connect(self._show_polygon)
        cancel = self._cancel_button()
        layout.addWidget(QLabel(self._tr("calibration.zone")))
        layout.addWidget(self.zone_combo)
        layout.addWidget(QLabel(self._tr("calibration.laps")))
        layout.addWidget(self.laps)
        layout.addStretch(1)
        layout.addLayout(self._row(cancel, self._next))
        self._next.setEnabled(bool(self._zones))
        return page

    def _polygon_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._polygon_hint = QLabel(self._tr("calibration.polygon_hint"))
        self._polygon_hint.setWordWrap(True)
        self._polygon_problem = QLabel()
        self._polygon_problem.setObjectName("calibration-polygon-problem")
        self._reset = QPushButton(self._tr("calibration.reset"))
        self._reset.setObjectName("calibration-reset")
        self._reset.clicked.connect(self.stage.reset)
        self.stage.changed.connect(self._refresh_polygon)
        self._start = QPushButton(self._tr("calibration.start"))
        self._start.setObjectName("calibration-start")
        set_role(self._start, "primary")
        self._start.clicked.connect(self._begin)
        back = QPushButton(self._tr("calibration.back"))
        back.setObjectName("calibration-back")
        back.clicked.connect(self._show_setup)
        layout.addWidget(self._polygon_hint)
        layout.addWidget(self._polygon_problem)
        layout.addWidget(self._reset)
        layout.addLayout(self._row(back, self._cancel_button(), self._start))
        return page

    def _drive_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._prompt = QLabel()
        self._prompt.setObjectName("calibration-prompt")
        self._prompt.setWordWrap(True)
        self._frames = QLabel()
        self._frames.setObjectName("calibration-frames")
        self._dropped = QLabel()
        self._dropped.setObjectName("calibration-dropped")
        self._fps = QLabel()
        self._fps.setObjectName("calibration-fps")
        self._resolution = QLabel()
        self._resolution.setObjectName("calibration-resolution")
        self._drive_error = QLabel()
        self._drive_error.setObjectName("calibration-error")
        self._drive_error.setWordWrap(True)
        done = QPushButton(self._tr("calibration.done"))
        done.setObjectName("calibration-done")
        set_role(done, "primary")
        done.clicked.connect(self._finish)
        restart = QPushButton(self._tr("calibration.restart"))
        restart.setObjectName("calibration-restart")
        restart.clicked.connect(self._restart)
        layout.addWidget(self._prompt)
        layout.addWidget(self._frames)
        layout.addWidget(self._dropped)
        layout.addWidget(self._fps)
        layout.addWidget(self._resolution)
        layout.addWidget(self._drive_error)
        layout.addLayout(self._row(self._cancel_button(), restart, done))
        return page

    def _progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel(self._tr("calibration.progress"))
        label.setObjectName("calibration-progress-label")
        self._progress = QProgressBar()
        self._progress.setObjectName("calibration-progress")
        self._progress.setRange(0, 100)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addWidget(self._progress)
        layout.addStretch(1)
        layout.addWidget(self._cancel_button())
        return page

    def _result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._result = QLabel()
        self._result.setObjectName("calibration-result")
        self._result.setWordWrap(True)
        self._result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._shared = QCheckBox(self._tr("calibration.shared_apply"))
        self._shared.setObjectName("calibration-shared")
        self._shared.setChecked(len(self._zones) <= 1)
        apply = QPushButton(self._tr("calibration.apply"))
        apply.setObjectName("calibration-apply")
        set_role(apply, "primary")
        apply.clicked.connect(self._apply)
        self._apply_button = apply
        again = QPushButton(self._tr("calibration.again"))
        again.setObjectName("calibration-again")
        again.clicked.connect(self._again)
        layout.addWidget(self._result)
        layout.addWidget(self._shared)
        layout.addLayout(self._row(self._cancel_button(), again, apply))
        return page

    def _show_setup(self) -> None:
        self._set_page(0)

    def _show_polygon(self) -> None:
        key = self._selected_key()
        saved = None if key is None else self._polygons.get(key)
        self.stage.set_polygon(saved)
        self.stage.set_zones(self._selected_roi(), None)
        self._refresh_polygon()
        self._set_page(1)
        self._timer.start()

    def _set_page(self, index: int) -> None:
        """Show the live picture only while drawing, recording or reviewing."""
        self.stage.setVisible(index in (1, 2, 4))
        self._stack.setCurrentIndex(index)

    def _snap_laps(self, value: int) -> None:
        snapped = round(value / LAP_STEP) * LAP_STEP
        snapped = min(LAP_MAXIMUM, max(LAP_MINIMUM, snapped))
        if snapped != value:
            self.laps.setValue(snapped)

    def _begin(self) -> None:
        polygon = self.stage.polygon()
        if not polygon.is_valid or self._selected_key() is None:
            return
        if self._poll is None:
            self._polygon_problem.setText(self._tr("calibration.no_camera"))
            return
        self._release_attempt()
        self._attempt = CalibrationAttempt(polygon)
        try:
            self._attempt.start()
        except RecordingError:
            self._polygon_problem.setText(self._tr("calibration.disk"))
            self._attempt = None
            return
        self._camera_lost = False
        self._last_sequence = -1
        self._drive_error.setText("")
        self._prompt.setText(
            self._translator.format(
                "calibration.prompt",
                laps=self.laps.value(),
                lane=self._selected_lane(),
            )
        )
        self._refresh_stats()
        self._set_page(2)
        self._timer.start()

    def _restart(self) -> None:
        attempt = self._attempt
        if attempt is None:
            return
        attempt.restart()
        self._camera_lost = False
        self._last_sequence = -1
        self._drive_error.setText("")
        self._refresh_stats()

    def _finish(self) -> None:
        attempt = self._attempt
        if attempt is None or attempt.failed:
            return
        tape = attempt.finish()
        key = self._selected_key()
        if key is None:
            return
        position_id, lane = key
        others = tuple(
            roi
            for other_position, other_lane, roi, _check in self._zones
            if (other_position, other_lane) != key
        )
        arguments: dict[str, object] = {
            "polygon": attempt.polygon,
            "position_id": position_id,
            "lane": lane,
            "other_rois": others,
            "direction": self._direction,
            "check_direction": self._selected_check(),
            "block_size": self._block_size,
            "target_laps": self.laps.value(),
            "saved_width": self._saved_size[0],
            "saved_height": self._saved_size[1],
            "margin": SAFETY_MARGIN,
        }
        self._progress.setValue(0)
        self._timer.stop()
        self._set_page(3)
        worker = _AnalysisThread(tape, arguments)
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._on_report)
        worker.failed.connect(self._on_analysis_failed)
        self._worker = worker
        worker.start()

    def _again(self) -> None:
        self._stop_worker()
        self._report = None
        self._proposal = None
        self._begin()

    def _apply(self) -> None:
        report = self._report
        key = self._selected_key()
        if report is None or key is None or not report.applicable or report.roi is None:
            return
        position_id, lane = key
        self._timer.stop()
        self._proposal = CalibrationProposal(
            position_id=position_id,
            lane=lane,
            roi=report.roi,
            check_direction=report.check_direction,
            sensitivity=report.sensitivity,
            block_size=report.block_size,
            direction=report.direction,
            polygon=self.stage.polygon(),
            apply_shared=self._shared.isChecked(),
        )
        self.accept()

    def reject(self) -> None:
        self._stop_worker()
        self._release_attempt()
        self._timer.stop()
        super().reject()

    def _tick(self) -> None:
        if self._poll is None or self._stack.currentIndex() not in (1, 2):
            return
        try:
            delivered = self._poll()
        except Exception:
            self._lost()
            return
        if delivered is None:
            return
        self._accept(delivered)

    def _accept(self, delivered: TimedFrame) -> None:
        now = time.monotonic()
        show = now - self._last_preview >= 0.1 or self._last_preview == 0
        if show:
            self.stage.set_frame(delivered.frame)
            self._last_preview = now
        attempt = self._attempt
        if attempt is None or not attempt.recording:
            return
        if delivered.sequence and delivered.sequence == self._last_sequence:
            return
        self._last_sequence = delivered.sequence
        try:
            attempt.note_frame(delivered.frame, delivered.timestamp_ns, delivered.sequence)
        except RecordingOverflowError:
            self._drive_error.setText(self._tr("calibration.overflow"))
            self._refresh_stats()
            return
        except RecordingError as error:
            key = (
                "calibration.resolution_changed"
                if error.code == "resolution_changed"
                else "calibration.disk"
            )
            self._drive_error.setText(self._tr(key))
            self._refresh_stats()
            return
        if show:
            self._refresh_stats()

    def _lost(self) -> None:
        if self._camera_lost:
            return
        self._camera_lost = True
        if self._attempt is not None:
            self._attempt.note_error("camera_lost")
        self._drive_error.setText(self._tr("calibration.camera_lost"))

    def _refresh_polygon(self) -> None:
        problem = self.stage.polygon().problem
        self._start.setEnabled(problem is None and bool(self._zones))
        text = "" if problem is None else self._tr(f"calibration.polygon.{problem}")
        self._polygon_problem.setText(text)

    def _refresh_stats(self) -> None:
        stats = None if self._attempt is None else self._attempt.stats()
        frames = 0 if stats is None else stats.frames
        dropped = 0 if stats is None else stats.dropped
        fps = "-" if stats is None or stats.fps is None else f"{stats.fps:.1f}"
        resolution = "-" if stats is None or not stats.width else f"{stats.width} x {stats.height}"
        self._frames.setText(self._translator.format("calibration.frames", count=frames))
        self._dropped.setText(self._translator.format("calibration.dropped", count=dropped))
        self._fps.setText(self._translator.format("calibration.fps", fps=fps))
        self._resolution.setText(
            self._translator.format("calibration.resolution", resolution=resolution)
        )

    def _on_progress(self, done: int, planned: int) -> None:
        if planned <= 0:
            return
        self._progress.setValue(min(100, round(100 * done / planned)))

    def _on_report(self, report: object) -> None:
        if not isinstance(report, CalibrationReport):
            self._on_analysis_failed("analysis")
            return
        self._report = report
        self._progress.setValue(100)
        self._release_attempt()
        self._show_result(report)

    def _on_analysis_failed(self, code: str) -> None:
        if code == "cancelled":
            return
        self._drive_error.setText(self._tr("calibration.analysis_failed"))
        self._set_page(2)
        self._timer.start()

    def _show_result(self, report: CalibrationReport) -> None:
        self.stage.set_zones(self._selected_roi(), report.roi)
        rating = self._tr(f"calibration.rating.{report.rating}")
        zone = "-"
        if report.roi is not None:
            zone = self._translator.format(
                "calibration.result.zone",
                x=f"{report.roi.x:.3f}",
                y=f"{report.roi.y:.3f}",
                width=f"{report.roi.width:.3f}",
                height=f"{report.roi.height:.3f}",
            )
        fps = "-" if report.camera_fps is None else f"{report.camera_fps:.1f}"
        reasons = "\n".join(self._tr(f"calibration.reason.{code}") for code in report.reasons)
        self._result.setText(
            "\n".join(
                (
                    self._translator.format(
                        "calibration.result.title", zone=self.zone_combo.currentText()
                    ),
                    self._translator.format("calibration.result.target", count=report.target_laps),
                    self._translator.format("calibration.result.detected", count=report.detected),
                    self._translator.format("calibration.result.missed", count=report.missed),
                    self._translator.format("calibration.result.ghosts", count=report.ghosts),
                    self._translator.format(
                        "calibration.result.sensitivity", value=report.sensitivity
                    ),
                    zone,
                    self._translator.format("calibration.fps", fps=fps),
                    self._translator.format("calibration.result.rating", rating=rating),
                    reasons,
                )
            )
        )
        self._apply_button.setEnabled(report.applicable and report.roi is not None)
        self._set_page(4)

    def _selected_key(self) -> tuple[str, int] | None:
        data = self.zone_combo.currentData()
        if not isinstance(data, tuple) or len(data) != 2:
            return None
        position_id, lane = data
        if not isinstance(position_id, str) or not isinstance(lane, int):
            return None
        return position_id, lane

    def _selected_lane(self) -> int:
        key = self._selected_key()
        return 1 if key is None else key[1]

    def _selected_roi(self) -> NormalizedRoi | None:
        key = self._selected_key()
        if key is None:
            return None
        for position_id, lane, roi, _check in self._zones:
            if (position_id, lane) == key:
                return roi
        return None

    def _selected_check(self) -> bool:
        key = self._selected_key()
        if key is None:
            return True
        for position_id, lane, _roi, check in self._zones:
            if (position_id, lane) == key:
                return check
        return True

    def _stop_worker(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        worker.cancel()
        worker.wait(20000)

    def _release_attempt(self) -> None:
        attempt = self._attempt
        self._attempt = None
        if attempt is not None:
            attempt.cancel()

    def _cancel_button(self) -> QPushButton:
        button = QPushButton(self._tr("calibration.cancel"))
        button.setObjectName("calibration-cancel")
        set_role(button, "ghost")
        button.clicked.connect(self.reject)
        return button

    def _row(self, *widgets: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        for widget in widgets:
            row.addWidget(widget)
        return row

    def _tr(self, key: str) -> str:
        return self._translator.translate(key, key)
