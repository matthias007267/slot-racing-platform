"""Diagnostic page. It shares the sensor link and does not start a race."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import TrackCatalog, TrackInfo
from slot_racing.core.domain import TimingPosition, TimingPositionType, TimingSetup, TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.core.timing import TimingSetupService
from slot_racing.modules.timing_sensor.clock_map import DeviceClockMapping, SyncStatus
from slot_racing.modules.timing_sensor.configuration import (
    DeviceHint,
    SensorAssignment,
    assignment_issues,
)
from slot_racing.modules.timing_sensor.devices import (
    PortMatch,
    SerialPortFacts,
    attach_remembered_serial,
    classify_port,
    list_serial_devices,
    remembered_hint,
)
from slot_racing.modules.timing_sensor.diagnostics import sensor_diagnostic
from slot_racing.modules.timing_sensor.intake import SensorObservation
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.protocol import BAUD_RATE
from slot_racing.modules.timing_sensor.store import (
    SensorConfigurationError,
    SensorConfigurationStore,
)
from slot_racing.modules.timing_sensor.transport import (
    SensorTransport,
    SerialSensorTransport,
    SimulatedSensorTransport,
)
from slot_racing.uikit import describe_error, make_table
from slot_racing.uikit.theme import configure_page
from slot_racing.uikit.widgets import StatusLabel, fill_table

_DEFAULT_SENSORS = ("s1", "s2")
TrackSource = TrackCatalog | Callable[[], TrackCatalog | None] | None
SetupSource = TimingSetupService | Callable[[], TimingSetupService | None] | None
PortLister = Callable[[], tuple[SerialPortFacts, ...]]
TransportOpener = Callable[[str], SensorTransport]


class SensorTimingPage(QWidget):
    """Connection, the track's sensor map, and the lines the shared link has seen."""

    def __init__(
        self,
        translator: Translator,
        link: SensorLink,
        store: SensorConfigurationStore,
        setups: SetupSource,
        tracks: TrackSource,
        *,
        ports: PortLister | None = None,
        serial_transport: TransportOpener | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("sensor-page")
        self._translator = translator
        self._link = link
        self._store = store
        self._setups = setups
        self._tracks = tracks
        self._port_lister = ports
        self._serial_transport = serial_transport
        self._holding = False
        self._applying_preferences = False
        self._announced_session: str | None = None
        self._stored_session: str | None = None
        self._track_snapshot: tuple[tuple[int, str, int, bool], ...] | None = None
        self._row_signature: tuple[object, ...] | None = None
        self._editors: dict[str, tuple[QComboBox, QComboBox]] = {}
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
        ports_button = QPushButton(tr("sensor.action.ports"))
        ports_button.setObjectName("sensor-ports")
        ports_button.clicked.connect(self._refresh_ports)
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
        self.setup_hint = QLabel("")
        self.setup_hint.setObjectName("sensor-setup-hint")
        self.setup_hint.setWordWrap(True)
        self.prerequisites = QLabel("")
        self.prerequisites.setObjectName("sensor-prerequisites")
        self.prerequisites.setWordWrap(True)
        self._open_timing = QPushButton(tr("sensor.action.open_timing"))
        self._open_timing.setObjectName("sensor-open-timing")
        self._open_timing.clicked.connect(self._open_timing_setup)
        self._open_tracks = QPushButton(tr("sensor.action.open_tracks"))
        self._open_tracks.setObjectName("sensor-open-tracks")
        self._open_tracks.clicked.connect(self._open_track_list)
        self._assignment_host = QWidget()
        self._assignment_host.setObjectName("sensor-assignments")
        self._assignment_grid = QGridLayout(self._assignment_host)
        self._assignment_grid.setContentsMargins(0, 0, 0, 0)
        save = QPushButton(tr("sensor.action.save"))
        save.setObjectName("sensor-save")
        save.clicked.connect(self._save)
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
        self.log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        developer = self._developer_box()
        form = QFormLayout()
        form.addRow(tr("sensor.field.current"), self.mode_label)
        form.addRow(tr("sensor.field.source"), self.transport_combo)
        form.addRow(tr("sensor.field.port"), self.port_combo)
        form.addRow(tr("sensor.field.baud"), self.baud_label)
        form.addRow(tr("sensor.field.identity"), self.identity_label)
        form.addRow(tr("sensor.field.quality"), self.quality_label)
        form.addRow(tr("sensor.field.connection"), self.connection_label)
        form.addRow(tr("sensor.field.sync"), self.sync_label)
        form.addRow(tr("sensor.field.readiness"), self.readiness_label)
        form.addRow(tr("sensor.field.track"), self.track_combo)
        actions = QHBoxLayout()
        actions.addWidget(self._open_timing)
        actions.addWidget(self._open_tracks)
        actions.addWidget(save)
        actions.addStretch(1)
        ports_row = QHBoxLayout()
        export = QPushButton(tr("sensor.action.export"))
        export.setObjectName("sensor-export")
        export.clicked.connect(self._export_diagnostic)
        ports_row.addWidget(ports_button)
        ports_row.addWidget(connect)
        ports_row.addWidget(sync)
        ports_row.addWidget(export)
        ports_row.addStretch(1)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        configure_page(top_layout)
        top_layout.addWidget(self.protocol_label)
        top_layout.addLayout(form)
        top_layout.addLayout(ports_row)
        top_layout.addWidget(self.setup_hint)
        top_layout.addLayout(actions)
        top_layout.addWidget(self._assignment_host)
        top_layout.addWidget(self.prerequisites)
        top_layout.addWidget(developer)
        top_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("sensor-controls-scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(top)
        scroll.setMinimumHeight(120)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        log_title = QLabel(tr("sensor.log.title"))
        log_title.setObjectName("sensor-log-title")
        log_wrap = QWidget()
        log_wrap.setObjectName("sensor-log-wrap")
        log_layout = QVBoxLayout(log_wrap)
        configure_page(log_layout)
        log_layout.addWidget(log_title)
        log_layout.addWidget(self.log, 1)
        log_wrap.setMinimumHeight(180)
        log_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("sensor-splitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(scroll)
        splitter.addWidget(log_wrap)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addWidget(self.status)
        layout.addWidget(splitter, 1)
        self._fill_tracks()
        self._load_preferences()
        self._ensure_rows(force=True)
        self.refresh()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._attach_remembered()
        if not self._holding:
            self._link.acquire()
            self._holding = True
        self._timer.start()
        self.refresh()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        # USB stays open and keeps being read. Closing the port resets the board,
        # and stopping here leaves a finished clock exchange invisible to a new race.
        if self._link.mode is TransportMode.SERIAL:
            super().hideEvent(event)
            return
        self._timer.stop()
        if self._holding:
            self._link.release()
            self._holding = False
        super().hideEvent(event)

    def refresh(self) -> None:
        """Redraw status, the track list, and lines already read."""
        self._fill_tracks()
        self._ensure_rows()
        self._bind_saved()
        self._link.poll()
        self._paint_status()
        self._paint_guidance()
        self._paint_log()

    def _developer_box(self) -> QGroupBox:
        tr = self._translator.translate
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
        content = QWidget()
        content.setObjectName("sensor-developer-body")
        form = QFormLayout(content)
        form.addRow(tr("sensor.field.offset"), self.offset_edit)
        form.addRow(tr("sensor.field.numerator"), self.numerator_edit)
        form.addRow(tr("sensor.field.denominator"), self.denominator_edit)
        mapping = QHBoxLayout()
        mapping.addWidget(apply_mapping)
        mapping.addWidget(clear_mapping)
        form.addRow(tr("sensor.field.sync"), mapping)
        frame = QHBoxLayout()
        frame.addWidget(self.line_edit, 1)
        frame.addWidget(inject)
        form.addRow(tr("sensor.field.line"), frame)
        box = QGroupBox(tr("sensor.developer"))
        box.setObjectName("sensor-developer")
        box.setCheckable(True)
        box.setChecked(False)
        outer = QVBoxLayout(box)
        outer.addWidget(content)
        content.setVisible(False)
        self._developer_body = content
        box.toggled.connect(self._on_developer_toggled)
        return box

    def _catalog(self) -> TrackCatalog | None:
        source = self._tracks
        if isinstance(source, TrackCatalog):
            return source
        if source is None:
            return None
        found = source()
        return found if isinstance(found, TrackCatalog) else None

    def _setup_service(self) -> TimingSetupService | None:
        source = self._setups
        if isinstance(source, TimingSetupService):
            return source
        if source is None:
            return None
        found = source()
        return found if isinstance(found, TimingSetupService) else None

    def _fill_tracks(self) -> None:
        catalog = self._catalog()
        tracks = () if catalog is None else tuple(catalog.list_tracks())
        snapshot = tuple(
            (int(track.id), track.name, track.lane_count, track.is_active) for track in tracks
        )
        if snapshot == self._track_snapshot:
            return
        current = self._track_id()
        self._track_snapshot = snapshot
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        inactive = self._translator.translate("sensor.track.inactive")
        for track_id, name, _lanes, active in snapshot:
            label = name if active else f"{name} ({inactive})"
            self.track_combo.addItem(label, track_id)
        if current is not None:
            index = self.track_combo.findData(current)
            if index >= 0:
                self.track_combo.setCurrentIndex(index)
        self.track_combo.blockSignals(False)
        self._row_signature = None

    def _on_track(self) -> None:
        self._row_signature = None
        self._ensure_rows(force=True)
        if not self._applying_preferences:
            track_id = self._track_id()
            if track_id is not None:
                self._save_preferences(track_id=track_id)
        self.refresh()

    def _ensure_rows(self, *, force: bool = False) -> None:
        track_id = self._track_id()
        sensors = self._visible_sensors(track_id)
        positions = self._position_choices(track_id)
        lanes = self._lane_values(track_id)
        signature = (track_id, sensors, positions, lanes)
        if not force and signature == self._row_signature:
            return
        saved = {item.physical_id: item for item in self._saved(track_id)}
        self._row_signature = signature
        self._editors.clear()
        while (item := self._assignment_grid.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if track_id is None:
            return
        tr = self._translator.translate
        headers = (
            tr("sensor.column.physical"),
            tr("sensor.column.position"),
            tr("sensor.column.lane"),
        )
        for column, text in enumerate(headers):
            label = QLabel(text)
            self._assignment_grid.addWidget(label, 0, column)
        for row_index, physical in enumerate(sensors, start=1):
            name = QLabel(physical)
            name.setObjectName(f"sensor-name-{physical}")
            position = QComboBox()
            position.setObjectName(f"sensor-position-{physical}")
            position.addItem(tr("sensor.position.choose"), "")
            for position_id, caption in positions:
                position.addItem(caption, position_id)
            lane = QComboBox()
            lane.setObjectName(f"sensor-lane-{physical}")
            for value in lanes:
                lane.addItem(self._translator.format("sensor.lane.value", lane=value), value)
            known = saved.get(physical)
            if known is not None:
                _select_data(position, known.position_id)
                if lane.findData(known.lane) < 0:
                    lane.addItem(
                        self._translator.format("sensor.lane.value", lane=known.lane),
                        known.lane,
                    )
                _select_data(lane, known.lane)
            self._assignment_grid.addWidget(name, row_index, 0)
            self._assignment_grid.addWidget(position, row_index, 1)
            self._assignment_grid.addWidget(lane, row_index, 2)
            self._editors[physical] = (position, lane)

    def _visible_sensors(self, track_id: int | None) -> tuple[str, ...]:
        identity = self._link.identity
        recognized = (
            identity.sensors if identity is not None and identity.sensors else _DEFAULT_SENSORS
        )
        ordered = list(recognized)
        for item in self._saved(track_id):
            if item.physical_id not in ordered:
                ordered.append(item.physical_id)
        return tuple(ordered)

    def _position_choices(self, track_id: int | None) -> tuple[tuple[str, str], ...]:
        setup = None if track_id is None else self._stored_setup(track_id)
        if setup is None:
            return ()
        return tuple(
            (position.id, self._position_label(position)) for position in setup.layout.positions
        )

    def _position_label(self, position: TimingPosition) -> str:
        if position.name:
            return position.name
        if position.type is TimingPositionType.START_FINISH:
            return self._translator.translate("sensor.position.start_finish")
        number = position.sector_number if position.sector_number is not None else 0
        return self._translator.format("sensor.position.sector", number=number)

    def _lane_values(self, track_id: int | None) -> tuple[int, ...]:
        count = 0 if track_id is None else self._lane_count(track_id)
        if count < 1:
            return ()
        return tuple(range(1, count + 1))

    def _saved(self, track_id: int | None) -> tuple[SensorAssignment, ...]:
        if track_id is None:
            return ()
        try:
            return self._store.track_assignments(track_id)
        except SensorConfigurationError:
            return ()

    def _form_rows(self) -> tuple[SensorAssignment, ...] | None:
        rows: list[SensorAssignment] = []
        for physical, (position, lane) in self._editors.items():
            position_id = position.currentData()
            lane_value = lane.currentData()
            if not isinstance(position_id, str) or not position_id:
                return None
            if isinstance(lane_value, bool) or not isinstance(lane_value, int):
                return None
            rows.append(
                SensorAssignment(physical_id=physical, position_id=position_id, lane=lane_value)
            )
        return tuple(rows)

    def _save(self) -> None:
        track_id = self._track_id()
        if track_id is None:
            self.status.show_error(self._translator.translate("sensor.error.no_track"))
            return
        if self._stored_setup(track_id) is None:
            self.status.show_error(self._setup_message(track_id))
            return
        missing = self._missing_fields()
        if missing:
            self.status.show_error(missing[0])
            return
        rows = self._form_rows()
        if rows is None:
            self.status.show_error(self._translator.translate("sensor.error.no_track"))
            return
        issues = self._issues(rows)
        if issues:
            self.status.show_error(
                " ".join(self._translator.format(issue.key, **issue.params) for issue in issues)
            )
            return
        try:
            self._store.save_track(track_id, rows)
        except (SensorConfigurationError, ValueError) as error:
            self.status.show_error(describe_error(self._translator, error))
            return
        self.status.show_info(self._translator.translate("sensor.saved"))
        self.refresh()

    def _apply_mapping(self) -> None:
        parsed = self._developer_ints()
        if parsed is None:
            self.status.show_error(
                self._translator.format("sensor.error.mapping", detail="not an integer")
            )
            return
        offset, numerator, denominator = parsed
        try:
            mapping = DeviceClockMapping(offset, numerator, denominator)
        except (TypeError, ValueError) as error:
            self.status.show_error(
                self._translator.format("sensor.error.mapping", detail=str(error))
            )
            return
        if not self._save_preferences(
            developer_offset_ns=offset,
            developer_numerator=numerator,
            developer_denominator=denominator,
            developer_expanded=True,
        ):
            return
        if self._link.mode is TransportMode.SIMULATION:
            self._link.set_time_mapping(mapping)
            self.status.show_info(self._translator.translate("sensor.mapping.applied"))
        else:
            self.status.show_info(self._translator.translate("sensor.mapping.stored"))
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
        setup = self._stored_setup(track_id)
        logical = (
            {} if setup is None else {sensor.position_id: sensor.id for sensor in setup.sensors}
        )
        self._link.bind_view(self._saved(track_id), logical)

    def _refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        for facts in self._listed_ports():
            self.port_combo.addItem(facts.device)
        if current:
            self.port_combo.setCurrentText(current)
            return
        # Listing ports must not select an arbitrary device.
        self.port_combo.setCurrentIndex(-1)
        self.port_combo.setEditText("")

    def _connect(self) -> None:
        mode = self.transport_combo.currentData()
        try:
            if mode == TransportMode.SERIAL.value:
                port = self.port_combo.currentText().strip()
                if not port:
                    self.status.show_error(self._translator.translate("sensor.error.port"))
                    return
                if (
                    self._link.mode is TransportMode.SERIAL
                    and self._link.port == port
                    and self._link.port_open
                ):
                    self.status.show_info(self._translator.translate("sensor.already_open"))
                    self._save_preferences(mode=TransportMode.SERIAL.value, port=port)
                    self.refresh()
                    return
                self._link.use_transport(self._open_serial(port), TransportMode.SERIAL)
                self.mode_label.setText(self._translator.translate("sensor.mode.serial"))
                self.status.show_info(self._translator.translate("sensor.connected_serial"))
                self._save_preferences(mode=TransportMode.SERIAL.value, port=port)
            else:
                self._link.use_transport(SimulatedSensorTransport(), TransportMode.SIMULATION)
                self.mode_label.setText(self._translator.translate("sensor.mode.simulation"))
                self.status.clear_message()
                self._save_preferences(mode=TransportMode.SIMULATION.value)
        except RuntimeError:
            self.status.show_error(self._translator.translate("sensor.error.race_holds_reader"))
            return
        except Exception as error:
            self.status.show_error(
                self._translator.format("sensor.error.serial", detail=str(error))
            )
            return
        self._announced_session = None
        self.refresh()

    def _request_sync(self) -> None:
        try:
            self._link.request_sync()
        except RuntimeError:
            self.status.show_error(self._translator.translate("sensor.error.sync"))
            return
        self.refresh()

    def _open_track_list(self) -> None:
        self._navigate("tracks", None)

    def _open_timing_setup(self) -> None:
        self._navigate("tracks", self._track_id())

    def _navigate(self, entry_id: str, track_id: int | None) -> None:
        window = self.window()
        select = getattr(window, "select", None)
        if not callable(select):
            self.status.show_error(self._translator.translate("sensor.error.timing_navigation"))
            return
        select(entry_id)
        if track_id is None:
            return
        current = getattr(window, "current_page", None)
        page = current() if callable(current) else None
        show_config = getattr(page, "show_config", None)
        if callable(show_config):
            show_config(track_id)
            return
        self.status.show_error(self._translator.translate("sensor.error.timing_navigation"))

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
            if (
                self._link.mode is TransportMode.SERIAL
                and identity.session_id != self._announced_session
            ):
                self._announced_session = identity.session_id
                self._remember_device()
                self.status.show_info(tr("sensor.hint.sync_started"))
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

    def _paint_guidance(self) -> None:
        track_id = self._track_id()
        catalog = self._catalog()
        self._open_tracks.setVisible(track_id is None)
        setup_missing = track_id is not None and self._stored_setup(track_id) is None
        self._open_timing.setVisible(setup_missing)
        if catalog is None:
            self.setup_hint.setText(self._translator.translate("sensor.hint.no_catalog"))
        elif track_id is None:
            self.setup_hint.setText(self._translator.translate("sensor.hint.no_tracks"))
        elif setup_missing:
            self.setup_hint.setText(self._setup_message(track_id))
        else:
            self.setup_hint.setText("")
        device_note = self._device_note()
        if device_note:
            notes = [device_note, *self._missing_fields()]
        else:
            notes = list(self._missing_fields())
        rows = self._form_rows()
        if rows is not None:
            notes.extend(
                self._translator.format(issue.key, **issue.params) for issue in self._issues(rows)
            )
        if rows is not None and rows != self._saved(track_id):
            notes.append(self._translator.translate("sensor.hint.unsaved"))
        self.prerequisites.setText(" ".join(notes))

    def _setup_message(self, track_id: int) -> str:
        track = self._track(track_id)
        name = str(track_id) if track is None else track.name
        return self._translator.format("sensor.hint.no_setup", name=name)

    def _missing_fields(self) -> tuple[str, ...]:
        notes: list[str] = []
        for physical, (position, lane) in self._editors.items():
            position_id = position.currentData()
            lane_value = lane.currentData()
            if not isinstance(position_id, str) or not position_id:
                notes.append(
                    self._translator.format("sensor.hint.position_missing", sensor=physical)
                )
            if isinstance(lane_value, bool) or not isinstance(lane_value, int):
                notes.append(self._translator.format("sensor.hint.lane_missing", sensor=physical))
        return tuple(notes)

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
            self._result_text(item),
        )

    def _result_text(self, item: SensorObservation) -> str:
        """Name the delivery stage. A parsed line is not described as accepted by the race."""
        translate = self._translator.translate
        if item.engine_result == "completed":
            return translate("sensor.delivery.lap")
        if item.engine_result:
            return translate(f"sensor.engine.{item.engine_result}", default=item.engine_result)
        if item.kind == "withheld":
            detail = translate(f"sensor.withheld.{item.detail}", default=item.detail)
            return self._translator.format("sensor.observation.withheld", detail=detail)
        if item.kind == "accepted" and item.delivery == "published":
            return translate("sensor.delivery.published")
        if item.kind == "accepted":
            return translate("sensor.delivery.received")
        return self._translator.format(
            f"sensor.observation.{item.kind}",
            gap=item.gap,
            detail=item.detail,
        )

    def _measurement_ready(self) -> bool:
        track_id = self._track_id()
        if track_id is None or not self._link.connected:
            return False
        if self._link.sync is not SyncStatus.SYNCHRONIZED:
            return False
        if self._link.mode is TransportMode.SERIAL and self._link.mapping_origin != "measured":
            return False
        if self._stored_setup(track_id) is None:
            return False
        saved = self._saved(track_id)
        return bool(saved) and not self._issues(saved)

    def _issues(self, rows: Sequence[SensorAssignment]) -> tuple[ValidationError, ...]:
        track_id = self._track_id()
        setup = None if track_id is None else self._stored_setup(track_id)
        if track_id is None or setup is None:
            return ()
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

    def _stored_setup(self, track_id: int) -> TimingSetup | None:
        service = self._setup_service()
        if service is None:
            return None
        return service.get_setup(TrackId(track_id))

    def _lane_count(self, track_id: int) -> int:
        track = self._track(track_id)
        if track is None:
            return 0
        return track.lane_count

    def _track(self, track_id: int) -> TrackInfo | None:
        catalog = self._catalog()
        if catalog is None:
            return None
        return catalog.get_track(TrackId(track_id))

    def _track_id(self) -> int | None:
        data = self.track_combo.currentData()
        if isinstance(data, bool) or not isinstance(data, int):
            return None
        return data

    def save_persistent(self) -> None:
        """Store the last accepted operating choices. Invalid developer text is left untouched."""
        changes: dict[str, object] = {}
        mode = self.transport_combo.currentData()
        if mode in {TransportMode.SIMULATION.value, TransportMode.SERIAL.value}:
            changes["mode"] = mode
        port = self.port_combo.currentText().strip()
        if port and not any(character.isspace() for character in port) and len(port) <= 64:
            changes["port"] = port
        track_id = self._track_id()
        if track_id is not None:
            changes["track_id"] = track_id
        developer = self.findChild(QGroupBox, "sensor-developer")
        if isinstance(developer, QGroupBox):
            changes["developer_expanded"] = developer.isChecked()
        parsed = self._developer_ints()
        if parsed is not None:
            offset, numerator, denominator = parsed
            changes["developer_offset_ns"] = offset
            changes["developer_numerator"] = numerator
            changes["developer_denominator"] = denominator
        if changes:
            self._save_preferences(**changes)

    def diagnostic_payload(self) -> dict[str, object]:
        """The export document, including the last race attempt still held by the link."""
        track_id = self._track_id()
        setup = None if track_id is None else self._stored_setup(track_id)
        positions = (
            () if setup is None else tuple(position.id for position in setup.layout.positions)
        )
        return sensor_diagnostic(
            self._link,
            track_id=track_id,
            positions=positions,
            assignments=self._saved(track_id),
        )

    def write_diagnostic(self, path: str | Path) -> None:
        target = Path(path)
        text = json.dumps(self.diagnostic_payload(), indent=2, ensure_ascii=False)
        target.write_text(text + "\n", encoding="utf-8")

    def _export_diagnostic(self) -> None:
        translate = self._translator.translate
        selected, _chosen = QFileDialog.getSaveFileName(
            self,
            translate("sensor.export.title"),
            "sensor-diagnostic.json",
            "JSON (*.json)",
        )
        if not selected:
            return
        try:
            self.write_diagnostic(selected)
        except OSError:
            self.status.show_error(translate("sensor.export.failed"))
            return
        self.status.show_info(translate("sensor.export.saved"))

    def _load_preferences(self) -> None:
        try:
            configuration = self._store.load()
        except SensorConfigurationError:
            self.status.show_error(self._translator.translate("sensor.error.configuration"))
            return
        self._applying_preferences = True
        try:
            mode_index = self.transport_combo.findData(configuration.mode)
            if mode_index >= 0:
                self.transport_combo.setCurrentIndex(mode_index)
            self._refresh_ports()
            _status, match = classify_port(remembered_hint(configuration), self._listed_ports())
            if match is not None:
                self.port_combo.setCurrentText(match.device)
            if configuration.track_id is not None:
                track_index = self.track_combo.findData(configuration.track_id)
                if track_index >= 0:
                    self.track_combo.setCurrentIndex(track_index)
            developer = self.findChild(QGroupBox, "sensor-developer")
            if isinstance(developer, QGroupBox):
                developer.setChecked(configuration.developer_expanded)
            if configuration.developer_offset_ns is not None:
                self.offset_edit.setText(str(configuration.developer_offset_ns))
            if configuration.developer_numerator is not None:
                self.numerator_edit.setText(str(configuration.developer_numerator))
            if configuration.developer_denominator is not None:
                self.denominator_edit.setText(str(configuration.developer_denominator))
        finally:
            self._applying_preferences = False

    def _save_preferences(self, **changes: object) -> bool:
        try:
            self._store.save_preferences(**changes)
        except SensorConfigurationError:
            self.status.show_error(self._translator.translate("sensor.error.configuration"))
            return False
        return True

    def _attach_remembered(self) -> None:
        if self._link.port_open or self._link.connected or self._link.race_bound:
            return
        try:
            configuration = self._store.load()
        except SensorConfigurationError:
            return
        try:
            attach_remembered_serial(
                self._link,
                configuration,
                self._listed_ports(),
                open_port=self._open_serial,
            )
        except (OSError, RuntimeError, ValueError) as error:
            self.status.show_error(
                self._translator.format("sensor.error.serial", detail=str(error))
            )

    def _open_serial(self, port: str) -> SensorTransport:
        if self._serial_transport is not None:
            return self._serial_transport(port)
        return SerialSensorTransport(port)

    def _listed_ports(self) -> tuple[SerialPortFacts, ...]:
        if self._port_lister is not None:
            return self._port_lister()
        return list_serial_devices()

    def _remember_device(self) -> None:
        identity = self._link.identity
        if (
            self._link.mode is not TransportMode.SERIAL
            or identity is None
            or identity.session_id == self._stored_session
        ):
            return
        facts = next(
            (item for item in self._listed_ports() if item.device == self._link.port),
            None,
        )
        try:
            hint = DeviceHint(
                vid=None if facts is None else facts.vid,
                pid=None if facts is None else facts.pid,
                serial_number="" if facts is None else facts.serial_number,
                location="" if facts is None else facts.location,
                port=self._link.port,
            )
        except ValueError:
            return
        if not self._save_preferences(mode="serial", port=hint.port, device=hint):
            return
        self._stored_session = identity.session_id

    def _device_note(self) -> str:
        if self._link.connected:
            return ""
        try:
            configuration = self._store.load()
        except SensorConfigurationError:
            return ""
        if configuration.mode != "serial":
            return ""
        hint = remembered_hint(configuration)
        status, _match = classify_port(hint, self._listed_ports())
        if status is PortMatch.AMBIGUOUS:
            return self._translator.translate("sensor.hint.device_ambiguous")
        if status is PortMatch.ABSENT and (hint.port or hint.serial_number or hint.vid is not None):
            return self._translator.translate("sensor.hint.device_absent")
        return ""

    def _on_developer_toggled(self, checked: bool) -> None:
        self._developer_body.setVisible(checked)
        if self._applying_preferences:
            return
        self._save_preferences(developer_expanded=checked)

    def _developer_ints(self) -> tuple[int, int, int] | None:
        try:
            return (
                _parse_int(self.offset_edit.text()),
                _parse_int(self.numerator_edit.text()),
                _parse_int(self.denominator_edit.text()),
            )
        except ValueError:
            return None


def _select_data(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _parse_int(text: str) -> int:
    stripped = text.strip()
    if stripped.startswith("+") or stripped in {"", "-"}:
        raise ValueError("not an integer")
    return int(stripped, 10)
