"""Sensor timing: assignment, provisional frames, clock conversion, duplicates and gaps."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QGroupBox, QLabel, QPushButton
from pytestqt.qtbot import QtBot

from slot_racing.core.catalog import TrackCatalog, TrackInfo
from slot_racing.core.domain import (
    TimingPositionType,
    TimingSetup,
    TrackId,
    default_timing_setup,
)
from slot_racing.core.events import SensorTriggered
from slot_racing.core.i18n import Translator
from slot_racing.core.messages import CORE_TRANSLATIONS
from slot_racing.core.timing import TimingSessionSpec, TimingSetupService
from slot_racing.modules.timing_sensor.clock_map import DeviceClockMapping, SyncStatus, to_host_ns
from slot_racing.modules.timing_sensor.configuration import (
    DeviceCapacity,
    SensorAssignment,
    SensorConfiguration,
    TrackSensorMap,
    assignment_issues,
)
from slot_racing.modules.timing_sensor.intake import IntakeState, consume_line
from slot_racing.modules.timing_sensor.link import SensorLink
from slot_racing.modules.timing_sensor.plugin import SensorTimingPlugin
from slot_racing.modules.timing_sensor.protocol import (
    PROVISIONAL_PROTOCOL,
    SensorFrame,
    SensorProtocolError,
    format_sensor_frame,
    parse_sensor_line,
)
from slot_racing.modules.timing_sensor.provider import SensorTimingFactory, SensorTimingSource
from slot_racing.modules.timing_sensor.store import SensorConfigurationStore
from slot_racing.modules.timing_sensor.transport import (
    BufferedSensorTransport,
    SimulatedSensorTransport,
)
from slot_racing.modules.timing_sensor.ui.page import SensorTimingPage
from tests.database import migrated_database

_POSITIONS = frozenset({"start_finish", "sector_1", "sector_2"})
_LOGICAL = {"start_finish": "start_finish", "sector_1": "sensor_01", "sector_2": "sensor_02"}


def _assignment(physical: str, position: str, lane: int) -> SensorAssignment:
    return SensorAssignment(physical_id=physical, position_id=position, lane=lane)


def _pair() -> tuple[SensorAssignment, SensorAssignment]:
    return (
        _assignment("gate-a", "start_finish", 1),
        _assignment("gate-b", "start_finish", 2),
    )


def _frame(session: str, sequence: int, device_ns: int, physical: str) -> str:
    return format_sensor_frame(SensorFrame(session, sequence, device_ns, physical))


def _issues(assignments: Sequence[SensorAssignment], **overrides: object) -> tuple[str, ...]:
    lane_count = overrides.pop("lane_count", 2)
    capacity = overrides.pop("capacity", None)
    assert isinstance(lane_count, int)
    assert capacity is None or isinstance(capacity, DeviceCapacity)
    found = assignment_issues(
        assignments,
        positions=_POSITIONS,
        lane_count=lane_count,
        start_finish_id="start_finish",
        capacity=capacity,
    )
    return tuple(issue.key for issue in found)


class _Setups(TimingSetupService):
    def get_setup(self, track_id: TrackId) -> TimingSetup | None:
        return default_timing_setup() if int(track_id) == 1 else None

    def save_setup(self, track_id: TrackId, setup: TimingSetup) -> None:
        del track_id, setup

    def clear_setup(self, track_id: TrackId) -> None:
        del track_id


class _Tracks(TrackCatalog):
    def __init__(self, lane_count: int = 2) -> None:
        self._track = TrackInfo(TrackId(1), "Ring", None, lane_count, True)

    def list_tracks(self, *, active_only: bool = False) -> list[TrackInfo]:
        del active_only
        return [self._track]

    def get_track(self, track_id: TrackId) -> TrackInfo | None:
        if int(track_id) == 1:
            return self._track
        return None


def _translator() -> Translator:
    translator = Translator()
    translator.add_catalog("de", CORE_TRANSLATIONS["de"])
    translator.add_catalog("de", SensorTimingPlugin.translations["de"])
    return translator


def test_provisional_frames_round_trip_and_reject_other_text() -> None:
    frame = SensorFrame("sess-1", 4, 2_500, "gate-a")
    assert PROVISIONAL_PROTOCOL == "PSF/1"
    assert parse_sensor_line(format_sensor_frame(frame)) == frame
    assert parse_sensor_line("  # comment") is None
    assert parse_sensor_line("") is None
    with pytest.raises(SensorProtocolError) as error:
        parse_sensor_line("PSF/2 sess-1 1 0 gate-a")
    assert error.value.reason == "version"
    with pytest.raises(SensorProtocolError):
        parse_sensor_line("PSF/1 sess-1 0 10 gate-a")


def test_two_sensors_may_share_a_position_on_different_lanes() -> None:
    assert _issues(_pair()) == ()
    with pytest.raises(ValueError, match="duplicate physical"):
        TrackSensorMap(track_id=1, assignments=(_pair()[0], _assignment("gate-a", "sector_1", 1)))
    assert "error.timing_provider.sensor_physical_duplicate" in _issues(
        (_pair()[0], _assignment("gate-a", "sector_1", 1))
    )
    assert "error.timing_provider.sensor_role_duplicate" in _issues(
        (_pair()[0], _assignment("gate-c", "start_finish", 1))
    )
    assert "error.timing_provider.sensor_position_unknown" in _issues(
        (_pair()[0], _assignment("gate-c", "missing", 1))
    )
    assert "error.timing_provider.sensor_lane_invalid" in _issues(
        (_pair()[0], _assignment("gate-c", "sector_1", 3))
    )
    assert _issues((_assignment("gate-a", "sector_1", 1),)) == (
        "error.timing_provider.sensor_start_finish_missing",
    )
    eleven = tuple(_assignment(f"s{index}", "start_finish", index) for index in range(1, 12))
    assert _issues(eleven, lane_count=11) == ()
    assert _issues(eleven, lane_count=11, capacity=DeviceCapacity(10)) == (
        "error.timing_provider.sensor_capacity",
    )


def test_host_time_uses_only_the_explicit_mapping() -> None:
    first = DeviceClockMapping(offset_ns=1_000, numerator=2, denominator=1)
    assert to_host_ns(10, first) == 1_020
    truncated = DeviceClockMapping(offset_ns=5, numerator=1, denominator=3)
    assert to_host_ns(10, truncated) == 8
    with pytest.raises(ValueError, match="negative"):
        to_host_ns(10, DeviceClockMapping(offset_ns=-100))
    replaced = DeviceClockMapping(offset_ns=50)
    assert to_host_ns(10, replaced) == 60


def test_duplicates_gaps_and_restarts_stay_inside_the_sensor_module() -> None:
    assignments = {item.physical_id: item for item in _pair()}
    mapping = DeviceClockMapping(offset_ns=1_000)
    state = IntakeState(mapping=mapping, sync=SyncStatus.SYNCHRONIZED)
    state, first, event = consume_line(
        state,
        _frame("sess", 1, 10, "gate-a"),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert event is not None and event.timestamp_ns == 1_010
    assert event.sensor_id == "start_finish"
    assert event.position_id == "start_finish"
    assert event.lane == 1
    assert not hasattr(event, "sequence")
    assert first is not None and first.kind == "accepted"
    state, duplicate, dropped = consume_line(
        state,
        _frame("sess", 1, 10, "gate-a"),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert dropped is None
    assert duplicate is not None and duplicate.kind == "duplicate"
    state, gap, gap_event = consume_line(
        state,
        _frame("sess", 4, 40, "gate-b"),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert gap is not None and gap.kind == "gap" and gap.gap == 2
    assert gap_event is not None and gap_event.lane == 2 and gap_event.timestamp_ns == 1_040
    state, restart, blocked = consume_line(
        state,
        _frame("other", 1, 5, "gate-a"),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert blocked is None
    assert restart is not None and restart.kind == "restart"
    assert state.sync is SyncStatus.LOST and state.mapping is None
    state, waiting, still_blocked = consume_line(
        state,
        _frame("other", 2, 6, "gate-a"),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert still_blocked is None
    assert waiting is not None and waiting.kind == "unsynchronized"
    state = IntakeState(
        mapping=DeviceClockMapping(offset_ns=5_000),
        sync=SyncStatus.SYNCHRONIZED,
        session_id=state.session_id,
        last_sequence=state.last_sequence,
    )
    _state, resumed, resumed_event = consume_line(
        state,
        _frame("other", 3, 7, "gate-a"),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert resumed_event is not None and resumed_event.timestamp_ns == 5_007
    assert resumed is not None and resumed.kind == "accepted"


def test_simulator_and_another_transport_share_the_parser() -> None:
    lines = (_frame("sess", 1, 10, "gate-a"), _frame("sess", 2, 20, "gate-b"))
    assignments = {item.physical_id: item for item in _pair()}

    def run(link: SensorLink) -> tuple[tuple[tuple[str, int | None], ...], tuple[int, ...]]:
        link.acquire()
        link.set_time_mapping(DeviceClockMapping(100))
        link.bind_race(tuple(assignments.values()), _LOGICAL, frozenset({1, 2}))
        link.poll()
        events = link.take_events()
        summary = tuple((item.kind, item.host_time_ns) for item in link.observations)
        link.release()
        return summary, tuple(event.timestamp_ns for event in events)

    simulator = SensorLink(SimulatedSensorTransport())
    simulator.acquire()
    for line in lines:
        simulator.inject(line)
    simulated = run(simulator)
    buffered = run(SensorLink(BufferedSensorTransport(lines)))
    assert simulated == buffered
    assert simulated[1] == (110, 120)


def test_assignments_are_stored_per_track() -> None:
    store = SensorConfigurationStore(migrated_database())
    first, second = _pair()
    store.save_track(1, (first, second))
    store.save_track(2, (_assignment("gate-c", "start_finish", 1),))
    assert store.track_assignments(1) == (first, second)
    assert store.track_assignments(2)[0].physical_id == "gate-c"
    loaded = store.load()
    assert loaded == SensorConfiguration(
        tracks=(
            TrackSensorMap(track_id=1, assignments=(first, second)),
            TrackSensorMap(track_id=2, assignments=(_assignment("gate-c", "start_finish", 1),)),
        )
    )
    store.save_track(1, ())
    assert store.track_assignments(1) == ()
    assert store.track_assignments(2)[0].physical_id == "gate-c"


def test_the_provider_blocks_events_until_time_is_mapped_and_drops_duplicates() -> None:
    store = SensorConfigurationStore(migrated_database())
    store.save_track(1, _pair())
    link = SensorLink()
    factory = SensorTimingFactory(link, store, _Tracks())
    spec = TimingSessionSpec(default_timing_setup(), lanes=(1, 2), laps=3, track_id=TrackId(1))
    assert factory.availability().available is False
    assert factory.availability().reason_key == "error.timing_provider.sensor_time_unsynchronized"
    link.set_time_mapping(DeviceClockMapping(0))
    assert factory.availability().available is True
    received: list[SensorTriggered] = []
    source = factory.create_source(spec)
    assert isinstance(source, SensorTimingSource)
    source.start(received.append)
    link.inject(_frame("sess", 1, 8, "gate-a"))
    link.inject(_frame("sess", 1, 8, "gate-a"))
    link.inject(_frame("sess", 3, 30, "gate-b"))
    source.poll()
    assert [event.timestamp_ns for event in received] == [8, 30]
    assert [event.lane for event in received] == [1, 2]
    assert received[0].sensor_id == "start_finish"
    link.inject(_frame("next", 1, 1, "gate-a"))
    source.poll()
    assert len(received) == 2
    assert link.sync is SyncStatus.LOST
    assert factory.availability().reason_key == "error.timing_provider.sensor_time_lost"
    link.inject(_frame("next", 2, 4, "gate-a"))
    source.poll()
    assert len(received) == 2
    link.set_time_mapping(DeviceClockMapping(100))
    link.inject(_frame("next", 3, 5, "gate-a"))
    source.poll()
    assert received[-1].timestamp_ns == 105
    source.stop()
    assert source.is_running is False


def test_one_transport_stays_open_for_the_page_and_the_race() -> None:
    link = SensorLink()
    assert link.connected is False
    with pytest.raises(RuntimeError, match="not open"):
        link.inject(_frame("sess", 1, 1, "gate-a"))
    link.acquire()
    link.acquire()
    link.release()
    assert link.connected is True
    source = SensorTimingSource(link, _pair(), _LOGICAL, frozenset({1, 2}))
    received: list[SensorTriggered] = []
    source.start(received.append)
    assert link.connected is True
    link.set_time_mapping(DeviceClockMapping(0))
    link.inject(_frame("sess", 1, 9, "gate-a"))
    link.poll()
    assert received == []
    source.poll()
    assert [event.timestamp_ns for event in received] == [9]
    source.poll()
    assert len(received) == 1
    source.stop()
    assert link.connected is True
    link.release()
    assert link.connected is False


def test_the_diagnostic_page_shows_simulation_mapping_and_a_gap(
    qtbot: QtBot,
) -> None:
    store = SensorConfigurationStore(migrated_database())
    link = SensorLink()
    page = SensorTimingPage(_translator(), link, store, _Setups(), _Tracks())
    qtbot.addWidget(page)
    page.show()
    QApplication.processEvents()
    assert page.mode_label.text() == "Simulation"
    assert page.connection_label.text() == "Verbunden"
    assert page.readiness_label.text() == "Nicht messbereit"
    assert "PSF/1" in page.protocol_label.text()
    _choose(page, "sensor-position-s1", "start_finish")
    _choose(page, "sensor-lane-s1", 1)
    _choose(page, "sensor-position-s2", "start_finish")
    _choose(page, "sensor-lane-s2", 2)
    _click(page, "sensor-save")
    developer = page.findChild(QGroupBox, "sensor-developer")
    assert developer is not None and developer.isChecked() is False
    developer.setChecked(True)
    page.offset_edit.setText("1000")
    _click(page, "sensor-map-apply")
    QApplication.processEvents()
    assert page.readiness_label.text() == "Messbereit"
    page.line_edit.setText(_frame("sess", 1, 25, "s1"))
    _click(page, "sensor-inject")
    page.line_edit.setText(_frame("sess", 1, 25, "s1"))
    _click(page, "sensor-inject")
    page.line_edit.setText(_frame("sess", 4, 40, "s2"))
    _click(page, "sensor-inject")
    texts = [_cell(page, row, 5) for row in range(page.log.rowCount())]
    assert texts == ["Empfangen", "Duplikat", "Lücke (2)"]
    assert _cell(page, 0, 1) == "start_finish"
    assert _cell(page, 0, 4) == "1025"
    assert _cell(page, 2, 3) == "40"
    assert link.take_events() == ()
    _click(page, "sensor-map-clear")
    assert page.sync_label.text() == "Zeitzuordnung verloren"
    assert page.readiness_label.text() == "Nicht messbereit"
    start = next(
        position.id
        for position in default_timing_setup().layout.positions
        if position.type is TimingPositionType.START_FINISH
    )
    assert start == "start_finish"
    note = page.findChild(QLabel, "sensor-protocol")
    assert note is not None and "Nanosekunden" in note.text()
    assert page.baud_label.text() == "115200"
    assert page.port_combo.currentText() == ""


def _choose(page: SensorTimingPage, name: str, value: object) -> None:
    combo = page.findChild(QComboBox, name)
    assert combo is not None
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def _click(page: SensorTimingPage, name: str) -> None:
    button = page.findChild(QPushButton, name)
    assert button is not None
    button.click()


def _cell(page: SensorTimingPage, row: int, column: int) -> str:
    item = page.log.item(row, column)
    assert item is not None
    return item.text()
