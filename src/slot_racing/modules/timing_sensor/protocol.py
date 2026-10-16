"""Sensor frames on the wire.

``PSF/1`` is the text protocol shared by the simulator, the USB reader and the
Mega firmware. A transport yields these lines to :func:`parse_sensor_line`. It
does not grow a second parser.

Every line is one message and ends at the newline. ``#`` comments and blank
lines are host-side only; the firmware must not write them. There is no
separate debug stream.

Event::

    PSF/1 <session_id> <sequence> <device_time_ns> <physical_sensor_id>

``device_time_ns`` is the controller clock at the accepted rising edge, in
nanoseconds. It is not the time the line reached the host. The unit is the
data unit. It is not a promise that the measurement is accurate to one
nanosecond. ``sequence`` starts at 1 for each session and increases by one for
every accepted edge, including an edge that the device buffer could not keep.

Identity, sent once after reset and before any event::

    PSF/1 HELLO <session_id> <device> <firmware> <time_unit> <count> <sensor_id>...

``time_unit`` is ``ns``. ``count`` is the number of sensor ids that follow.

Status and errors::

    PSF/1 STATUS <session_id> <code>
    PSF/1 ERROR <session_id> <code> <detail>

Status codes are ``WAIT``, ``READY`` and ``TIME`` (the 32-bit microsecond
counter was extended; timestamps stay monotonic). Error codes are ``BUFFER``
(detail is how many accepted edges were dropped), ``TIME`` (the clock was
lost) and ``PROTOCOL``.

Clock exchange. The host writes::

    PSF/1 SYNC <host_seq>

and remembers its monotonic send time. The device answers, with the controller
time at which the request line completed and the time just before the reply::

    PSF/1 SYNC <session_id> <host_seq> <device_rx_ns> <device_tx_ns>

The host stamps its receive time when that reply line completes. Offset and
rate come only from these four timestamps. A crossing never uses the host
receive time.

Session. A new ``session_id`` means the controller restarted. The host drops
the clock mapping and does not treat later events as a continuation of the
previous race. ``sequence`` going backwards does the same. The 64-bit
nanosecond timestamp does not wrap when the microcontroller's 32-bit
microsecond counter does; that extension is announced with ``STATUS TIME``.
A lost extension is ``ERROR TIME`` and ends the session's clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PROTOCOL = "PSF/1"
PROVISIONAL_PROTOCOL = PROTOCOL
PROVISIONAL_PROTOCOL_NOTE = (
    "PSF/1 is the sensor text protocol: events, identity, status, errors and clock sync. "
    "device_time_ns is nanoseconds on the controller clock, not an accuracy claim."
)
BAUD_RATE = 115200
TIME_UNIT = "ns"
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_RESERVED = frozenset({"HELLO", "STATUS", "ERROR", "SYNC"})
_STATUS_CODES = frozenset({"WAIT", "READY", "TIME"})
_ERROR_CODES = frozenset({"BUFFER", "TIME", "PROTOCOL"})
_MAX_SENSORS = 6


class SensorProtocolError(Exception):
    """One line is not a PSF/1 message. The reason is a stable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SensorFrame:
    """One captured sensor event, still on the device clock."""

    session_id: str
    sequence: int
    device_time_ns: int
    physical_id: str


@dataclass(frozen=True, slots=True)
class HelloFrame:
    """Identity announced by the controller at the start of a session."""

    session_id: str
    device: str
    firmware: str
    time_unit: str
    sensors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StatusFrame:
    """A controller status that is not a crossing."""

    session_id: str
    code: str


@dataclass(frozen=True, slots=True)
class ErrorFrame:
    """A controller fault. ``detail`` is a non-negative integer."""

    session_id: str
    code: str
    detail: int


@dataclass(frozen=True, slots=True)
class SyncCommand:
    """Host request. ``host_seq`` pairs the reply with the host send time."""

    host_seq: int


@dataclass(frozen=True, slots=True)
class SyncReply:
    """Device side of one clock exchange."""

    session_id: str
    host_seq: int
    device_rx_ns: int
    device_tx_ns: int


type SensorMessage = SensorFrame | HelloFrame | StatusFrame | ErrorFrame | SyncCommand | SyncReply


def is_sensor_token(value: str) -> bool:
    """A session id or a physical sensor id: one token, no spaces."""
    return _TOKEN.fullmatch(value) is not None


def format_sensor_frame(frame: SensorFrame) -> str:
    """The event line for ``frame``. The parser accepts this text."""
    if not isinstance(frame, SensorFrame):
        raise TypeError("frame must be a SensorFrame")
    return (
        f"{PROTOCOL} {frame.session_id} {frame.sequence} {frame.device_time_ns} {frame.physical_id}"
    )


def format_hello(frame: HelloFrame) -> str:
    """The identity line. ``time_unit`` is ``ns`` and the sensor count matches."""
    if not isinstance(frame, HelloFrame):
        raise TypeError("frame must be a HelloFrame")
    sensors = " ".join(frame.sensors)
    return (
        f"{PROTOCOL} HELLO {frame.session_id} {frame.device} {frame.firmware} "
        f"{frame.time_unit} {len(frame.sensors)} {sensors}"
    )


def format_status(frame: StatusFrame) -> str:
    """One status line."""
    if not isinstance(frame, StatusFrame):
        raise TypeError("frame must be a StatusFrame")
    return f"{PROTOCOL} STATUS {frame.session_id} {frame.code}"


def format_error(frame: ErrorFrame) -> str:
    """One error line."""
    if not isinstance(frame, ErrorFrame):
        raise TypeError("frame must be an ErrorFrame")
    return f"{PROTOCOL} ERROR {frame.session_id} {frame.code} {frame.detail}"


def format_sync_command(host_seq: int) -> str:
    """The host's clock request. The device does not need the host's clock."""
    _require_seq(host_seq)
    return f"{PROTOCOL} SYNC {host_seq}"


def format_sync_reply(frame: SyncReply) -> str:
    """The device's clock reply."""
    if not isinstance(frame, SyncReply):
        raise TypeError("frame must be a SyncReply")
    return (
        f"{PROTOCOL} SYNC {frame.session_id} {frame.host_seq} "
        f"{frame.device_rx_ns} {frame.device_tx_ns}"
    )


def parse_sensor_line(line: str) -> SensorMessage | None:
    """Parse one line.

    Blank lines and ``#`` comments are ignored (``None``). Anything else that
    is not ``PSF/1`` raises :class:`SensorProtocolError`.
    """
    if not isinstance(line, str):
        raise SensorProtocolError("type")
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    parts = text.split()
    if parts[0] != PROTOCOL:
        raise SensorProtocolError("version")
    if len(parts) == 3 and parts[1] == "SYNC":
        return SyncCommand(_require_seq(_non_negative(parts[2], "sequence")))
    if len(parts) == 4 and parts[1] == "STATUS":
        return _status(parts)
    if len(parts) == 5 and parts[1] == "ERROR":
        return _error(parts)
    if len(parts) == 5:
        return _event(parts)
    if len(parts) == 6 and parts[1] == "SYNC":
        return _sync_reply(parts)
    if len(parts) >= 7 and parts[1] == "HELLO":
        return _hello(parts)
    raise SensorProtocolError("fields")


def _event(parts: list[str]) -> SensorFrame:
    _prefix, session_id, sequence_text, time_text, physical_id = parts
    if session_id in _RESERVED:
        raise SensorProtocolError("session")
    if not is_sensor_token(session_id):
        raise SensorProtocolError("session")
    if not is_sensor_token(physical_id):
        raise SensorProtocolError("sensor")
    sequence = _non_negative(sequence_text, "sequence")
    if sequence < 1:
        raise SensorProtocolError("sequence")
    return SensorFrame(
        session_id=session_id,
        sequence=sequence,
        device_time_ns=_non_negative(time_text, "time"),
        physical_id=physical_id,
    )


def _hello(parts: list[str]) -> HelloFrame:
    _prefix, _verb, session_id, device, firmware, time_unit, count_text, *sensors = parts
    _token(session_id, "session")
    _token(device, "device")
    _token(firmware, "firmware")
    if time_unit != TIME_UNIT:
        raise SensorProtocolError("unit")
    count = _non_negative(count_text, "count")
    if count < 1 or count > _MAX_SENSORS or count != len(sensors):
        raise SensorProtocolError("count")
    for sensor in sensors:
        _token(sensor, "sensor")
    if session_id in _RESERVED or device in _RESERVED:
        raise SensorProtocolError("session")
    return HelloFrame(session_id, device, firmware, time_unit, tuple(sensors))


def _status(parts: list[str]) -> StatusFrame:
    _prefix, _verb, session_id, code = parts
    _token(session_id, "session")
    if code not in _STATUS_CODES:
        raise SensorProtocolError("status")
    return StatusFrame(session_id, code)


def _error(parts: list[str]) -> ErrorFrame:
    _prefix, _verb, session_id, code, detail = parts
    _token(session_id, "session")
    if code not in _ERROR_CODES:
        raise SensorProtocolError("error")
    return ErrorFrame(session_id, code, _non_negative(detail, "detail"))


def _sync_reply(parts: list[str]) -> SyncReply:
    _prefix, _verb, session_id, host_seq, device_rx, device_tx = parts
    _token(session_id, "session")
    return SyncReply(
        session_id,
        _require_seq(_non_negative(host_seq, "sequence")),
        _non_negative(device_rx, "time"),
        _non_negative(device_tx, "time"),
    )


def _token(value: str, reason: str) -> None:
    if value in _RESERVED or not is_sensor_token(value):
        raise SensorProtocolError(reason)


def _require_seq(value: int) -> int:
    if value < 1:
        raise SensorProtocolError("sequence")
    return value


def _non_negative(text: str, reason: str) -> int:
    if text.startswith("+") or not text.isdigit():
        raise SensorProtocolError(reason)
    return int(text)
