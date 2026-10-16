"""Diagnostic page. It shares the sensor link and does not start a race."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import TrackCatalog
from slot_racing.core.domain import TimingPositionType, TimingSetup, TrackId, default_timing_setup
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.core.timing import TimingSetupService
from slot_racing.modules.timing_sensor.clock_map import DeviceClockMapping, SyncStatus
from slot_racing.modules.timing_sensor.configuration import SensorAssignment, assignment_issues
from slot_racing.modules.timing_sensor.intake import SensorObservation
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.protocol import BAUD_RATE
from slot_racing.modules.timing_sensor.store import (
    SensorConfigurationError,
    SensorConfigurationStore,
)
from slot_racing.modules.timing_sensor.transport import (
    SerialSensorTransport,
    SimulatedSensorTransport,
    list_serial_ports,
)
from slot_racing.uikit import describe_error, make_table
from slot_racing.uikit.theme import configure_page
from slot_racing.uikit.widgets import StatusLabel, fill_table


class SensorTimingPage(QWidget):
    """Simulation status, the track's sensor map, and the lines the shared link has seen."""

    def __init__(
        self,
        translator: Translator,
        link: SensorLink,
        store: SensorConfigurationStore,
        setups: TimingSetupService | None,
        tracks: TrackCatalog | None,
    ) -> None:
        super().__init__()
        self.setObjectName("sensor-page")
        self._translator = translator
        self._link = link
        self._store = store
        self._setups = setups
        self._tracks = tracks
        self._rows: list[SensorAssignment] = []
        self._holding = False
        self._shown_observations = 0
        tr = translator.translate
        self.mode_label = QLabel(tr("sensor.mode.simulation"))
        self.mode_label.setObjectName("sensor-mode")
        self.transport_combo = QComboBox()
        self.transport_combo.setObjectName("sensor-transport")
        self.transport_combo.addItem(tr("sensor.mode.simulation"), TransportMode.SIMULATION.value)
        self.transport_combo.addItem(tr("sensor.mode.serial"), TransportMode.SERIAL.value)
        self.port_combo = QComboBox()
        self.port_combo.setObjectName("sensor-port")
        self.port_combo.setEditable(True)
        self.baud_label = QLabel(str(BAUD_RATE))
        self.baud_label.setObjectName("sensor-baud")
        self.identity_label = QLabel(tr("sensor.identity.none"))
        self.identity_label.setObjectName("sensor-identity")
        self.quality_label = QLabel(tr("sensor.quality.none"))
        self.quality_label.setObjectName("sensor-quality")
        ports = QPushButton(tr("sensor.action.ports"))
        ports.setObjectName("sensor-ports")
        ports.clicked.connect(self._refresh_ports)
        connect = QPushButton(tr("sensor.action.connect"))
        connect.setObjectName("sensor-connect")
        connect.clicked.connect(self._connect)
        sync = QPushButton(tr("sensor.action.sync"))
        sync.setObjectName("sensor-sync-now")
        sync.clicked.connect(self._request_sync)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.refresh)
        self.connection_label = QLabel("")
        self.connection_label.setObjectName("sensor-connection")
        self.sync_label = QLabel("")
        self.sync_label.setObjectName("sensor-sync")
        self.readiness_label = QLabel("")
        self.readiness_label.setObjectName("sensor-readiness")
        self.protocol_label = QLabel(tr("sensor.protocol.note"))
        self.protocol_label.setObjectName("sensor-protocol")
        self.protocol_label.setWordWrap(True)
        self.track_combo = QComboBox()
        self.track_combo.setObjectName("sensor-track")
        self.track_combo.currentIndexChanged.connect(self._on_track)
        self.assignments = make_table(
            (
                tr("sensor.column.physical"),
                tr("sensor.column.position"),
                tr("sensor.column.lane"),
            ),
            "sensor-assignments",
        )
        self.physical_edit = QLineEdit()
        self.physical_edit.setObjectName("sensor-physical")
        self.position_edit = QLineEdit()
        self.position_edit.setObjectName("sensor-position")
        self.lane_spin = QSpinBox()
        self.lane_spin.setObjectName("sensor-lane")
        self.lane_spin.setRange(1, 99)
        add = QPushButton(tr("sensor.action.add"))
        add.setObjectName("sensor-add")
        add.clicked.connect(self._add_row)
        remove = QPushButton(tr("sensor.action.remove"))
        remove.setObjectName("sensor-remove")
        remove.clicked.connect(self._remove_row)
        save = QPushButton(tr("sensor.action.save"))
        save.setObjectName("sensor-save")
        save.clicked.connect(self._save)
        self.offset_edit = QLineEdit("0")
        self.offset_edit.setObjectName("sensor-offset")
        self.numerator_edit = QLineEdit("1")
        self.numerator_edit.setObjectName("sensor-numerator")
        self.denominator_edit = QLineEdit("1")
        self.denominator_edit.setObjectName("sensor-denominator")
        apply_mapping = QPushButton(tr("sensor.action.apply_mapping"))
        apply_mapping.setObjectName("sensor-map-apply")
        apply_mapping.clicked.connect(self._apply_mapping)
        clear_mapping = QPushButton(tr("sensor.action.clear_mapping"))
        clear_mapping.setObjectName("sensor-map-clear")
        clear_mapping.clicked.connect(self._clear_mapping)
        self.line_edit = QLineEdit()
        self.line_edit.setObjectName("sensor-line")
        self.line_edit.setPlaceholderText(tr("sensor.line.placeholder"))
        inject = QPushButton(tr("sensor.action.inject"))
        inject.setObjectName("sensor-inject")
        inject.clicked.connect(self._inject)
        self.status = StatusLabel("sensor-status")
        self.log = make_table(
            (
                tr("sensor.column.physical"),
                tr("sensor.column.position"),
                tr("sensor.column.lane"),
                tr("sensor.column.device_time"),
                tr("sensor.column.host_time"),
                tr("sensor.column.result"),
            ),
            "sensor-log",
        )
        form = QFormLayout()
        form.addRow(tr("sensor.field.mode"), self.mode_label)
        form.addRow(tr("sensor.field.mode"), self.transport_combo)
        form.addRow(tr("sensor.field.port"), self.port_combo)
        form.addRow(tr("sensor.field.baud"), self.baud_label)
        form.addRow(tr("sensor.field.identity"), self.identity_label)
        form.addRow(tr("sensor.field.quality"), self.quality_label)
        form.addRow(tr("sensor.field.connection"), self.connection_label)
        form.addRow(tr("sensor.field.sync"), self.sync_label)
        form.addRow(tr("sensor.field.readiness"), self.readiness_label)
        form.addRow(tr("sensor.field.track"), self.track_combo)
        editor = QHBoxLayout()
        editor.addWidget(self.physical_edit, 2)
        editor.addWidget(self.position_edit, 2)
        editor.addWidget(self.lane_spin)
        editor.addWidget(add)
        editor.addWidget(remove)
        editor.addWidget(save)
        mapping = QHBoxLayout()
        mapping.addWidget(self.offset_edit)
        mapping.addWidget(self.numerator_edit)
        mapping.addWidget(self.denominator_edit)
        mapping.addWidget(apply_mapping)
        mapping.addWidget(clear_mapping)
        frame = QHBoxLayout()
        frame.addWidget(self.line_edit, 1)
        frame.addWidget(inject)
        layout = QVBoxLayout(self)
        configure_page(layout)
        ports_row = QHBoxLayout()
        ports_row.addWidget(ports)
        ports_row.addWidget(connect)
        ports_row.addWidget(sync)
        layout.addWidget(self.protocol_label)
        layout.addLayout(form)
        layout.addLayout(ports_row)
        layout.addWidget(self.assignments)
        layout.addLayout(editor)
        layout.addLayout(mapping)
        layout.addLayout(frame)
        layout.addWidget(self.status)
        layout.addWidget(self.log, 1)
        self._fill_tracks()
        self._reload_rows()
        self.refresh()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._holding:
            self._link.acquire()
            self._holding = True
        self._timer.start()
        self.refresh()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._timer.stop()
        if self._holding:
            self._link.release()
            self._holding = False
        super().hideEvent(event)

    def refresh(self) -> None:
        """Redraw status and any lines already read. Does not open a second transport."""
        self._bind_saved()
        self._link.poll()
        self._paint_status()
        self._paint_log()

    def _fill_tracks(self) -> None:
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        if self._tracks is not None:
            for track in self._tracks.list_tracks():
                self.track_combo.addItem(track.name, int(track.id))
        self.track_combo.blockSignals(False)

    def _on_track(self) -> None:
        self._reload_rows()
        self.refresh()

    def _reload_rows(self) -> None:
        track_id = self._track_id()
        self._rows = [] if track_id is None else list(self._store.track_assignments(track_id))
        fill_table(
            self.assignments,
            [(row.physical_id, row.position_id, str(row.lane)) for row in self._rows],
            list(range(len(self._rows))),
        )

    def _add_row(self) -> None:
        try:
            assignment = SensorAssignment(
                physical_id=self.physical_edit.text().strip(),
                position_id=self.position_edit.text().strip(),
                lane=self.lane_spin.value(),
            )
        except ValueError as error:
            self.status.show_error(str(error))
            return
        self._rows.append(assignment)
        self._reload_table_from_rows()
        self.status.clear_message()

    def _remove_row(self) -> None:
        row = self.assignments.currentRow()
        if row < 0 or row >= len(self._rows):
            return
        del self._rows[row]
        self._reload_table_from_rows()

    def _save(self) -> None:
        track_id = self._track_id()
        if track_id is None:
            self.status.show_error(self._translator.translate("sensor.error.no_track"))
            return
        if self._rows:
            issues = self._issues(self._rows)
            if issues:
                self.status.show_error(
                    " ".join(self._translator.format(issue.key, **issue.params) for issue in issues)
                )
                return
        try:
            self._store.save_track(track_id, self._rows)
        except (SensorConfigurationError, ValueError) as error:
            self.status.show_error(describe_error(self._translator, error))
            return
        self.status.show_info(self._translator.translate("sensor.saved"))
        self.refresh()

    def _apply_mapping(self) -> None:
        try:
            mapping = DeviceClockMapping(
                offset_ns=_parse_int(self.offset_edit.text()),
                numerator=_parse_int(self.numerator_edit.text()),
                denominator=_parse_int(self.denominator_edit.text()),
            )
        except (TypeError, ValueError) as error:
            self.status.show_error(
                self._translator.format("sensor.error.mapping", detail=str(error))
            )
            return
        self._link.set_time_mapping(mapping)
        self.status.show_info(self._translator.translate("sensor.mapping.applied"))
        self.refresh()

    def _clear_mapping(self) -> None:
        self._link.lose_time_mapping()
        self.status.show_info(self._translator.translate("sensor.mapping.cleared"))
        self.refresh()

    def _inject(self) -> None:
        if self._link.mode.value != "simulation":
            self.status.show_error(self._translator.translate("sensor.error.not_simulation"))
            return
        try:
            self._link.inject(self.line_edit.text())
        except RuntimeError:
            self.status.show_error(self._translator.translate("sensor.error.not_open"))
            return
        self.line_edit.clear()
        self.refresh()

    def _bind_saved(self) -> None:
        track_id = self._track_id()
        if track_id is None:
            self._link.bind_view((), {})
            return
        setup = self._setup(track_id)
        logical = {sensor.position_id: sensor.id for sensor in setup.sensors}
        try:
            saved = self._store.track_assignments(track_id)
        except SensorConfigurationError:
            saved = ()
        self._link.bind_view(saved, logical)

    def _refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        for name in list_serial_ports():
            self.port_combo.addItem(name)
        if current:
            self.port_combo.setCurrentText(current)

    def _connect(self) -> None:
        mode = self.transport_combo.currentData()
        try:
            if mode == TransportMode.SERIAL.value:
                port = self.port_combo.currentText().strip()
                if not port:
                    self.status.show_error(self._translator.translate("sensor.error.port"))
                    return
                self._link.use_transport(SerialSensorTransport(port), TransportMode.SERIAL)
                self.mode_label.setText(self._translator.translate("sensor.mode.serial"))
                self.status.show_info(self._translator.translate("sensor.connected_serial"))
            else:
                self._link.use_transport(SimulatedSensorTransport(), TransportMode.SIMULATION)
                self.mode_label.setText(self._translator.translate("sensor.mode.simulation"))
                self.status.clear_message()
        except RuntimeError:
            self.status.show_error(self._translator.translate("sensor.error.race_holds_reader"))
            return
        except Exception as error:
            self.status.show_error(
                self._translator.format("sensor.error.serial", detail=str(error))
            )
            return
        self.refresh()

    def _request_sync(self) -> None:
        try:
            self._link.request_sync()
        except RuntimeError:
            self.status.show_error(self._translator.translate("sensor.error.sync"))
            return
        self.refresh()

    def _paint_status(self) -> None:
        tr = self._translator.translate
        self.mode_label.setText(tr(f"sensor.mode.{self._link.mode.value}"))
        identity = self._link.identity
        if identity is None:
            self.identity_label.setText(tr("sensor.identity.none"))
        else:
            sensors = " ".join(identity.sensors)
            self.identity_label.setText(
                f"{identity.device} {identity.firmware} {identity.session_id} {sensors}"
            )
        samples, uncertainty, numerator, denominator, accepted = self._link.sync_quality
        if accepted:
            self.quality_label.setText(
                self._translator.format(
                    "sensor.quality.value",
                    samples=samples,
                    uncertainty=uncertainty,
                    numerator=numerator,
                    denominator=denominator,
                )
            )
        else:
            self.quality_label.setText(tr("sensor.quality.none"))
        self.connection_label.setText(
            tr("sensor.connection.open") if self._link.connected else tr("sensor.connection.closed")
        )
        self.sync_label.setText(tr(f"sensor.sync.{self._link.sync.value}"))
        ready = self._measurement_ready()
        self.readiness_label.setText(
            tr("sensor.readiness.ready") if ready else tr("sensor.readiness.not_ready")
        )

    def _paint_log(self) -> None:
        observations = self._link.observations
        fill_table(
            self.log,
            [self._log_row(item) for item in observations],
            list(range(len(observations))),
        )

    def _log_row(self, item: SensorObservation) -> tuple[str, str, str, str, str, str]:
        return (
            item.physical_id or "",
            item.position_id or "",
            "" if item.lane is None else str(item.lane),
            "" if item.device_time_ns is None else str(item.device_time_ns),
            "" if item.host_time_ns is None else str(item.host_time_ns),
            self._translator.format(
                f"sensor.observation.{item.kind}",
                gap=item.gap,
                detail=item.detail,
            ),
        )

    def _measurement_ready(self) -> bool:
        track_id = self._track_id()
        if track_id is None or not self._link.connected:
            return False
        if self._link.sync is not SyncStatus.SYNCHRONIZED:
            return False
        try:
            saved = self._store.track_assignments(track_id)
        except SensorConfigurationError:
            return False
        return not self._issues(saved)

    def _issues(
        self, rows: list[SensorAssignment] | tuple[SensorAssignment, ...]
    ) -> tuple[ValidationError, ...]:
        track_id = self._track_id()
        if track_id is None:
            return ()
        setup = self._setup(track_id)
        start_finish = next(
            position.id
            for position in setup.layout.positions
            if position.type is TimingPositionType.START_FINISH
        )
        return assignment_issues(
            rows,
            positions=frozenset(position.id for position in setup.layout.positions),
            lane_count=self._lane_count(track_id),
            start_finish_id=start_finish,
        )

    def _setup(self, track_id: int) -> TimingSetup:
        if self._setups is not None:
            stored = self._setups.get_setup(TrackId(track_id))
            if stored is not None:
                return stored
        return default_timing_setup()

    def _lane_count(self, track_id: int) -> int:
        if self._tracks is None:
            return 0
        track = self._tracks.get_track(TrackId(track_id))
        if track is None:
            return 0
        return track.lane_count

    def _track_id(self) -> int | None:
        data = self.track_combo.currentData()
        if isinstance(data, bool) or not isinstance(data, int):
            return None
        return data

    def _reload_table_from_rows(self) -> None:
        fill_table(
            self.assignments,
            [(row.physical_id, row.position_id, str(row.lane)) for row in self._rows],
            list(range(len(self._rows))),
        )


def _parse_int(text: str) -> int:
    stripped = text.strip()
    if stripped.startswith("+") or stripped in {"", "-"}:
        raise ValueError("not an integer")
    return int(stripped, 10)
