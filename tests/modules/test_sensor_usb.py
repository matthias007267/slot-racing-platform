"""USB sensor transport, PSF/1 control lines, and clock exchange.

The bytes come from a scripted stream. No test opens a serial port.
"""

from __future__ import annotations

from collections import deque

import pytest

from slot_racing.core.events import SensorTriggered
from slot_racing.modules.timing_sensor.clock_map import DeviceClockMapping, SyncStatus
from slot_racing.modules.timing_sensor.clock_sync import (
    MIN_RATE_SPAN_NS,
    SyncSample,
    continuous_mapping,
    mapping_for,
)
from slot_racing.modules.timing_sensor.intake import IntakeState, consume_line
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.protocol import (
    BAUD_RATE,
    PROTOCOL,
    ErrorFrame,
    HelloFrame,
    SensorFrame,
    SensorProtocolError,
    StatusFrame,
    SyncCommand,
    SyncReply,
    format_error,
    format_hello,
    format_sensor_frame,
    format_status,
    format_sync_command,
    format_sync_reply,
    parse_sensor_line,
)
from slot_racing.modules.timing_sensor.transport import SerialSensorTransport
from tests.modules.test_sensor_timing import _LOGICAL, _assignment, _frame

_HELLO = HelloFrame("b0001", "mega2560", "1.0.0", "ns", ("s1", "s2"))


class _Clock:
    def __init__(self) -> None:
        self.now = 10_000_000_000

    def __call__(self) -> int:
        self.now += 1_000_000
        return self.now


class _Stream:
    def __init__(self) -> None:
        self.incoming: deque[bytes] = deque()
        self.outgoing = bytearray()
        self.closed = False
        self.fail_read = False
        self.device_ns = 5_000_000_000
        self.session = _HELLO.session_id

    def push(self, data: bytes) -> None:
        self.incoming.append(data)

    def read(self, size: int) -> bytes:
        if self.fail_read:
            raise OSError("unplugged")
        if size < 1 or not self.incoming:
            return b""
        found = bytearray()
        while self.incoming and len(found) < size:
            chunk = self.incoming[0]
            room = size - len(found)
            if len(chunk) <= room:
                found.extend(self.incoming.popleft())
            else:
                found.extend(chunk[:room])
                self.incoming[0] = chunk[room:]
        return bytes(found)

    def write(self, data: bytes) -> int:
        self.outgoing.extend(data)
        text = data.decode("ascii").strip()
        if text.startswith("PSF/1 SYNC "):
            seq = int(text.split()[2])
            rx = self.device_ns
            tx = rx + 100_000
            self.device_ns += 1_000_000
            self.incoming.append(f"PSF/1 SYNC {self.session} {seq} {rx} {tx}\n".encode("ascii"))
        return len(data)

    def close(self) -> None:
        self.closed = True


def _pair() -> tuple[object, object]:
    return (
        _assignment("s1", "start_finish", 1),
        _assignment("s2", "start_finish", 2),
    )


def _reader() -> tuple[SensorLink, SerialSensorTransport, _Stream]:
    stream = _Stream()
    transport = SerialSensorTransport(
        "COM7",
        stream=stream,
        clock=_Clock(),
        sync_gap_ns=0,
    )
    link = SensorLink(transport, mode=TransportMode.SERIAL)
    return link, transport, stream


def _identify(link: SensorLink, transport: SerialSensorTransport, stream: _Stream) -> None:
    stream.push(format_hello(_HELLO).encode("ascii") + b"\n")
    transport.pump()
    link.poll()


def _sync(link: SensorLink, transport: SerialSensorTransport) -> None:
    for _ in range(5):
        transport.pump()
    link.poll()


def test_protocol_keeps_the_event_line_and_adds_control_lines() -> None:
    event = SensorFrame("b0001", 4, 2_500, "s1")
    assert PROTOCOL == "PSF/1"
    assert BAUD_RATE == 115200
    assert parse_sensor_line(format_sensor_frame(event)) == event
    hello = parse_sensor_line(format_hello(_HELLO))
    assert hello == _HELLO
    status = parse_sensor_line(format_status(StatusFrame("b0001", "TIME")))
    assert isinstance(status, StatusFrame) and status.code == "TIME"
    error = parse_sensor_line(format_error(ErrorFrame("b0001", "BUFFER", 2)))
    assert isinstance(error, ErrorFrame) and error.detail == 2
    command = parse_sensor_line(format_sync_command(3))
    assert command == SyncCommand(3)
    reply = SyncReply("b0001", 3, 10, 12)
    assert parse_sensor_line(format_sync_reply(reply)) == reply
    assert parse_sensor_line("# debug") is None
    with pytest.raises(SensorProtocolError):
        parse_sensor_line("PSF/2 b0001 1 0 s1")


def test_clock_exchange_uses_both_directions_and_does_not_jump_published_time() -> None:
    samples = tuple(
        SyncSample(
            host_send_ns=1_000_000_000 + index * 1_000_000,
            host_recv_ns=1_000_000_000 + index * 1_000_000 + 2_000_000,
            device_rx_ns=500_000_000 + index * 1_000_000,
            device_tx_ns=500_000_000 + index * 1_000_000 + 100_000,
        )
        for index in range(4)
    )
    quality, mapping = mapping_for(samples)
    assert quality.accepted
    assert mapping is not None
    assert quality.numerator == 1 and quality.denominator == 1
    assert quality.uncertainty_ns < 2_000_000
    noisy = SyncSample(0, 100_000_000, 0, 1_000)
    quality, mapping = mapping_for((*samples, noisy))
    assert quality.samples == 4
    assert mapping is not None
    drifted = (
        SyncSample(0, 1_000_000, 0, 100_000),
        SyncSample(
            MIN_RATE_SPAN_NS,
            MIN_RATE_SPAN_NS + 1_000_000,
            MIN_RATE_SPAN_NS,
            MIN_RATE_SPAN_NS + 100_000,
        ),
        SyncSample(10_000_000, 11_000_000, 10_000_000, 10_100_000),
        SyncSample(20_000_000, 21_000_000, 20_000_000, 20_100_000),
    )
    _quality, rated = mapping_for(drifted)
    assert rated is not None and rated.numerator == rated.denominator
    wild = tuple(
        SyncSample(
            index * MIN_RATE_SPAN_NS,
            index * MIN_RATE_SPAN_NS + 1_000_000,
            index * MIN_RATE_SPAN_NS // 2,
            index * MIN_RATE_SPAN_NS // 2 + 100_000,
        )
        for index in range(1, 5)
    )
    _quality, rejected = mapping_for(wild)
    assert rejected is not None and rejected.numerator == 1
    anchored = continuous_mapping(
        DeviceClockMapping(offset_ns=50, numerator=1, denominator=1),
        last_device_ns=1_000,
        last_host_ns=2_000,
    )
    assert anchored is not None
    from slot_racing.modules.timing_sensor.clock_map import to_host_ns

    assert to_host_ns(1_000, anchored) >= 2_000
    assert to_host_ns(1_001, anchored) >= to_host_ns(1_000, anchored)


def test_partial_lines_discard_boot_noise_and_then_identify() -> None:
    link, transport, stream = _reader()
    link.acquire()
    hello = (format_hello(_HELLO) + "\n").encode("ascii")
    stream.push(b"\xffgarbage\n")
    stream.push(hello[:12])
    transport.pump()
    assert transport.connected is False
    assert transport.read_available() == ()
    stream.push(hello[12:])
    transport.pump()
    assert transport.connected is True
    assert transport.read_available() == (format_hello(_HELLO),)
    link.release()


def test_both_lanes_sync_then_a_drop_does_not_continue_the_race() -> None:
    link, transport, stream = _reader()
    first, second = _pair()
    assert isinstance(first, type(second))
    received: list[SensorTriggered] = []
    link.acquire()
    link.set_time_mapping(DeviceClockMapping(0))
    link.bind_race((first, second), _LOGICAL, frozenset({1, 2}))  # type: ignore[arg-type]
    _identify(link, transport, stream)
    _sync(link, transport)
    assert link.sync is SyncStatus.SYNCHRONIZED
    assert link.reported_sensor_count == 2
    identity = link.identity
    assert identity is not None and identity.sensors == ("s1", "s2")
    stream.push((_frame("b0001", 1, 8_000_000_000, "s1") + "\n").encode("ascii"))
    stream.push((_frame("b0001", 2, 8_000_020_000, "s2") + "\n").encode("ascii"))
    transport.pump()
    transport.pump()
    link.poll()
    received.extend(link.take_events())
    assert [event.lane for event in received] == [1, 2]
    assert [event.position_id for event in received] == ["start_finish", "start_finish"]
    hosts = [item.host_time_ns for item in link.observations if item.kind == "accepted"]
    devices = [item.device_time_ns for item in link.observations if item.kind == "accepted"]
    assert hosts[0] is not None and devices[0] is not None
    assert hosts[1] is not None and devices[1] is not None
    assert hosts[0] - devices[0] == hosts[1] - devices[1]
    assert hosts[0] != devices[0]
    stream.fail_read = True
    transport.pump()
    link.poll()
    status_after_drop: SyncStatus = link.sync
    assert status_after_drop is SyncStatus.LOST
    assert any(item.kind == "disconnect" for item in link.observations)
    stream.fail_read = False
    stream.push((_frame("b0001", 3, 8_000_040_000, "s1") + "\n").encode("ascii"))
    transport.pump()
    link.poll()
    assert link.take_events() == ()
    link.release()


def test_buffer_gap_rollover_and_lost_clock() -> None:
    assignments = {
        "s1": _assignment("s1", "start_finish", 1),
        "s2": _assignment("s2", "start_finish", 2),
    }
    state = IntakeState(
        mapping=DeviceClockMapping(0), sync=SyncStatus.SYNCHRONIZED, session_id="b0001"
    )
    state, buffer, event = consume_line(
        state,
        format_error(ErrorFrame("b0001", "BUFFER", 2)),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert event is None
    assert buffer is not None and buffer.kind == "buffer" and buffer.detail == "2"
    state, gap, gap_event = consume_line(
        state,
        _frame("b0001", 4, 40, "s2"),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert gap is not None and gap.kind == "gap" and gap.gap == 3
    assert gap_event is not None and gap_event.lane == 2
    state, rollover, still = consume_line(
        state,
        format_status(StatusFrame("b0001", "TIME")),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert rollover is not None and rollover.kind == "rollover"
    assert still is None and state.sync is SyncStatus.SYNCHRONIZED
    state, fault, blocked = consume_line(
        state,
        format_error(ErrorFrame("b0001", "TIME", 0)),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert fault is not None and fault.kind == "time_fault"
    assert blocked is None and state.sync is SyncStatus.LOST
    _state, waiting, still_blocked = consume_line(
        state,
        _frame("b0001", 5, 50, "s1"),
        assignments=assignments,
        logical_ids=_LOGICAL,
        active_lanes=frozenset({1, 2}),
        source_id="sensor",
    )
    assert still_blocked is None
    assert waiting is not None and waiting.kind == "unsynchronized"


def test_a_new_session_mutes_the_race_until_an_explicit_mapping() -> None:
    link, transport, stream = _reader()
    link.acquire()
    link.bind_race(
        (_assignment("s1", "start_finish", 1), _assignment("s2", "start_finish", 2)),
        _LOGICAL,
        frozenset({1, 2}),
    )
    _identify(link, transport, stream)
    _sync(link, transport)
    stream.push((_frame("b0001", 1, 8_000_000_000, "s1") + "\n").encode("ascii"))
    transport.pump()
    link.poll()
    assert len(link.take_events()) == 1
    restarted = HelloFrame("b0002", "mega2560", "1.0.0", "ns", ("s1", "s2"))
    stream.session = "b0002"
    stream.push((format_hello(restarted) + "\n").encode("ascii"))
    transport.pump()
    link.poll()
    _sync(link, transport)
    assert link.sync is SyncStatus.SYNCHRONIZED
    stream.push((_frame("b0002", 1, 100, "s2") + "\n").encode("ascii"))
    transport.pump()
    link.poll()
    assert link.take_events() == ()
    assert any(item.kind == "restart" for item in link.observations)
    withheld = [item for item in link.observations if item.kind == "withheld"]
    assert len(withheld) == 1
    assert withheld[0].detail == "restart"
    assert withheld[0].physical_id == "s2"
    assert withheld[0].position_id == "start_finish"
    assert withheld[0].lane == 2
    link.set_time_mapping(DeviceClockMapping(5))
    stream.push((_frame("b0002", 2, 200, "s2") + "\n").encode("ascii"))
    transport.pump()
    link.poll()
    resumed = link.take_events()
    assert len(resumed) == 1 and resumed[0].lane == 2 and resumed[0].timestamp_ns == 205
    link.release()


def test_a_running_race_keeps_its_measured_clock_until_an_event_anchors_it() -> None:
    clock = _Clock()
    stream = _Stream()
    transport = SerialSensorTransport("COM7", stream=stream, clock=clock, sync_gap_ns=0)
    link = SensorLink(transport, mode=TransportMode.SERIAL)
    link.acquire()
    _identify(link, transport, stream)
    _sync(link, transport)
    installed = link._state.mapping
    assert installed is not None
    assert link.mapping_origin == "measured"
    link.bind_race(
        (_assignment("s1", "start_finish", 1), _assignment("s2", "start_finish", 2)),
        _LOGICAL,
        frozenset({1, 2}),
    )
    link._estimator.clear()
    clock.now += 50_000_000_000_000
    link.request_sync(5)
    _sync(link, transport)
    assert link._state.mapping == installed
    assert link.mapping_origin == "measured"
    link.release()


def test_a_manual_serial_mapping_does_not_stay_the_race_clock() -> None:
    link, transport, stream = _reader()
    link.acquire()
    link.set_time_mapping(DeviceClockMapping(0))
    assert link.mapping_origin == "manual"
    link.bind_race(
        (_assignment("s1", "start_finish", 1), _assignment("s2", "start_finish", 2)),
        _LOGICAL,
        frozenset({1, 2}),
    )
    _identify(link, transport, stream)
    _sync(link, transport)
    mapping = link._state.mapping
    assert link.mapping_origin == "measured"
    assert mapping is not None and mapping.offset_ns != 0
    link.release()


def test_an_empty_port_name_is_refused_and_com5_is_not_implied() -> None:
    with pytest.raises(ValueError, match="empty"):
        SerialSensorTransport("   ")
    transport = SerialSensorTransport("COM11", stream=_Stream(), threaded=False)
    assert transport.port == "COM11"
