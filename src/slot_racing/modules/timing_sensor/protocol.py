"""Provisional sensor frames.

``PSF/1`` is a versioned text frame for the simulator and for tests. It is not
the final USB protocol. A later transport must feed the same lines through
:func:`parse_sensor_line`; it must not invent a second parser.

A frame is one line::

    PSF/1 <session_id> <sequence> <device_time_ns> <physical_sensor_id>

``device_time_ns`` is the controller's clock at the moment the sensor fired,
not the time the line arrived on the host. ``sequence`` is the controller's
event number for that session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PROVISIONAL_PROTOCOL = "PSF/1"
PROVISIONAL_PROTOCOL_NOTE = "PSF/1 is a provisional text frame. It is not the final USB protocol."
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class SensorProtocolError(Exception):
    """One line is not a provisional sensor frame. The reason is a stable code."""

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


def is_sensor_token(value: str) -> bool:
    """A session id or a physical sensor id: one token, no spaces."""
    return _TOKEN.fullmatch(value) is not None


def format_sensor_frame(frame: SensorFrame) -> str:
    """The provisional line for ``frame``. The parser accepts this text."""
    if not isinstance(frame, SensorFrame):
        raise TypeError("frame must be a SensorFrame")
    return (
        f"{PROVISIONAL_PROTOCOL} {frame.session_id} {frame.sequence} "
        f"{frame.device_time_ns} {frame.physical_id}"
    )


def parse_sensor_line(line: str) -> SensorFrame | None:
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
    if parts[0] != PROVISIONAL_PROTOCOL:
        raise SensorProtocolError("version")
    if len(parts) != 5:
        raise SensorProtocolError("fields")
    _prefix, session_id, sequence_text, time_text, physical_id = parts
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


def _non_negative(text: str, reason: str) -> int:
    if text.startswith("+") or not text.isdigit():
        raise SensorProtocolError(reason)
    return int(text)
