"""Test mode: trigger simulated sensor events and see what the timing source delivers."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Signal
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from carrera.core.clock import Clock
from carrera.core.domain import TimingSetup, TrackId
from carrera.core.i18n import Translator
from carrera.core.timing import TimingSourceFactory
from carrera.modules.tracks.timing_test import TimingTestSession
from carrera.modules.tracks.ui.common import format_time, position_text, run_guarded
from carrera.uikit import StatusLabel, fill_table, heading, make_table


class TimingTestView(QWidget):
    """Shows simulated events only. All interpretation lives in :class:`TimingTestSession`."""

    back_requested = Signal()

    def __init__(
        self,
        translator: Translator,
        factories: Callable[[], Sequence[TimingSourceFactory]],
        clock: Clock,
        *,
        show_back: bool = True,
    ) -> None:
        super().__init__()
        self.translator = translator
        self._factories = factories
        self._clock = clock
        self.session: TimingTestSession | None = None
        tr = translator.translate

        self.table = make_table(
            [
                tr("timing.test.column.number"),
                tr("timing.test.column.time"),
                tr("timing.test.column.sensor"),
                tr("timing.test.column.position"),
            ],
            "timing-test-table",
        )
        self.status = StatusLabel("timing-test-status")
        self.count_label = QLabel()
        self.count_label.setObjectName("timing-test-count")
        self.last_position_label = QLabel()
        self.last_position_label.setObjectName("timing-test-last-position")
        self.last_sensor_label = QLabel()
        self.last_sensor_label.setObjectName("timing-test-last-sensor")
        self.last_time_label = QLabel()
        self.last_time_label.setObjectName("timing-test-last-time")
        self.order_label = QLabel()
        self.order_label.setObjectName("timing-test-order")
        self.order_label.setWordWrap(True)
        self.order_state_label = QLabel()
        self.order_state_label.setObjectName("timing-test-order-state")

        self.trigger_button = QPushButton(tr("timing.test.trigger"))
        self.trigger_button.setObjectName("timing-test-trigger")
        self.lap_button = QPushButton(tr("timing.test.trigger_lap"))
        self.lap_button.setObjectName("timing-test-lap")
        self.reset_button = QPushButton(tr("timing.test.reset"))
        self.reset_button.setObjectName("timing-test-reset")
        self.back_button = QPushButton(tr("timing.back"))
        self.back_button.setObjectName("timing-test-back")
        self.back_button.setVisible(show_back)

        buttons = QHBoxLayout()
        for button in (self.trigger_button, self.lap_button, self.reset_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.back_button)

        layout = QVBoxLayout(self)
        layout.addWidget(heading(tr("timing.test.title")))
        layout.addWidget(QLabel(tr("timing.test.hint")))
        layout.addLayout(buttons)
        layout.addWidget(self.table, 1)
        for label_key, label in (
            ("timing.test.count", self.count_label),
            ("timing.test.last_position", self.last_position_label),
            ("timing.test.last_sensor", self.last_sensor_label),
            ("timing.test.last_time", self.last_time_label),
            ("timing.test.detected_order", self.order_label),
            ("timing.test.order_state", self.order_state_label),
        ):
            row = QHBoxLayout()
            caption = QLabel(tr(label_key))
            caption.setMinimumWidth(180)
            row.addWidget(caption)
            row.addWidget(label, 1)
            layout.addLayout(row)
        layout.addWidget(self.status)

        self.trigger_button.clicked.connect(lambda: self.trigger())
        self.lap_button.clicked.connect(lambda: self.trigger_lap())
        self.reset_button.clicked.connect(lambda: self.reset())
        self.back_button.clicked.connect(lambda: self.back_requested.emit())
        self._refresh()

    def open_setup(self, setup: TimingSetup, track_id: TrackId | None = None) -> None:
        """Start a fresh test for ``setup``."""
        if self.session is not None:
            self.session.stop()
        self.session = TimingTestSession(setup, self._factories, self._clock, track_id)
        self.status.clear_message()
        self._refresh()

    def trigger(self) -> None:
        self._run(lambda session: session.trigger())

    def trigger_lap(self) -> None:
        self._run(lambda session: session.trigger_lap())

    def reset(self) -> None:
        self._run(lambda session: session.reset())

    def stop(self) -> None:
        if self.session is not None:
            self.session.stop()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self.stop()
        super().hideEvent(event)

    def _run(self, action: Callable[[TimingTestSession], None]) -> None:
        session = self.session
        if session is None:
            return
        self.status.clear_message()
        run_guarded(self.translator, self.status, self, lambda: action(session))
        self._refresh()

    def _refresh(self) -> None:
        tr = self.translator.translate
        session = self.session
        has_session = session is not None
        for button in (self.trigger_button, self.lap_button, self.reset_button):
            button.setEnabled(has_session)
        if session is None:
            fill_table(self.table, [])
            for label in (
                self.count_label,
                self.last_position_label,
                self.last_sensor_label,
                self.last_time_label,
                self.order_label,
                self.order_state_label,
            ):
                label.setText("")
            return
        layout = session.setup.layout

        def title(position_id: str) -> str:
            position = layout.position(position_id)
            return position_text(
                self.translator, position.type, position.sector_number or 0, position.name
            )

        fill_table(
            self.table,
            [
                (str(e.number), format_time(e.time_ns), e.sensor_id, title(e.position_id))
                for e in session.events
            ],
            keep_selection=False,
        )
        self.table.scrollToBottom()
        last = session.last
        self.count_label.setText(str(session.count))
        self.last_position_label.setText(title(last.position_id) if last else "-")
        self.last_sensor_label.setText(last.sensor_id if last else "-")
        self.last_time_label.setText(format_time(last.time_ns) if last else "-")
        self.order_label.setText(
            " → ".join(title(p) for p in session.detected_order) if session.events else "-"
        )
        self.order_state_label.setText(
            tr("timing.test.order_ok" if session.in_order else "timing.test.order_wrong")
        )
